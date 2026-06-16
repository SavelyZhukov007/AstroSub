# -*- coding: utf-8 -*-
"""Minimal desktop API: video subtitles plus EmotionAI frame analysis."""
from __future__ import annotations

import json
import re
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import webview

from . import config
from .core import device, emotion, install, media, project, server, transcribe


def _esc(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)


def friendly_error(exc) -> str:
    text = str(exc or "")
    low = text.lower()
    if any(x in low for x in ("cublas", "cudnn", "cublas64", "cudnn64")):
        return (
            "CUDA-библиотеки не загружены. Submind переключит распознавание на CPU; "
            "для GPU установите CUDA runtime/cuBLAS/cuDNN версии, совместимой с faster-whisper."
        )
    if "ffmpeg" in low or "failed to open" in low:
        return "Не удалось прочитать аудио/видео через ffmpeg. Проверьте файл или установку ffmpeg."
    if "no module named" in low:
        match = re.search(r"no module named ['\"]([^'\"]+)", text, re.IGNORECASE)
        missing = match.group(1).split(".", 1)[0] if match else ""
        if missing and missing in getattr(sys, "stdlib_module_names", set()):
            return "Сборка Submind неполная: отсутствует стандартный модуль Python " + missing
        return "Не установлен нужный Python-пакет: " + text
    return text or "Неизвестная ошибка"


class Progress:
    def __init__(self, api, steps):
        self.api = api
        self.steps = steps
        self.frac = {k: 0.0 for k, _, _ in steps}
        self.total_w = sum(w for _, _, w in steps) or 1
        self.start = time.time()
        self.label = ""

    def update(self, key, frac, label=None):
        self.frac[key] = max(0.0, min(1.0, frac))
        if label:
            self.label = label
        self.emit()

    def done(self, key):
        self.frac[key] = 1.0
        self.emit()

    def overall(self):
        return sum(self.frac[k] * w for k, _, w in self.steps) / self.total_w

    def emit(self):
        ov = self.overall()
        elapsed = time.time() - self.start
        eta = (elapsed * (1 - ov) / ov) if ov > 0.02 else 0
        self.api._emit("process:progress", {
            "progress": ov,
            "label": self.label,
            "eta": int(eta),
            "elapsed": int(elapsed),
        })


