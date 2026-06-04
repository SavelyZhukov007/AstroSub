# -*- coding: utf-8 -*-
"""Работа с медиа через ffmpeg/ffprobe и opencv."""
import json
import shutil
import subprocess
import tempfile
from pathlib import Path


def has_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


def probe_duration(path: str) -> float:
    """Длительность в секундах. Возвращает 0.0 при ошибке."""
    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        try:
            out = subprocess.run(
                [ffprobe, "-v", "quiet", "-print_format", "json",
                 "-show_format", path],
                capture_output=True, text=True, timeout=30,
            )
            return float(json.loads(out.stdout)["format"]["duration"])
        except Exception:
            pass
    # fallback через opencv
    try:
        import cv2
        cap = cv2.VideoCapture(path)
        fps = cap.get(cv2.CAP_PROP_FPS) or 25
        frames = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
        cap.release()
        return frames / fps if fps else 0.0
    except Exception:
        return 0.0


def extract_audio(path: str, sample_rate: int = 16000) -> str:
    """Извлекает моно-аудио в WAV 16 кГц для Whisper. Возвращает путь к файлу."""
    if not has_ffmpeg():
        # Whisper умеет читать видео напрямую через av, но WAV надёжнее
        return path
    tmp = Path(tempfile.gettempdir()) / ("submind_%d.wav" % abs(hash(path)))
    subprocess.run(
        ["ffmpeg", "-y", "-i", path, "-vn", "-ac", "1",
         "-ar", str(sample_rate), "-f", "wav", str(tmp)],
        capture_output=True,
    )
    return str(tmp) if tmp.exists() else path


def iter_frames(path: str, fps: float = 0.5):
    """Генератор (timestamp_sec, BGR-кадр) с заданной частотой выборки."""
    import cv2
    cap = cv2.VideoCapture(path)
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 25
    step = max(1, int(round(src_fps / max(fps, 0.01))))
    idx = 0
    while True:
        ok = cap.grab()
        if not ok:
            break
        if idx % step == 0:
            ok, frame = cap.retrieve()
            if ok:
                yield idx / src_fps, frame
        idx += 1
    cap.release()


def grab_thumbnail(path: str, ts: float, out_path: str, width: int = 320) -> bool:
    if not has_ffmpeg():
        return False
    r = subprocess.run(
        ["ffmpeg", "-y", "-ss", str(max(0.0, ts)), "-i", path,
         "-frames:v", "1", "-vf", f"scale={width}:-1", out_path],
        capture_output=True,
    )
    return r.returncode == 0 and Path(out_path).exists()


def save_b64_audio(b64: str, suffix: str = ".webm") -> str:
    """Сохраняет base64-аудио (голосовое из браузера) во временный файл."""
    import base64
    if "," in b64:
        b64 = b64.split(",", 1)[1]
    data = base64.b64decode(b64)
    tmp = Path(tempfile.gettempdir()) / f"submind_voice_{abs(hash(b64)) % 10_000_000}{suffix}"
    tmp.write_bytes(data)
    return str(tmp)
