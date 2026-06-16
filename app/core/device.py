# -*- coding: utf-8 -*-
"""
Определение оборудования и политика распределения нагрузки.

При первом запуске собирает данные об устройстве (CPU, наличие CUDA-видеокарты,
объём видеопамяти) и сохраняет их локально в device.json. Дальше эти данные
используются, чтобы по умолчанию пускать тяжёлые задачи (распознавание речи,
генерация конспектов через LLM) на видеокарту, а вспомогательные —
параллельно на процессор.
"""
from __future__ import annotations

import ctypes
import json
import multiprocessing
import os
import platform
import shutil
import subprocess
from typing import Optional

from .. import config


def _loadable_library(names) -> bool:
    loader = ctypes.WinDLL if os.name == "nt" else ctypes.CDLL
    for name in names:
        try:
            loader(name)
            return True
        except OSError:
            continue
    return False


def _ctranslate2_cuda_ready() -> bool:
    """CTranslate2 needs its own cuBLAS/cuDNN runtime, not only an ONNX CUDA provider."""
    try:
        import ctranslate2
        if ctranslate2.get_cuda_device_count() < 1:
            return False
    except Exception:
        return False

    if os.name == "nt":
        has_blas = _loadable_library(("cublas64_12.dll", "cublas64_11.dll"))
        has_dnn = _loadable_library(("cudnn64_9.dll", "cudnn64_8.dll"))
    else:
        has_blas = _loadable_library(("libcublas.so.12", "libcublas.so.11"))
        has_dnn = _loadable_library(("libcudnn.so.9", "libcudnn.so.8"))
    return has_blas and has_dnn


def _probe() -> dict:
    info = {
        "os": platform.system(),
        "os_release": platform.release(),
        "machine": platform.machine(),
        "cpu": platform.processor() or platform.machine(),
        "cpu_count": multiprocessing.cpu_count(),
        "has_cuda": False,
        "cuda_ready": False,
        "asr_cuda_ready": False,
        "onnx_cuda_ready": False,
        "gpu_available": False,
        "gpus": [],
        "vram_gb": 0.0,
        "torch": None,
        "onnx_providers": [],
    }
    # Физическая NVIDIA/CUDA-видеокарта без torch: нужно для мастера первого запуска.
    nvidia_smi = shutil.which("nvidia-smi")
    if nvidia_smi:
        try:
            out = subprocess.run(
                [nvidia_smi, "--query-gpu=name,memory.total",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5,
            )
            for line in out.stdout.splitlines():
                if not line.strip():
                    continue
                parts = [p.strip() for p in line.split(",", 1)]
                name = parts[0] or "NVIDIA GPU"
                try:
                    vram = round(float(parts[1]) / 1024, 1) if len(parts) > 1 else 0.0
                except ValueError:
                    vram = 0.0
                info["gpus"].append({"name": name, "vram_gb": vram})
            if info["gpus"]:
                info["gpu_available"] = True
                info["vram_gb"] = max(g["vram_gb"] for g in info["gpus"])
        except Exception:
            pass

    # CUDA через torch
    try:
        import torch
        info["torch"] = torch.__version__
        if torch.cuda.is_available():
            info["has_cuda"] = True
            info["cuda_ready"] = True
            info["gpu_available"] = True
            for i in range(torch.cuda.device_count()):
                props = torch.cuda.get_device_properties(i)
                if not any(g.get("name") == props.name for g in info["gpus"]):
                    info["gpus"].append({
                        "name": props.name,
                        "vram_gb": round(props.total_memory / (1024 ** 3), 1),
                    })
            if info["gpus"]:
                info["vram_gb"] = max(g["vram_gb"] for g in info["gpus"])
    except Exception:
        pass
    info["asr_cuda_ready"] = _ctranslate2_cuda_ready()
    if info["asr_cuda_ready"]:
        info["has_cuda"] = True
        info["cuda_ready"] = True
        info["gpu_available"] = True
    return info


def detect(force: bool = False) -> dict:
    """Возвращает данные об устройстве, кешируя их в device.json."""
    if not force and config.DEVICE_PATH.exists():
        try:
            cached = json.loads(config.DEVICE_PATH.read_text(encoding="utf-8"))
            if "asr_cuda_ready" in cached:
                return cached
        except Exception:
            pass
    info = _probe()
    try:
        config.DEVICE_PATH.write_text(
            json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    return info


def policy(settings: Optional[dict] = None) -> dict:
    """Куда отправлять задачи: 'cuda' или 'cpu' для тяжёлых, число воркеров CPU."""
    settings = settings or config.load_settings()
    dev = detect()
    pref = settings.get("gpu_policy", "auto")

    asr_cuda_ready = bool(dev.get("asr_cuda_ready"))
    light_cuda_ready = bool(dev.get("onnx_cuda_ready") or asr_cuda_ready)

    if pref == "cpu":
        heavy = "cpu"
    elif pref == "gpu":
        heavy = "cuda" if asr_cuda_ready else "cpu"
    else:  # auto
        heavy = "cuda" if asr_cuda_ready else "cpu"

    workers = int(settings.get("max_cpu_workers") or 0)
    if workers <= 0:
        workers = max(1, dev.get("cpu_count", 2) - 1)

    return {
        "heavy_device": heavy,        # ASR + LLM
        "light_device": "cuda" if light_cuda_ready and pref != "cpu" else "cpu",
        "cpu_workers": workers,
        "has_cuda": bool(dev.get("has_cuda")),
        "asr_cuda_ready": asr_cuda_ready,
        "onnx_cuda_ready": bool(dev.get("onnx_cuda_ready")),
        "gpu_available": dev.get("gpu_available", False),
        "gpus": dev.get("gpus", []),
    }


def summary() -> dict:
    """Короткая сводка для UI."""
    dev = detect()
    pol = policy()
    gpu_name = dev["gpus"][0]["name"] if dev.get("gpus") else None
    return {
        "cpu": dev.get("cpu"),
        "cpu_count": dev.get("cpu_count"),
        "has_cuda": dev.get("has_cuda"),
        "cuda_ready": dev.get("cuda_ready", dev.get("has_cuda")),
        "asr_cuda_ready": dev.get("asr_cuda_ready", False),
        "onnx_cuda_ready": dev.get("onnx_cuda_ready", False),
        "gpu_available": dev.get("gpu_available", dev.get("has_cuda")),
        "gpu_setup_needed": bool(dev.get("gpu_available") and not dev.get("has_cuda")),
        "gpu": gpu_name,
        "vram_gb": dev.get("vram_gb"),
        "heavy_device": pol["heavy_device"],
        "cpu_workers": pol["cpu_workers"],
    }
