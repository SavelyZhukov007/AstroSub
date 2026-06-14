# -*- coding: utf-8 -*-
"""
API-мост: методы класса доступны из JS как window.pywebview.api.*
Тяжёлые операции уходят в фоновые потоки и шлют события в UI.
"""
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
from .core import (device, diarize, emotion, export, facedb, faceid, faces,
                   install, lan, llm, media, project, server, transcribe)


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
    if "ollama" in low:
        return "Ollama/Qwen не ответил: " + text
    return text or "Неизвестная ошибка"


class Progress:
    """Сводный прогресс по нескольким шагам + оценка оставшегося времени."""

    def __init__(self, api, steps):
        self.api = api
        self.steps = steps              # [(key, label, weight)]
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
            "progress": ov, "label": self.label,
            "eta": int(eta), "elapsed": int(elapsed),
        })


class Api:
    def __init__(self):
        self.settings = config.load_settings()
        self.project = None
        self._transcriber = None
        self._faces = None
        self._faceid = None
        self._emotion = None
        self._llm = None
        self._server = server.MediaServer()
        self._lan = lan.LanServer(
            web_root=config.web_dir(),
            on_event=lambda e, p: self._emit(e, p),
            on_complete=self._process_lan_job,
        )
        try:
            self._lan.start()
        except Exception:
            pass
        try:
            self._server.set_data_root(config.user_data_dir())
        except Exception:
            pass
        self._pol = device.policy(self.settings)

    # ------------------------------------------------------------------ #
    #  События / потоки
    # ------------------------------------------------------------------ #
    def _window(self):
        return webview.windows[0] if webview.windows else None

    def _emit(self, event, payload):
        w = self._window()
        if w:
            try:
                w.evaluate_js(f"window.SubmindBus && window.SubmindBus.emit({_esc(event)}, {json.dumps(payload, ensure_ascii=False)})")
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

    def _warning(self, message, **extra):
        payload = {"message": message}
        payload.update(extra)
        self._emit("process:warning", payload)

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

    # ------------------------------------------------------------------ #
    #  Окружение / устройство / установка
    # ------------------------------------------------------------------ #
    def get_settings(self):
        return self.settings

    def update_settings(self, patch):
        config.save_settings(patch or {})
        self.settings = config.load_settings()
        self._pol = device.policy(self.settings)
        self._transcriber = None
        self._faces = None
        self._faceid = None
        self._emotion = None
        self._llm = None
        return self.settings

    def device_info(self):
        return device.summary()

    def environment(self):
        cli = self._get_llm()
        ok_ollama = cli.available()
        build_id = config.current_build_id()
        saved_build_id = self.settings.get("build_id")
        first_run_done = bool(
            self.settings.get("first_run_done", False)
            and (build_id == "source" or saved_build_id == build_id)
        )
        if not first_run_done:
            device.detect(force=True)
        dev = device.summary()
        packages = install.check()
        return {
            "ffmpeg": media.has_ffmpeg(),
            "ollama": ok_ollama,
            "ollama_models": cli.list_models() if ok_ollama else [],
            "qwen": cli.resolve_model() if ok_ollama else "",
            "device": dev,
            "packages": packages,
            "first_run_done": first_run_done,
            "build_id": build_id,
            "server_base": self._safe_base(),
            "lan": self.lan_info(),
        }

    def _safe_base(self):
        try:
            return self._server.base_url()
        except Exception:
            return ""

    def local_url(self, path):
        try:
            return self._server.local_url(path)
        except Exception:
            return ""

    def install_packages(self, keys):
        def prog(payload):
            if not isinstance(payload, dict):
                payload = {"progress": 0, "text": str(payload)}
            self._emit("install:progress", payload)
        self._emit("install:start", {})
        def run():
            res = install.install(keys or [], on_progress=prog,
                                  gpu=self._pol.get("has_cuda", False))
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

    # ------------------------------------------------------------------ #
    #  LAN / устройства
    # ------------------------------------------------------------------ #
    def lan_info(self):
        try:
            return self._lan.pairing_payload()
        except Exception:
            return {"urls": [], "url": "", "pair_url": "", "token": "", "qr_svg": ""}

    def lan_devices(self):
        return self._lan.devices.all()

    def lan_jobs(self):
        return list(self._lan.jobs.all().values())

    def lan_trust_device(self, device_id, trusted=True):
        return {"ok": self._lan.trust_device(device_id, trusted)}

    def lan_approve_job(self, job_id, approved=True):
        job = self._lan.approve_job(job_id, approved)
        return {"ok": bool(job), "job": job}

    def _process_lan_job(self, job):
        try:
            self._lan.jobs.save({**job, "status": "processing", "message": "Обработка на Submind"})
            self.project = project.new_project(job["video_path"])
            self.project["duration"] = media.probe_duration(job["video_path"])
            self._do_process(job.get("options") or {"subtitles": True})
            saved = project.save(self.project)
            done = dict(job)
            done.update({
                "status": "done",
                "message": "Готово",
                "project_id": self.project["id"],
                "project_path": saved,
            })
            self._lan.jobs.save(done)
            self._emit("lan:job_done", {"job": done})
        except Exception as e:  # noqa: BLE001
            failed = dict(job)
            failed.update({"status": "failed", "message": str(e)})
            self._lan.jobs.save(failed)
            self._emit("lan:job_failed", {"job": failed})

    def finish_first_run(self):
        config.save_settings({
            "first_run_done": True,
            "build_id": config.current_build_id(),
        })
        self.settings = config.load_settings()
        return True

    # ------------------------------------------------------------------ #
    #  Модели Ollama
    # ------------------------------------------------------------------ #
    def _get_llm(self):
        if self._llm is None:
            self._llm = llm.OllamaClient(
                host=self.settings.get("ollama_host", "http://127.0.0.1:11434"),
                model=self.settings.get("ollama_model", ""),
                default_model=self.settings.get("default_model", "qwen2.5:7b-instruct"),
            )
        return self._llm

    def _llm_ready(self):
        cli = self._get_llm()
        return cli if cli.available() else None

    def list_models(self):
        cli = self._get_llm()
        return {"models": cli.list_models(), "current": cli.resolve_model(),
                "default": self.settings.get("default_model")}

    def set_model(self, name):
        self.update_settings({"ollama_model": name or ""})
        return self._get_llm().resolve_model()

    def pull_model(self, name):
        def prog(f, t):
            self._emit("pull:progress", {"progress": f, "text": t, "model": name})
        self._emit("pull:start", {"model": name})
        def run():
            res = self._get_llm().pull(name, on_progress=prog)
            self._emit("pull:done", res)
        self._bg(run)
        return {"ok": True}

    # ------------------------------------------------------------------ #
    #  Файлы / проекты / медиа
    # ------------------------------------------------------------------ #
    def pick_video(self):
        w = self._window()
        types = ("Видео и аудио (*.mp4;*.mkv;*.mov;*.avi;*.webm;*.mp3;*.wav;*.m4a)",
                 "Все файлы (*.*)")
        res = w.create_file_dialog(webview.OPEN_DIALOG, allow_multiple=False, file_types=types)
        if not res:
            return None
        path = res[0]
        self.project = project.new_project(path)
        self.project["duration"] = media.probe_duration(path)
        project.save(self.project)
        return {"path": path, "title": self.project["title"],
                "duration": self.project["duration"], "id": self.project["id"]}

    def list_projects(self):
        return project.list_projects()

    def open_project(self, pid):
        self.project = project.load(pid)
        return self.project

    def save_project(self):
        return project.save(self.project) if self.project else None

    def delete_project(self, pid):
        return project.delete(pid)

    def media_uri(self, path):
        """Отдаём через локальный http-сервер с Range — иначе чёрный экран."""
        try:
            return self._server.serve(path)
        except Exception:
            return Path(path).as_uri()

    # ------------------------------------------------------------------ #
    #  Единый конвейер обработки (модальное окно выбора задач)
    # ------------------------------------------------------------------ #
    def process(self, options):
        if not self.project:
            return {"ok": False, "error": "Нет открытого видео"}
        self._bg(self._do_process, options or {})
        return {"ok": True}

    def _do_process(self, opt):
        p = self.project
        steps = []
        if opt.get("subtitles"):
            steps.append(("subtitles", "Распознавание речи", 5))
        if opt.get("speakers"):
            steps.append(("speakers", "Идентификация спикеров", 4))
        if opt.get("summary"):
            steps.append(("summary", "Конспект", 2))
        if opt.get("glossary"):
            steps.append(("glossary", "Глоссарий", 1))
        if opt.get("chapters"):
            steps.append(("chapters", "Главы", 1))
        if not steps:
            steps = [("subtitles", "Распознавание речи", 5)]
            opt["subtitles"] = True

        prog = Progress(self, steps)
        self._emit("process:start", {"steps": [{"key": k, "label": l} for k, l, _ in steps]})

        # Тяжёлое (ASR) на GPU, лица параллельно на CPU
        with ThreadPoolExecutor(max_workers=max(2, self._pol["cpu_workers"])) as pool:
            futs = {}
            if opt.get("subtitles"):
                futs["subtitles"] = pool.submit(self._step_subtitles, prog, opt.get("translate", False))
            if opt.get("speakers"):
                futs["speakers"] = pool.submit(self._step_speakers, prog)
            for f in list(futs.values()):
                f.result()

        # привязка спикеров к репликам, если оба шага есть
        if p.get("segments") and p.get("face_timeline"):
            p["segments"] = diarize.assign_speakers(p["segments"], p["face_timeline"])

        # текстовые задачи (нужна расшифровка) — последовательно
        if p.get("segments"):
            text = " ".join(s["text"] for s in p["segments"])
            if opt.get("summary"):
                try:
                    self._step_summary(prog, text)
                except Exception as exc:  # noqa: BLE001
                    prog.done("summary")
                    self._warning("Конспект пропущен: " + friendly_error(exc), step="summary")
            if opt.get("glossary"):
                try:
                    self._step_glossary(prog, text)
                except Exception as exc:  # noqa: BLE001
                    prog.done("glossary")
                    self._warning("Глоссарий пропущен: " + friendly_error(exc), step="glossary")
            if opt.get("chapters"):
                try:
                    self._step_chapters(prog)
                except Exception as exc:  # noqa: BLE001
                    prog.done("chapters")
                    self._warning("Главы пропущены: " + friendly_error(exc), step="chapters")

        project.save(p)
        self._emit("process:done", {
            "segments": p["segments"], "persons": p["persons"],
            "summary": p["summary"], "glossary": p["glossary"],
            "chapters": p["chapters"], "language": p["language"],
            "duration": p["duration"],
        })

    def _step_subtitles(self, prog, translate):
        p = self.project
        prog.update("subtitles", 0.02, "Извлечение аудио")
        audio = self._heartbeat_call(
            prog, "subtitles", 0.035, "Извлечение аудио",
            lambda: media.extract_audio(p["video_path"]),
        )
        prog.update("subtitles", 0.07, "Загрузка модели Whisper")
        tr = self._get_transcriber()
        self._heartbeat_call(
            prog, "subtitles", 0.075,
            "Загрузка Whisper; первый запуск может скачать модель",
            tr.load,
        )
        prog.update("subtitles", 0.11, "Распознавание речи")
        res = tr.transcribe(
            audio, language=self.settings["language"], translate=translate,
            on_progress=lambda f, t: prog.update("subtitles", 0.11 + (f * 0.89), t),
            total_duration=p["duration"])
        p["segments"] = res["segments"]
        p["language"] = res["language"]
        p["duration"] = res["duration"] or p["duration"]
        prog.done("subtitles")
        self._emit("subtitles:ready", {"segments": p["segments"]})

    def _step_speakers(self, prog):
        p = self.project
        try:
            eng = self._get_faces()
            res = eng.analyze(p["video_path"],
                              on_progress=lambda f, t: prog.update("speakers", f, t))
        except Exception as exc:  # noqa: BLE001
            self._faces = None
            p["persons"] = []
            p["face_timeline"] = []
            prog.done("speakers")
            self._warning("Лица/спикеры пропущены: " + friendly_error(exc), step="speakers")
            self._emit("speakers:ready", {"persons": []})
            return
        p["persons"] = res["persons"]
        p["face_timeline"] = res["timeline"]
        prog.done("speakers")
        self._emit("speakers:ready", {"persons": p["persons"]})

    def _step_summary(self, prog, text):
        p = self.project
        prog.update("summary", 0.1, "Конспект")
        cli = self._llm_ready()
        if not cli:
            p["summary"] = ""
            self._emit("summary:done", {"summary": ""})
            prog.done("summary")
            return
        acc = []
        cli.generate(
            llm.prompt_summary(text, "тезисы"), system=llm.SYS_RU,
            on_token=lambda t: (acc.append(t), self._emit("summary:token", {"token": t})))
        p["summary"] = "".join(acc)
        prog.done("summary")
        self._emit("summary:done", {"summary": p["summary"]})

    def _step_glossary(self, prog, text):
        p = self.project
        prog.update("glossary", 0.2, "Глоссарий")
        cli = self._llm_ready()
        if not cli:
            p["glossary"] = []
            self._emit("glossary:done", {"glossary": []})
            prog.done("glossary")
            return
        raw = cli.generate(llm.prompt_glossary(text), system=llm.SYS_RU)
        p["glossary"] = self._safe_json(raw, [])
        prog.done("glossary")
        self._emit("glossary:done", {"glossary": p["glossary"]})

    def _step_chapters(self, prog):
        p = self.project
        prog.update("chapters", 0.2, "Главы")
        cli = self._llm_ready()
        if not cli:
            p["chapters"] = []
            self._emit("chapters:done", {"chapters": []})
            prog.done("chapters")
            return
        brief = "\n".join(f"[{int(s['start'])}] {s['text']}" for s in p["segments"])
        raw = cli.generate(llm.prompt_chapters(brief), system=llm.SYS_RU)
        p["chapters"] = self._safe_json(raw, [])
        prog.done("chapters")
        self._emit("chapters:done", {"chapters": p["chapters"]})

    # ------------------------------------------------------------------ #
    #  Отдельные действия (повторный запуск из вкладок)
    # ------------------------------------------------------------------ #
    def get_segments(self):
        return self.project["segments"] if self.project else []

    def _get_transcriber(self):
        if self._transcriber is None:
            dev = self.settings["whisper_device"]
            if dev == "auto":
                dev = self._pol["heavy_device"]
            self._transcriber = transcribe.Transcriber(
                model_size=self.settings["whisper_model"], device=dev,
                compute_type=self.settings["whisper_compute"])
        return self._transcriber

    def _get_faces(self):
        if self._faces is None:
            self._faces = faces.FaceEngine(
                sample_fps=self.settings["face_sample_fps"],
                cluster_threshold=self.settings["face_cluster_threshold"],
                match_threshold=self.settings["face_match_threshold"],
                remember=self.settings.get("remember_faces", True),
                device=self._pol["light_device"])
        return self._faces

    def rename_person(self, person_id, label):
        if not self.project:
            return False
        for per in self.project["persons"]:
            if per["id"] == person_id:
                per["label"] = label
                if per.get("uid"):
                    facedb.rename(per["uid"], label)
        project.save(self.project)
        return True

    def face_landmarks(self, ts):
        if not self.project:
            return []
        return self._get_faces().landmarks_468(self.project["video_path"], float(ts))

    # ------------------------------------------------------------------ #
    #  Объяснение выделения / вопрос
    # ------------------------------------------------------------------ #
    def explain(self, selection, context):
        self._bg(self._do_explain, selection, context)
        return {"ok": True}

    def _do_explain(self, selection, context):
        cli = self._get_llm()
        if not cli.available():
            self._emit("explain:error", {"message": "Ollama не запущен"})
            return
        self._emit("explain:start", {"selection": selection})
        cli.generate(llm.prompt_explain(selection, context), system=llm.SYS_RU,
                     on_token=lambda t: self._emit("explain:token", {"token": t}))
        self._emit("explain:done", {})

    def make_summary(self, style="тезисы"):
        self._bg(self._do_summary, style)
        return {"ok": True}

    def _do_summary(self, style):
        p = self.project
        text = " ".join(s["text"] for s in p["segments"])
        self._emit("summary:start", {})
        cli = self._llm_ready()
        if not cli:
            self._emit("summary:done", {"summary": ""})
            return
        acc = []
        cli.generate(llm.prompt_summary(text, style), system=llm.SYS_RU,
                     on_token=lambda t: (acc.append(t), self._emit("summary:token", {"token": t})))
        p["summary"] = "".join(acc)
        project.save(p)
        self._emit("summary:done", {"summary": p["summary"]})

    def make_glossary(self):
        self._bg(self._do_glossary)
        return {"ok": True}

    def _do_glossary(self):
        p = self.project
        text = " ".join(s["text"] for s in p["segments"])
        self._emit("glossary:start", {})
        cli = self._llm_ready()
        if not cli:
            self._emit("glossary:done", {"glossary": []})
            return
        raw = cli.generate(llm.prompt_glossary(text), system=llm.SYS_RU)
        p["glossary"] = self._safe_json(raw, [])
        project.save(p)
        self._emit("glossary:done", {"glossary": p["glossary"]})

    def make_chapters(self):
        self._bg(self._do_chapters)
        return {"ok": True}

    def _do_chapters(self):
        p = self.project
        self._emit("chapters:start", {})
        cli = self._llm_ready()
        if not cli:
            self._emit("chapters:done", {"chapters": []})
            return
        brief = "\n".join(f"[{int(s['start'])}] {s['text']}" for s in p["segments"])
        raw = cli.generate(llm.prompt_chapters(brief), system=llm.SYS_RU)
        p["chapters"] = self._safe_json(raw, [])
        project.save(p)
        self._emit("chapters:done", {"chapters": p["chapters"]})

    def ask(self, question):
        self._bg(self._do_ask, question)
        return {"ok": True}

    def _do_ask(self, question):
        p = self.project
        text = " ".join(s["text"] for s in p["segments"])
        self._emit("ask:start", {"question": question})
        cli = self._llm_ready()
        if not cli:
            self._emit("ask:token", {"token": "Ollama не запущен."})
            self._emit("ask:done", {})
            return
        cli.generate(llm.prompt_qa(question, text), system=llm.SYS_RU,
                     on_token=lambda t: self._emit("ask:token", {"token": t}))
        self._emit("ask:done", {})

    # ------------------------------------------------------------------ #
    #  Чат с Qwen (со ссылкой на видео)
    # ------------------------------------------------------------------ #
    def chat_send(self, history, attach_project=None):
        self._bg(self._do_chat, history or [], attach_project)
        return {"ok": True}

    def _do_chat(self, history, attach_project):
        cli = self._get_llm()
        if not cli.available():
            self._emit("chat:error", {"message": "Ollama не запущен"})
            return
        system = llm.SYS_CHAT
        msgs = list(history)
        if attach_project:
            try:
                proj = project.load(attach_project)
                ctx = " ".join(s["text"] for s in proj.get("segments", []))[:8000]
                if ctx and msgs:
                    msgs[-1] = dict(msgs[-1])
                    msgs[-1]["content"] = (
                        f"[Контекст видео «{proj['title']}»]:\n{ctx}\n\n"
                        f"Вопрос: {msgs[-1]['content']}")
            except Exception:
                pass
        self._emit("chat:start", {})
        cli.chat(msgs, system=system,
                 on_token=lambda t: self._emit("chat:token", {"token": t}))
        self._emit("chat:done", {})

    def transcribe_voice(self, b64):
        """Голосовое сообщение -> текст (для отправки в чат)."""
        self._bg(self._do_voice, b64)
        return {"ok": True}

    def transcribe_voice_live(self, b64, seq=0, final=False):
        self._bg(self._do_voice_live, b64, int(seq or 0), bool(final))
        return {"ok": True}

    def _do_voice_live(self, b64, seq, final):
        try:
            path = media.save_b64_audio(b64, ".webm")
            text = self._get_transcriber().quick(path, self.settings["language"])
            event = "voice:done" if final else "voice:partial"
            self._emit(event, {"text": text, "seq": seq, "final": final})
        except Exception as exc:  # noqa: BLE001
            self._emit("voice:error", {"message": friendly_error(exc), "seq": seq, "final": final})

    def _do_voice(self, b64):
        self._emit("voice:start", {})
        try:
            path = media.save_b64_audio(b64, ".webm")
            text = self._get_transcriber().quick(path, self.settings["language"])
            self._emit("voice:done", {"text": text})
        except Exception as exc:  # noqa: BLE001
            self._emit("voice:error", {"message": friendly_error(exc), "final": True})

    # ------------------------------------------------------------------ #
    #  Сервис FaceID
    # ------------------------------------------------------------------ #
    def _get_faceid(self):
        if self._faceid is None:
            self._faceid = faceid.FaceIDService(device=self._pol["heavy_device"])
        return self._faceid

    def faceid_enroll(self, frames, label=""):
        self._bg(self._do_faceid_enroll, frames or [], label)
        return {"ok": True}

    def _do_faceid_enroll(self, frames, label):
        self._emit("faceid:start", {"count": len(frames)})
        try:
            svc = self._get_faceid()
            res = svc.enroll(frames, label=label,
                             on_progress=lambda f, t: self._emit("faceid:progress", {"progress": f, "text": t}))
            if res.get("ok") and self.settings.get("remember_faces", True):
                facedb.remember(res["uid"], res["label"], res["embedding"], res.get("thumb"))
        except ModuleNotFoundError as e:
            res = {"ok": False, "error": f"Не установлен пакет для FaceID: {e.name}"}
        except Exception as e:  # noqa: BLE001
            res = {"ok": False, "error": "FaceID не смог обработать профиль: " + str(e)}
        self._emit("faceid:done", res)

    def faceid_list(self):
        return faceid.FaceIDService.list_enrollments()

    def faceid_delete(self, uid):
        return faceid.FaceIDService.delete(uid)

    def faceid_analyze(self, uid):
        self._bg(self._do_faceid_analyze, uid)
        return {"ok": True}

    def _do_faceid_analyze(self, uid):
        try:
            data = faceid.FaceIDService.load(uid)
        except Exception:
            self._emit("faceid:analysis_error", {"message": "Профиль FaceID не найден"})
            return
        metrics = json.dumps({
            "label": data.get("label"), "пол": data.get("gender"),
            "возраст": data.get("age"), "кадров": data.get("frames_total"),
            "качество": data.get("quality"),
            "точек_106": len(data["frames"][0]["landmarks_106"]) if data.get("frames") else 0,
            "точек_468": len(data["frames"][0]["landmarks_468"]) if data.get("frames") else 0,
        }, ensure_ascii=False)
        cli = self._get_llm()
        if not cli.available():
            self._emit("faceid:analysis_error", {"message": "Ollama не запущен"})
            return
        self._emit("faceid:analysis_start", {"uid": uid})
        try:
            cli.generate(llm.prompt_face(metrics), system=llm.SYS_FACE,
                         on_token=lambda t: self._emit("faceid:analysis_token", {"token": t}))
            self._emit("faceid:analysis_done", {"uid": uid})
        except Exception as e:  # noqa: BLE001
            self._emit("faceid:analysis_error", {"message": "Qwen не ответил: " + str(e)})

    # ------------------------------------------------------------------ #
    #  EmotionAI
    # ------------------------------------------------------------------ #
    def emotion_status(self):
        return emotion.dependency_status()

    def emotion_analyze_frame(self, data_url, device_name="CPU", threshold=0.55):
        try:
            if self._emotion is None:
                self._emotion = emotion.EmotionEngine()
            return self._emotion.analyze_data_url(
                data_url, device=str(device_name or "CPU"), threshold=float(threshold or 0.55)
            )
        except ModuleNotFoundError as exc:
            return {"ok": False, "error": f"Не установлен компонент EmotionAI: {exc.name}"}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": "EmotionAI не обработал кадр: " + str(exc)}

    # ------------------------------------------------------------------ #
    #  Галерея известных лиц
    # ------------------------------------------------------------------ #
    def known_faces(self):
        return facedb.all_people()

    def rename_known(self, uid, label):
        return facedb.rename(uid, label)

    def forget_known(self, uid):
        return facedb.forget(uid)

    def face_compare(self, uid_a, uid_b):
        import numpy as np
        try:
            a = faceid.FaceIDService.load(uid_a)["embedding"]
            b = faceid.FaceIDService.load(uid_b)["embedding"]
        except Exception:
            return {"ok": False, "error": "Профиль не найден"}
        va = np.asarray(a); vb = np.asarray(b)
        sim = float(np.dot(va, vb) / ((np.linalg.norm(va) * np.linalg.norm(vb)) + 1e-9))
        return {"ok": True, "similarity": round(sim, 3),
                "same": sim >= 0.5}

    # ------------------------------------------------------------------ #
    #  Карточки для запоминания (доп. сервис)
    # ------------------------------------------------------------------ #
    def make_flashcards(self):
        self._bg(self._do_flashcards)
        return {"ok": True}

    def _do_flashcards(self):
        p = self.project
        text = " ".join(s["text"] for s in p.get("segments", []))
        if not text:
            self._emit("flashcards:done", {"cards": []})
            return
        self._emit("flashcards:start", {})
        cli = self._llm_ready()
        if not cli:
            self._emit("flashcards:done", {"cards": []})
            return
        prompt = ("Составь 6-10 карточек вопрос-ответ по содержанию. Верни СТРОГО "
                  "JSON-массив объектов {\"q\":..., \"a\":...}.\n\n" + text)
        raw = cli.generate(prompt, system=llm.SYS_RU)
        self._emit("flashcards:done", {"cards": self._safe_json(raw, [])})

    # ------------------------------------------------------------------ #
    @staticmethod
    def _safe_json(raw, fallback):
        raw = (raw or "").strip()
        if raw.startswith("```"):
            raw = raw.strip("`")
            raw = raw[raw.find("\n") + 1:] if "\n" in raw else raw
        start = raw.find("[")
        end = raw.rfind("]")
        if start != -1 and end != -1:
            try:
                return json.loads(raw[start:end + 1])
            except Exception:
                pass
        return fallback

    # ------------------------------------------------------------------ #
    #  Заметки / закладки / поиск / экспорт
    # ------------------------------------------------------------------ #
    def add_note(self, t, text):
        self.project["notes"].append({"t": float(t), "text": text})
        project.save(self.project)
        return self.project["notes"]

    def add_bookmark(self, t, label):
        self.project["bookmarks"].append({"t": float(t), "label": label})
        project.save(self.project)
        return self.project["bookmarks"]

    def search(self, query):
        q = (query or "").lower().strip()
        if not q or not self.project:
            return []
        return [{"t": s["start"], "text": s["text"]}
                for s in self.project["segments"] if q in s["text"].lower()]

    def export_as(self, fmt):
        if not self.project or fmt not in export.EXPORTERS:
            return {"ok": False}
        p = self.project
        ext, func = export.EXPORTERS[fmt]
        speakers = {per["id"]: per["label"] for per in p.get("persons", [])}
        if fmt == "md":
            content = export.to_markdown(p["title"], p["segments"], p.get("summary", ""),
                                         p.get("glossary"), p.get("chapters"), speakers)
        elif fmt == "txt":
            content = export.to_txt(p["segments"], speakers)
        else:
            content = func(p["segments"])
        w = self._window()
        save_path = w.create_file_dialog(webview.SAVE_DIALOG, save_filename=f"{p['title']}.{ext}")
        if not save_path:
            return {"ok": False}
        Path(save_path).write_text(content, encoding="utf-8")
        return {"ok": True, "path": save_path}
