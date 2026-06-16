# -*- coding: utf-8 -*-
"""Managed runtime installer for optional ML dependencies.

The desktop shell stays small. Heavy Python wheels live in an app-managed
runtime directory and are installed by uv with no visible terminal windows.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import os
import platform
import queue
import shutil
import subprocess
import sys
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional
from urllib.request import urlretrieve

from .. import config

PYTHON_VERSION = "3.10"
UV_INSTALL_PS1 = "https://astral.sh/uv/install.ps1"
UV_INSTALL_SH = "https://astral.sh/uv/install.sh"

FEATURES = {
    "asr": {
        "title": "Распознавание речи",
        "desc": "Субтитры из видео (faster-whisper).",
        "packages": ["faster-whisper>=1.2,<2"],
        "modules": ["faster_whisper"],
    },
    "video": {
        "title": "Обработка кадров",
        "desc": "Чтение видео и превью (OpenCV).",
        "packages": ["opencv-python>=4.9"],
        "modules": ["cv2"],
    },
    "emotion": {
        "title": "EmotionAI",
        "desc": "Распознавание лиц и эмоций в реальном времени (OpenVINO).",
        "packages": ["openvino>=2024,<2027"],
        "modules": ["openvino"],
    },
}


@dataclass
class RuntimeStats:
    start: float
    last_time: float
    last_size: int


class RuntimeInstaller:
    def __init__(self, on_progress: Optional[Callable[[dict], None]] = None):
        self.on_progress = on_progress
        self.runtime = config.runtime_dir()
        self.venv = self.runtime / ".venv"
        self.tools = self.runtime / "tools"
        self.log_path = config.logs_dir() / f"runtime-install-{time.strftime('%Y%m%d-%H%M%S')}.log"
        self.stats = RuntimeStats(time.time(), time.time(), self._runtime_size())

    # ------------------------------------------------------------------ #
    # public
    # ------------------------------------------------------------------ #
    def install(self, keys: list[str]) -> dict:
        self.runtime.mkdir(parents=True, exist_ok=True)
        self.tools.mkdir(parents=True, exist_ok=True)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

        ordered = packages_for(keys)
        if not ordered:
            self._progress(1.0, "done", "Нечего устанавливать")
            return self._result(True, [], [])

        archive = config.runtime_archive_path()
        if archive.exists():
            self._progress(0.03, "archive", f"Найден offline-архив {archive.name}")
            ok, error = self._restore_archive(archive)
            if ok:
                config.bootstrap_runtime_packages()
                health = health_check(keys)
                return self._result(health["ok"], ordered if health["ok"] else [], health.get("failed", []), health)
            self._log(f"Archive restore failed: {error}")

        uv = self._ensure_uv()
        self._ensure_python(uv)
        self._ensure_venv(uv)

        installed, failed = [], []
        total = len(ordered)
        for i, package in enumerate(ordered):
            base = 0.24 + (i / max(1, total)) * 0.58
            self._progress(base, "install", f"Установка {package}", package=package)
            code, output = self._run([
                str(uv), "pip", "install",
                "--python", str(config.runtime_python()),
                "--upgrade", package,
            ], timeout=3600)
            if code == 0:
                installed.append(package)
            else:
                failed.append({"package": package, "error": short_error(output)})

        config.bootstrap_runtime_packages()
        health = health_check(keys)
        if not health["ok"]:
            failed.extend(health["failed"])

        if not failed:
            self._progress(0.91, "archive", "Создание offline-архива runtime")
            try:
                self.create_archive(archive)
            except Exception as e:  # noqa: BLE001
                self._log(f"Archive create failed: {e}")

        ok = not failed
        self._progress(1.0, "done" if ok else "failed", "Готово" if ok else "Готово с ошибками")
        return self._result(ok, installed, failed, health)

    def create_archive(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        if tmp.exists():
            tmp.unlink()
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
            for p in self.runtime.rglob("*"):
                if p == tmp or p == path or not p.is_file():
                    continue
                zf.write(p, p.relative_to(self.runtime))
        tmp.replace(path)

    # ------------------------------------------------------------------ #
    # setup
    # ------------------------------------------------------------------ #
    def _ensure_uv(self) -> Path:
        found = shutil.which("uv")
        if found:
            return Path(found)

        local = self.tools / ("uv.exe" if os.name == "nt" else "uv")
        if local.exists():
            return local

        self._progress(0.07, "uv", "Установка uv")
        if os.name == "nt":
            env = {"UV_INSTALL_DIR": str(self.tools)}
            cmd = [
                "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-Command", f"$env:UV_INSTALL_DIR='{self.tools}'; irm {UV_INSTALL_PS1} | iex",
            ]
            code, output = self._run(cmd, timeout=300, extra_env=env)
        else:
            env = {"UV_INSTALL_DIR": str(self.tools)}
            cmd = ["sh", "-c", f"curl -LsSf {UV_INSTALL_SH} | sh"]
            code, output = self._run(cmd, timeout=300, extra_env=env)

        if code != 0 or not local.exists():
            if not getattr(sys, "frozen", False):
                code, output = self._run([sys.executable, "-m", "pip", "install", "--upgrade", "uv"], timeout=300)
                found = shutil.which("uv")
                if code == 0 and found:
                    return Path(found)
            raise RuntimeError("Не удалось установить uv: " + short_error(output))
        return local

    def _ensure_python(self, uv: Path) -> None:
        py = config.runtime_python()
        if py.exists():
            return
        self._progress(0.12, "python", f"Установка Python {PYTHON_VERSION}")
        code, output = self._run([str(uv), "python", "install", PYTHON_VERSION], timeout=1800)
        if code != 0:
            raise RuntimeError("Не удалось установить Python: " + short_error(output))

    def _ensure_venv(self, uv: Path) -> None:
        py = config.runtime_python()
        if py.exists():
            return
        self._progress(0.18, "venv", "Создание runtime-окружения")
        code, output = self._run([str(uv), "venv", "--python", PYTHON_VERSION, str(self.venv)], timeout=600)
        if code != 0:
            raise RuntimeError("Не удалось создать runtime: " + short_error(output))

    def _restore_archive(self, archive: Path) -> tuple[bool, str]:
        try:
            if self.runtime.exists():
                for child in self.runtime.iterdir():
                    if child.name == "tools":
                        continue
                    if child.is_dir():
                        shutil.rmtree(child, ignore_errors=True)
                    else:
                        child.unlink(missing_ok=True)
            with zipfile.ZipFile(archive, "r") as zf:
                zf.extractall(self.runtime)
            self._progress(0.9, "archive", "Runtime восстановлен из offline-архива")
            return True, ""
        except Exception as e:  # noqa: BLE001
            return False, str(e)

    # ------------------------------------------------------------------ #
    # process helpers
    # ------------------------------------------------------------------ #
    def _run(self, cmd: list[str], timeout: int, extra_env: Optional[dict] = None) -> tuple[int, str]:
        self._log("$ " + " ".join(cmd))
        env = os.environ.copy()
        env.update(extra_env or {})
        flags = 0
        startupinfo = None
        if os.name == "nt":
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            creationflags=flags,
            startupinfo=startupinfo,
        )

        lines = []
        deadline = time.time() + timeout
        q: queue.Queue[str | None] = queue.Queue()

        def reader():
            try:
                assert proc.stdout is not None
                for line in proc.stdout:
                    q.put(line)
            finally:
                q.put(None)

        import threading
        threading.Thread(target=reader, daemon=True).start()
        timed_out = False
        while proc.poll() is None:
            try:
                while True:
                    line = q.get_nowait()
                    if line is None:
                        break
                    lines.append(line)
                    self._log(line.rstrip())
                    self._progress(None, "running", line.strip()[:140])
            except queue.Empty:
                pass
            if time.time() > deadline:
                proc.kill()
                lines.append("timeout")
                timed_out = True
                break
            self._progress(None, "running", "Идёт установка...")
            time.sleep(0.5)

        try:
            while True:
                line = q.get_nowait()
                if line is None:
                    break
                lines.append(line)
                self._log(line.rstrip())
        except queue.Empty:
            pass
        if timed_out:
            return 124, "".join(lines)
        return int(proc.returncode or 0), "".join(lines)

    def _runtime_size(self) -> int:
        if not self.runtime.exists():
            return 0
        total = 0
        for p in self.runtime.rglob("*"):
            if p.is_file():
                try:
                    total += p.stat().st_size
                except OSError:
                    pass
        return total

    def _progress(self, progress: Optional[float], stage: str, text: str, **extra) -> None:
        if not self.on_progress:
            return
        now = time.time()
        size = self._runtime_size()
        dt = max(0.001, now - self.stats.last_time)
        disk_bps = max(0.0, (size - self.stats.last_size) / dt)
        self.stats.last_time = now
        self.stats.last_size = size
        payload = {
            "progress": progress,
            "stage": stage,
            "text": text,
            "elapsed": int(now - self.stats.start),
            "eta": estimate_eta(progress, now - self.stats.start),
            "disk_bps": int(disk_bps),
            "runtime_bytes": size,
            "log_path": str(self.log_path),
        }
        payload.update(extra)
        self.on_progress(payload)

    def _log(self, text: str) -> None:
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(text + "\n")

    def _result(self, ok: bool, installed: list, failed: list, health: Optional[dict] = None) -> dict:
        return {
            "ok": ok,
            "installed": installed,
            "failed": failed,
            "health": health or health_check(),
            "target": str(self.runtime),
            "python": str(config.runtime_python()),
            "archive": str(config.runtime_archive_path()),
            "archive_exists": config.runtime_archive_path().exists(),
            "log_path": str(self.log_path),
        }


def packages_for(keys: Iterable[str]) -> list[str]:
    selected = list(keys or [])
    packages = []
    for key in selected:
        if key in FEATURES:
            packages.extend(FEATURES[key]["packages"])
    seen, out = set(), []
    for pkg in packages:
        if pkg not in seen:
            seen.add(pkg)
            out.append(pkg)
    return out


def health_check(keys: Optional[Iterable[str]] = None) -> dict:
    config.bootstrap_runtime_packages()
    failed = []
    modules = []
    if keys:
        for key in keys:
            modules.extend(FEATURES.get(key, {}).get("modules", []))
    else:
        modules = ["faster_whisper", "cv2", "openvino"]
    modules = list(dict.fromkeys(modules))
    for name in modules:
        try:
            importlib.import_module(name)
        except Exception as e:  # noqa: BLE001
            failed.append({"module": name, "error": str(e)})
    return {
        "ok": not failed,
        "failed": failed,
        "onnx_providers": [],
        "cuda": False,
    }


def check_features() -> list:
    config.bootstrap_runtime_packages()
    out = []
    for key, meta in FEATURES.items():
        installed = all(module_available(m) for m in meta["modules"])
        out.append({
            "key": key,
            "title": meta["title"],
            "desc": meta["desc"],
            "packages": meta["packages"],
            "installed": installed,
            "recommended": not installed,
            "available": True,
        })
    return out


def module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except Exception:
        return False


def estimate_eta(progress: Optional[float], elapsed: float) -> int:
    if not progress or progress <= 0.02:
        return 0
    return int(max(0, elapsed * (1 - progress) / progress))


def short_error(text: str) -> str:
    lines = [l.strip() for l in (text or "").splitlines() if l.strip()]
    return lines[-1] if lines else "неизвестная ошибка"
