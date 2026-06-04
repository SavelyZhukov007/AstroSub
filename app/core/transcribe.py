# -*- coding: utf-8 -*-
"""Генерация субтитров через faster-whisper (word-level таймкоды)."""
from __future__ import annotations

from typing import Callable, Optional


class Transcriber:
    """Ленивая обёртка над faster-whisper. Модель грузится при первом вызове."""

    def __init__(self, model_size="small", device="auto", compute_type="auto"):
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self._model = None

    def _resolve(self):
        device = self.device
        compute = self.compute_type
        if device == "auto":
            try:
                import torch
                device = "cuda" if torch.cuda.is_available() else "cpu"
            except Exception:
                device = "cpu"
        if compute == "auto":
            compute = "float16" if device == "cuda" else "int8"
        return device, compute

    def load(self):
        if self._model is not None:
            return
        from faster_whisper import WhisperModel
        device, compute = self._resolve()
        try:
            self._model = WhisperModel(self.model_size, device=device, compute_type=compute)
        except Exception:
            if device != "cuda":
                raise
            self.device = "cpu"
            self.compute_type = "int8"
            self._model = WhisperModel(self.model_size, device="cpu", compute_type="int8")

    def transcribe(
        self,
        audio_path: str,
        language: Optional[str] = None,
        translate: bool = False,
        on_progress: Optional[Callable[[float, str], None]] = None,
        total_duration: float = 0.0,
    ) -> dict:
        """Возвращает {language, duration, segments:[{id,start,end,text,words:[...] }]}"""
        self.load()
        task = "translate" if translate else "transcribe"
        lang = None if (not language or language == "auto") else language

        segments_iter, info = self._model.transcribe(
            audio_path,
            language=lang,
            task=task,
            word_timestamps=True,
            vad_filter=True,
            vad_parameters=dict(min_silence_duration_ms=400),
            beam_size=5,
        )

        dur = total_duration or float(getattr(info, "duration", 0.0) or 0.0)
        out_segments = []
        for i, seg in enumerate(segments_iter):
            words = []
            for w in (seg.words or []):
                words.append({
                    "start": round(float(w.start), 3),
                    "end": round(float(w.end), 3),
                    "word": w.word,
                    "prob": round(float(getattr(w, "probability", 1.0)), 3),
                })
            out_segments.append({
                "id": i,
                "start": round(float(seg.start), 3),
                "end": round(float(seg.end), 3),
                "text": seg.text.strip(),
                "words": words,
                "speaker": None,
            })
            if on_progress and dur:
                on_progress(min(0.99, seg.end / dur), seg.text.strip()[:80])

        if on_progress:
            on_progress(1.0, "Готово")

        return {
            "language": getattr(info, "language", lang or "unknown"),
            "duration": dur,
            "segments": out_segments,
        }

    def quick(self, audio_path: str, language: Optional[str] = None) -> str:
        """Быстрая расшифровка короткого аудио (голосовое сообщение) в текст."""
        self.load()
        lang = None if (not language or language == "auto") else language
        segments_iter, _ = self._model.transcribe(
            audio_path, language=lang, vad_filter=True, beam_size=5)
        return " ".join(s.text.strip() for s in segments_iter).strip()