class Api:
    def __init__(self):
        self.settings = config.load_settings()
        self.project = None
        self._transcriber = None
        self._emotion = None
        self._server = server.MediaServer()
        try:
            self._server.set_data_root(config.user_data_dir())
        except Exception:
            pass
        self._pol = device.policy(self.settings)

    def _window(self):
        return webview.windows[0] if webview.windows else None

    def _emit(self, event, payload):
        w = self._window()
        if w:
            try:
                w.evaluate_js(
                    f"window.SubmindBus && window.SubmindBus.emit({_esc(event)}, "
                    f"{json.dumps(payload, ensure_ascii=False)})"
                )
            except Exception:
                pass

    def _bg(self, fn, *a, **k):
        threading.Thread(target=self._guard, args=(fn, a, k), daemon=True).start()

    def _guard(self, fn, a, k):
        try:
            fn(*a, **k)
        except Exception as e:  # noqa: BLE001
            self._emit("error", {
                "message": friendly_error(e),
                "raw": str(e),
                "trace": traceback.format_exc(),
            })

    def _heartbeat_call(self, prog, key, frac, label, fn, interval=1.0):
        box = {}

        def runner():
            try:
                box["result"] = fn()
            except Exception as exc:  # noqa: BLE001
                box["error"] = exc

        t = threading.Thread(target=runner, daemon=True)
        t.start()
        start = time.time()
        while t.is_alive():
            elapsed = int(time.time() - start)
            prog.update(key, frac, f"{label} ({elapsed}s)")
            time.sleep(interval)
        if "error" in box:
            raise box["error"]
        return box.get("result")

    def get_settings(self):
        return self.settings

    def update_settings(self, patch):
        config.save_settings(patch or {})
        self.settings = config.load_settings()
        self._pol = device.policy(self.settings)
        self._transcriber = None
        self._emotion = None
        return self.settings

    def environment(self):
        build_id = config.current_build_id()
        saved_build_id = self.settings.get("build_id")
        first_run_done = bool(
            self.settings.get("first_run_done", False)
            and (build_id == "source" or saved_build_id == build_id)
        )
        if not first_run_done:
            device.detect(force=True)
        return {
            "ffmpeg": media.has_ffmpeg(),
            "device": device.summary(),
            "emotion": emotion.dependency_status(),
            "packages": install.check(),
            "first_run_done": first_run_done,
            "build_id": build_id,
            "server_base": self._safe_base(),
        }

    def _safe_base(self):
        try:
            return self._server.base_url()
        except Exception:
            return ""

    def install_packages(self, keys):
        def prog(payload):
            if not isinstance(payload, dict):
                payload = {"progress": 0, "text": str(payload)}
            self._emit("install:progress", payload)

        self._emit("install:start", {})

        def run():
            res = install.install(keys or [], on_progress=prog)
            try:
                device.detect(force=True)
                self._pol = device.policy(self.settings)
            except Exception:
                pass
            self._emit("install:done", res)

        self._bg(run)
        return {"ok": True}

    def runtime_health(self):
        from .core import runtime
        return runtime.health_check()

    def keep_runtime_archive(self, keep=True):
        p = config.runtime_archive_path()
        if not keep and p.exists():
            try:
                p.unlink()
            except Exception:
                return {"ok": False, "path": str(p)}
        return {"ok": True, "path": str(p), "exists": p.exists()}

    def finish_first_run(self):
        config.save_settings({
            "first_run_done": True,
            "build_id": config.current_build_id(),
        })
        self.settings = config.load_settings()
        return True

    def pick_video(self):
        w = self._window()
        types = ("Видео (*.mp4;*.mkv;*.mov;*.avi;*.webm)", "Все файлы (*.*)")
        res = w.create_file_dialog(webview.OPEN_DIALOG, allow_multiple=False, file_types=types)
        if not res:
            return None
        path = res[0]
        self.project = project.new_project(path)
        self.project["duration"] = media.probe_duration(path)
        project.save(self.project)
        return {
            "path": path,
            "title": self.project["title"],
            "duration": self.project["duration"],
            "id": self.project["id"],
        }

    def media_uri(self, path):
        try:
            return self._server.serve(path)
        except Exception:
            return Path(path).as_uri()

    def process(self, _options=None):
        if not self.project:
            return {"ok": False, "error": "Нет открытого видео"}
        self._bg(self._do_process)
        return {"ok": True}

    def _do_process(self):
        p = self.project
        steps = [("subtitles", "Распознавание речи", 5)]
        prog = Progress(self, steps)
        self._emit("process:start", {"steps": [{"key": k, "label": l} for k, l, _ in steps]})
        with ThreadPoolExecutor(max_workers=max(1, self._pol["cpu_workers"])) as pool:
            pool.submit(self._step_subtitles, prog).result()
        project.save(p)
        self._emit("process:done", {
            "segments": p["segments"],
            "language": p["language"],
            "duration": p["duration"],
        })

    def _step_subtitles(self, prog):
        p = self.project
        prog.update("subtitles", 0.02, "Извлечение аудио")
        audio = self._heartbeat_call(
            prog,
            "subtitles",
            0.035,
            "Извлечение аудио",
            lambda: media.extract_audio(p["video_path"]),
        )
        prog.update("subtitles", 0.07, "Загрузка модели Whisper")
        tr = self._get_transcriber()
        self._heartbeat_call(
            prog,
            "subtitles",
            0.075,
            "Загрузка Whisper; первый запуск может скачать модель",
            tr.load,
        )
        prog.update("subtitles", 0.11, "Распознавание речи")
        res = tr.transcribe(
            audio,
            language=self.settings["language"],
            translate=False,
            on_progress=lambda f, t: prog.update("subtitles", 0.11 + (f * 0.89), t),
            total_duration=p["duration"],
        )
        p["segments"] = res["segments"]
        p["language"] = res["language"]
        p["duration"] = res["duration"] or p["duration"]
        prog.done("subtitles")
        self._emit("subtitles:ready", {"segments": p["segments"]})

    def _get_transcriber(self):
        if self._transcriber is None:
            dev = self.settings["whisper_device"]
            if dev == "auto":
                dev = self._pol["heavy_device"]
            self._transcriber = transcribe.Transcriber(
                model_size=self.settings["whisper_model"],
                device=dev,
                compute_type=self.settings["whisper_compute"],
            )
        return self._transcriber

    def emotion_status(self):
        return emotion.dependency_status()

    def emotion_analyze_frame(self, data_url, device_name="CPU", threshold=0.55):
        try:
            if self._emotion is None:
                self._emotion = emotion.EmotionEngine()
            return self._emotion.analyze_data_url(
                data_url,
                device=str(device_name or "CPU"),
                threshold=float(threshold or 0.55),
            )
        except ModuleNotFoundError as exc:
            return {"ok": False, "error": f"Не установлен компонент EmotionAI: {exc.name}"}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": "EmotionAI не обработал кадр: " + str(exc)}
