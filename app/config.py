# -*- coding: utf-8 -*-
"""Конфигурация, пути приложения и постоянные настройки."""
import json
import os
import platform
import sys
from pathlib import Path


def app_root() -> Path:
    """Корень ресурсов: рядом с exe в сборке, либо корень исходников."""
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        if (exe_dir / "web").exists() or (exe_dir / "models").exists():
            return exe_dir
        return Path(getattr(sys, "_MEIPASS", exe_dir))
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


def cache_dir() -> Path:
    return _sub("cache")


def packages_dir() -> Path:
    d = executable_dir() / "packages"
    d.mkdir(parents=True, exist_ok=True)
    return d


def runtime_dir() -> Path:
    d = executable_dir() / "runtime"
    d.mkdir(parents=True, exist_ok=True)
    return d


def logs_dir() -> Path:
    return _sub("logs")


def device_id_path() -> Path:
    return user_data_dir() / "device-id.txt"


def device_id() -> str:
    p = device_id_path()
    if p.exists():
        try:
            value = p.read_text(encoding="utf-8").strip()
            if value:
                return value
        except Exception:
            pass
    import uuid
    value = uuid.uuid4().hex
    p.write_text(value, encoding="utf-8")
    return value


def runtime_archive_name() -> str:
    system = platform.system().lower() or "unknown"
    machine = platform.machine().lower().replace("amd64", "x86_64")
    return f"submind-runtime-{system}-{machine}.zip"


def executable_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return app_root()


def runtime_archive_path() -> Path:
    return executable_dir() / runtime_archive_name()


def runtime_python() -> Path:
    base = runtime_dir() / ".venv"
    if os.name == "nt":
        return base / "Scripts" / "python.exe"
    return base / "bin" / "python"


def _runtime_site_packages() -> list[Path]:
    base = runtime_dir() / ".venv"
    candidates = [
        base / "Lib" / "site-packages",
    ]
    lib = base / "lib"
    if lib.exists():
        candidates.extend(lib.glob("python*/site-packages"))
    return candidates


def bootstrap_runtime_packages() -> Path:
    """Подключает пакеты, скачанные мастером первого запуска."""
    d = packages_dir()
    candidates = _runtime_site_packages() + [
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
        ]
        for site in _runtime_site_packages():
            dll_dirs.extend([
                site / "openvino" / "libs",
            ])
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


DEFAULTS = {
    "whisper_model": "small",          # tiny|base|small|medium|large-v3
    "whisper_device": "auto",          # auto|cpu|cuda
    "whisper_compute": "auto",         # auto|int8|float16|float32
    "language": "auto",
    "gpu_policy": "auto",              # auto|gpu|cpu — тяжёлые задачи на GPU
    "max_cpu_workers": 0,              # 0 => авто (cpu_count-1)
    "theme": "studio-dark",
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
