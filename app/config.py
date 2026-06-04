# -*- coding: utf-8 -*-
"""Конфигурация, пути приложения и постоянные настройки."""
import json
import os
import sys
from pathlib import Path


def app_root() -> Path:
    """Корень ресурсов: работает и из исходников, и из onefile-сборки PyInstaller."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


def web_dir() -> Path:
    base = app_root()
    cand = base / "web"
    return cand if cand.exists() else base


def user_data_dir() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home()))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    d = base / "Submind"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _sub(name: str) -> Path:
    d = user_data_dir() / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def projects_dir() -> Path:
    return _sub("projects")


def faceid_dir() -> Path:
    return _sub("faceid")


def cache_dir() -> Path:
    return _sub("cache")


def packages_dir() -> Path:
    return _sub("packages")


def bootstrap_runtime_packages() -> Path:
    """Подключает пакеты, скачанные мастером первого запуска."""
    d = packages_dir()
    candidates = [
        d,
        d / "Lib" / "site-packages",
    ]
    for p in reversed(candidates):
        if p.exists():
            s = str(p)
            if s not in sys.path:
                sys.path.insert(0, s)

    if os.name == "nt":
        dll_dirs = [
            d,
            d / "onnxruntime" / "capi",
            d / "torch" / "lib",
            d / "nvidia" / "cublas" / "bin",
            d / "nvidia" / "cudnn" / "bin",
        ]
        path_parts = []
        for p in dll_dirs:
            if p.exists():
                path_parts.append(str(p))
                if hasattr(os, "add_dll_directory"):
                    try:
                        os.add_dll_directory(str(p))
                    except OSError:
                        pass
        if path_parts:
            os.environ["PATH"] = os.pathsep.join(path_parts + [os.environ.get("PATH", "")])
    return d


def current_build_id() -> str:
    meta = app_root() / "build-info.json"
    if meta.exists():
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
            return str(data.get("build_id") or "")
        except Exception:
            pass
    return "source"


DEVICE_PATH = user_data_dir() / "device.json"
FACEDB_PATH = user_data_dir() / "facedb.json"
CHATS_PATH = user_data_dir() / "chats.json"


DEFAULTS = {
    "whisper_model": "small",          # tiny|base|small|medium|large-v3
    "whisper_device": "auto",          # auto|cpu|cuda
    "whisper_compute": "auto",         # auto|int8|float16|float32
    "language": "auto",
    "ollama_host": "http://127.0.0.1:11434",
    "ollama_model": "",                # пусто => автоопределение qwen
    "default_model": "qwen2.5:7b-instruct",
    "faces_enabled": True,
    "face_sample_fps": 0.5,
    "face_cluster_threshold": 0.55,
    "face_match_threshold": 0.42,      # порог узнавания лица из галереи (косинус)
    "remember_faces": True,            # запоминать спикеров между сессиями
    "gpu_policy": "auto",              # auto|gpu|cpu — тяжёлые задачи на GPU
    "max_cpu_workers": 0,              # 0 => авто (cpu_count-1)
    "theme": "studio-dark",
    "translate_to": "",
    "split_ratio": 0.56,               # доля ширины под видео-колонку
    "first_run_done": False,
}

_SETTINGS_PATH = user_data_dir() / "settings.json"


def load_settings() -> dict:
    data = dict(DEFAULTS)
    if _SETTINGS_PATH.exists():
        try:
            data.update(json.loads(_SETTINGS_PATH.read_text(encoding="utf-8")))
        except Exception:
            pass
    return data


def save_settings(data: dict) -> None:
    merged = load_settings()
    merged.update(data or {})
    _SETTINGS_PATH.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
