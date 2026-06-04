# -*- coding: utf-8 -*-
"""
Проверка и установка опциональных зависимостей.

.exe самодостаточен в части UI и ядра, но тяжёлые ML-функции зависят от
крупных пакетов. При первом запуске можно выбрать, что доустановить.
Не выбранное просто отключается — приложение продолжает работать.
"""
from __future__ import annotations

import importlib.util
import io
import subprocess
import sys
import threading
from contextlib import redirect_stderr, redirect_stdout
from typing import Callable, List, Optional

from .. import config

config.bootstrap_runtime_packages()

# группа -> (заголовок, описание, [pip-пакеты], [проверяемые модули])
FEATURES = {
    "asr": ("Распознавание речи", "Субтитры из видео (faster-whisper).",
            ["faster-whisper"], ["faster_whisper"]),
    "faces": ("Лица и спикеры", "Идентификация спикеров и FaceID (InsightFace).",
              ["insightface", "onnxruntime", "scikit-learn"],
              ["insightface", "onnxruntime", "sklearn"]),
    "mesh": ("Сетка лица 468", "Плотная лицевая сетка (MediaPipe).",
             ["mediapipe"], ["mediapipe"]),
    "video": ("Обработка кадров", "Чтение видео и превью (OpenCV).",
              ["opencv-python"], ["cv2"]),
    "gpu": ("Ускорение на видеокарте", "CUDA-сборка onnxruntime + torch, если доступна NVIDIA/CUDA.",
            ["onnxruntime-gpu", "torch"], ["onnxruntime", "torch"]),
}


def _has(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except Exception:
        return False


def _gpu_runtime_ready() -> bool:
    try:
        import onnxruntime as ort
        if any("CUDA" in p for p in ort.get_available_providers()):
            return True
    except Exception:
        pass
    try:
        import torch
        return bool(torch.cuda.is_available())
    except Exception:
        return False


def _gpu_available() -> bool:
    try:
        from . import device
        dev = device.detect()
        return bool(dev.get("gpu_available") or dev.get("has_cuda"))
    except Exception:
        return False


def _installed(key: str, mods: list) -> bool:
    if key == "gpu":
        return _gpu_runtime_ready()
    return all(_has(m) for m in mods)


def check() -> list:
    """Статус каждой группы: установлена ли (по всем её модулям)."""
    out = []
    gpu_available = _gpu_available()
    for key, (title, desc, pkgs, mods) in FEATURES.items():
        installed = _installed(key, mods)
        recommended = not installed and (key != "gpu" or gpu_available)
        out.append({
            "key": key, "title": title, "desc": desc,
            "packages": pkgs, "installed": installed,
            "recommended": recommended,
            "available": key != "gpu" or gpu_available,
        })
    return out


def _packages_for(keys: List[str]) -> list:
    pkgs: List[str] = []
    key_set = set(keys or [])
    for k in keys:
        if k in FEATURES:
            pkgs.extend(FEATURES[k][2])
    if "gpu" in key_set:
        pkgs = [p for p in pkgs if p != "onnxruntime"]

    seen, ordered = set(), []
    for p in pkgs:
        if p not in seen:
            seen.add(p)
            ordered.append(p)
    return ordered


def _run_pip_install(pkg: str) -> tuple[int, str]:
    target = config.packages_dir()
    target.mkdir(parents=True, exist_ok=True)
    args = [
        "install",
        "--upgrade",
        "--target", str(target),
        "--no-warn-script-location",
        pkg,
    ]

    if not getattr(sys, "frozen", False):
        proc = subprocess.run(
            [sys.executable, "-m", "pip", *args],
            capture_output=True, text=True, timeout=1800,
        )
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")

    try:
        from pip._internal.cli.main import main as pip_main
    except Exception as e:  # noqa: BLE001
        return 1, f"В сборку не попал pip: {e}"

    buf = io.StringIO()
    try:
        with redirect_stdout(buf), redirect_stderr(buf):
            code = pip_main(args)
    except SystemExit as e:
        code = int(e.code or 0) if isinstance(e.code, int) else 1
    except Exception as e:  # noqa: BLE001
        return 1, str(e)
    return int(code or 0), buf.getvalue()


def _short_error(text: str) -> str:
    lines = [l.strip() for l in (text or "").splitlines() if l.strip()]
    return lines[-1] if lines else "неизвестная ошибка pip"


def install(keys: List[str],
            on_progress: Optional[Callable[[float, str], None]] = None,
            gpu: bool = False) -> dict:
    """Ставит pip-пакеты для выбранных групп. Возвращает итог."""
    ordered = _packages_for(keys or [])

    if not ordered:
        if on_progress:
            on_progress(1.0, "Нечего устанавливать")
        return {"ok": True, "installed": []}

    done = []
    failed = []
    total = len(ordered)
    for i, pkg in enumerate(ordered):
        if on_progress:
            on_progress(i / total, f"Установка {pkg}…")
        code, output = _run_pip_install(pkg)
        if code == 0:
            done.append(pkg)
            config.bootstrap_runtime_packages()
            importlib.invalidate_caches()
        else:
            failed.append({"package": pkg, "error": _short_error(output)})
            if on_progress:
                on_progress((i + 1) / total, f"Ошибка с {pkg}: {failed[-1]['error']}")

    if "gpu" in set(keys or []) or gpu:
        try:
            from . import device
            device.detect(force=True)
        except Exception:
            pass

    if on_progress:
        on_progress(1.0, "Готово" if not failed else "Готово с ошибками")
    return {
        "ok": not failed,
        "installed": done,
        "failed": failed,
        "target": str(config.packages_dir()),
    }


def install_async(keys, on_progress, gpu=False):
    t = threading.Thread(target=install, args=(keys, on_progress, gpu), daemon=True)
    t.start()
    return t
