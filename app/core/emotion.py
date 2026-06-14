# -*- coding: utf-8 -*-
"""OpenVINO face and emotion inference for browser-provided camera frames."""
from __future__ import annotations

import base64
import importlib.util
import threading
from pathlib import Path

from .. import config


EMOTIONS = ("neutral", "happy", "sad", "surprise", "anger")
EMOTION_LABELS = {
    "neutral": "нейтрально",
    "happy": "радость",
    "sad": "грусть",
    "surprise": "удивление",
    "anger": "злость",
}


def models_dir() -> Path:
    return config.app_root() / "models" / "emotion" / "intel"


def model_paths() -> tuple[Path, Path]:
    root = models_dir()
    face = root / "face-detection-retail-0004" / "FP16" / "face-detection-retail-0004.xml"
    emotion = root / "emotions-recognition-retail-0003" / "FP16" / "emotions-recognition-retail-0003.xml"
    return face, emotion


def dependency_status() -> dict:
    face, emotion = model_paths()
    installed = importlib.util.find_spec("openvino") is not None
    devices = []
    error = ""
    if installed:
        try:
            Core = _core_class()
            devices = list(Core().available_devices)
        except Exception as exc:  # noqa: BLE001
            error = str(exc)
    return {
        "installed": installed,
        "models_ready": face.exists() and emotion.exists(),
        "devices": devices,
        "models_dir": str(models_dir()),
        "error": error,
    }


def _core_class():
    try:
        from openvino import Core
    except ImportError:
        from openvino.runtime import Core
    return Core


class EmotionEngine:
    """Lazy, reusable OpenVINO inference engine."""

    def __init__(self):
        self._lock = threading.Lock()
        self._device = None
        self._face_model = None
        self._emotion_model = None
        self._face_output = None
        self._emotion_output = None

    def _load(self, device: str) -> None:
        requested = (device or "CPU").upper()
        if self._device == requested and self._face_model is not None:
            return

        face_path, emotion_path = model_paths()
        missing = [str(path) for path in (face_path, emotion_path) if not path.exists()]
        if missing:
            raise RuntimeError("Не найдены модели EmotionAI: " + ", ".join(missing))

        Core = _core_class()
        core = Core()
        available = list(core.available_devices)
        if requested not in available:
            raise RuntimeError(
                f"Устройство OpenVINO '{requested}' недоступно. "
                f"Доступно: {', '.join(available) or 'ничего'}"
            )

        self._face_model = core.compile_model(str(face_path), requested)
        self._emotion_model = core.compile_model(str(emotion_path), requested)
        self._face_output = self._face_model.output(0)
        self._emotion_output = self._emotion_model.output(0)
        self._device = requested

    def analyze_data_url(self, data_url: str, device: str = "CPU", threshold: float = 0.55) -> dict:
        import cv2
        import numpy as np

        raw = data_url.split(",", 1)[-1]
        try:
            encoded = base64.b64decode(raw, validate=True)
        except Exception as exc:  # noqa: BLE001
            raise ValueError("Некорректный кадр камеры") from exc
        frame = cv2.imdecode(np.frombuffer(encoded, dtype=np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            raise ValueError("Не удалось декодировать кадр камеры")

        with self._lock:
            self._load(device)
            faces = self._detect(frame, max(0.05, min(0.99, float(threshold))))

        height, width = frame.shape[:2]
        return {
            "ok": True,
            "width": width,
            "height": height,
            "device": self._device,
            "faces": faces,
        }

    def _detect(self, frame, threshold: float) -> list[dict]:
        import cv2
        import numpy as np

        height, width = frame.shape[:2]
        resized = cv2.resize(frame, (300, 300))
        blob = np.expand_dims(resized.transpose(2, 0, 1), axis=0)
        detections = self._face_model([blob])[self._face_output][0][0]
        faces = []

        for detection in detections:
            confidence = float(detection[2])
            if confidence < threshold:
                continue
            x_min = max(0, int(detection[3] * width))
            y_min = max(0, int(detection[4] * height))
            x_max = min(width, int(detection[5] * width))
            y_max = min(height, int(detection[6] * height))
            if x_max <= x_min or y_max <= y_min:
                continue

            crop = frame[y_min:y_max, x_min:x_max]
            emotion, emotion_score = self._recognize(crop)
            faces.append({
                "box": [x_min, y_min, x_max, y_max],
                "confidence": round(confidence, 4),
                "emotion": emotion,
                "label": EMOTION_LABELS.get(emotion, emotion),
                "emotion_score": round(emotion_score, 4),
            })
        return faces

    def _recognize(self, crop) -> tuple[str, float]:
        import cv2
        import numpy as np

        if crop.size == 0:
            return "neutral", 0.0
        resized = cv2.resize(crop, (64, 64))
        blob = np.expand_dims(resized.transpose(2, 0, 1), axis=0)
        result = self._emotion_model([blob])[self._emotion_output].reshape(-1)
        index = int(np.argmax(result))
        return EMOTIONS[index], float(result[index])
