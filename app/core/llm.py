# -*- coding: utf-8 -*-
"""Клиент к Ollama: генерация, чат, управление моделями (qwen по умолчанию)."""
from __future__ import annotations

import json
from typing import Callable, List, Optional

import requests


class OllamaClient:
    def __init__(self, host="http://127.0.0.1:11434", model="", default_model="qwen2.5:7b-instruct"):
        self.host = host.rstrip("/")
        self.model = model
        self.default_model = default_model

    # --- сервис -----------------------------------------------------------
    def available(self) -> bool:
        try:
            requests.get(self.host + "/api/tags", timeout=2)
            return True
        except Exception:
            return False

    def list_models(self) -> List[str]:
        try:
            r = requests.get(self.host + "/api/tags", timeout=5)
            return [m["name"] for m in r.json().get("models", [])]
        except Exception:
            return []

    def resolve_model(self) -> str:
        if self.model:
            return self.model
        models = self.list_models()
        qwen = [m for m in models if "qwen" in m.lower()]
        self.model = (qwen or models or [self.default_model])[0]
        return self.model

    def set_model(self, name: str) -> str:
        self.model = name or ""
        return self.resolve_model()

    # --- скачивание модели (ollama pull) ----------------------------------
    def pull(self, name: str, on_progress: Optional[Callable[[float, str], None]] = None) -> dict:
        url = self.host + "/api/pull"
        try:
            with requests.post(url, json={"name": name, "stream": True},
                               stream=True, timeout=3600) as r:
                r.raise_for_status()
                for line in r.iter_lines():
                    if not line:
                        continue
                    ch = json.loads(line.decode("utf-8"))
                    status = ch.get("status", "")
                    total = ch.get("total") or 0
                    completed = ch.get("completed") or 0
                    frac = (completed / total) if total else 0.0
                    if on_progress:
                        on_progress(min(0.999, frac), status)
                    if ch.get("error"):
                        return {"ok": False, "error": ch["error"]}
            if on_progress:
                on_progress(1.0, "Модель загружена")
            return {"ok": True, "model": name}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}

    # --- генерация (одиночный промпт) -------------------------------------
    def generate(
        self,
        prompt: str,
        system: str = "",
        on_token: Optional[Callable[[str], None]] = None,
        temperature: float = 0.4,
        num_ctx: int = 8192,
    ) -> str:
        model = self.resolve_model()
        payload = {
            "model": model, "prompt": prompt, "system": system,
            "stream": bool(on_token),
            "options": {"temperature": temperature, "num_ctx": num_ctx},
        }
        url = self.host + "/api/generate"
        if not on_token:
            r = requests.post(url, json=payload, timeout=600)
            r.raise_for_status()
            return r.json().get("response", "")
        out = []
        with requests.post(url, json=payload, stream=True, timeout=600) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if not line:
                    continue
                chunk = json.loads(line.decode("utf-8"))
                tok = chunk.get("response", "")
                if tok:
                    out.append(tok)
                    on_token(tok)
                if chunk.get("done"):
                    break
        return "".join(out)

    # --- чат (история сообщений) ------------------------------------------
    def chat(
        self,
        messages: List[dict],
        system: str = "",
        on_token: Optional[Callable[[str], None]] = None,
        temperature: float = 0.5,
        num_ctx: int = 8192,
    ) -> str:
        model = self.resolve_model()
        msgs = ([{"role": "system", "content": system}] if system else []) + messages
        payload = {"model": model, "messages": msgs, "stream": bool(on_token),
                   "options": {"temperature": temperature, "num_ctx": num_ctx}}
        url = self.host + "/api/chat"
        if not on_token:
            r = requests.post(url, json=payload, timeout=600)
            r.raise_for_status()
            return r.json().get("message", {}).get("content", "")
        out = []
        with requests.post(url, json=payload, stream=True, timeout=600) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if not line:
                    continue
                chunk = json.loads(line.decode("utf-8"))
                tok = chunk.get("message", {}).get("content", "")
                if tok:
                    out.append(tok)
                    on_token(tok)
                if chunk.get("done"):
                    break
        return "".join(out)


# --------------------------------------------------------------------------- #
#  Промпты
# --------------------------------------------------------------------------- #
SYS_RU = (
    "Ты — внимательный ассистент-конспектист. Отвечай по-русски, кратко и по делу, "
    "без воды и без выдуманных фактов. Используй markdown."
)

SYS_CHAT = (
    "Ты — локальный ассистент Submind. Отвечай по-русски, дружелюбно и по делу. "
    "Если приложен контекст видео — опирайся на него и можешь ссылаться на таймкоды."
)

SYS_FACE = (
    "Ты — ассистент по анализу лица. На вход даны агрегированные метрики "
    "(пол, возраст, число кадров, качество, геометрия точек). Дай аккуратное "
    "описание без псевдонаучных и медицинских выводов, по-русски, в markdown."
)


def prompt_explain(selection: str, context: str) -> str:
    return (
        "Объясни простыми словами выделенный фрагмент из субтитров. "
        "Дай определение терминов, расшифруй сокращения, приведи короткий пример, "
        "если уместно. Не более 6 предложений.\n\n"
        f"Выделено: «{selection}»\n\nКонтекст вокруг: {context}"
    )


def prompt_summary(text: str, style: str = "тезисы") -> str:
    return (
        f"Сделай конспект расшифровки видео в формате: {style}. "
        "Структурируй по смысловым блокам с подзаголовками. "
        "В конце добавь раздел «Главные выводы» из 3-5 пунктов.\n\n"
        f"Текст расшифровки:\n{text}"
    )


def prompt_glossary(text: str) -> str:
    return (
        "Извлеки из текста ключевые термины и дай каждому короткое определение. "
        "Верни СТРОГО JSON-массив объектов {\"term\":..., \"definition\":...} без пояснений.\n\n"
        f"Текст:\n{text}"
    )


def prompt_chapters(segments_brief: str) -> str:
    return (
        "Раздели видео на смысловые главы. На вход дан список реплик с таймкодами. "
        "Верни СТРОГО JSON-массив объектов {\"time\":<секунды>, \"title\":<кратко>} "
        "от 4 до 12 глав, по нарастанию времени.\n\n"
        f"Реплики:\n{segments_brief}"
    )


def prompt_qa(question: str, transcript: str) -> str:
    return (
        "Ответь на вопрос пользователя строго по содержанию расшифровки. "
        "Если ответа в тексте нет — так и скажи.\n\n"
        f"Вопрос: {question}\n\nРасшифровка:\n{transcript}"
    )


def prompt_face(metrics: str) -> str:
    return (
        "По этим метрикам лица дай короткое нейтральное описание: предполагаемый "
        "возрастной диапазон, пол, стабильность распознавания и качество съёмки. "
        "Добавь 2-3 совета, как улучшить съёмку. Без медицинских и оценочных суждений.\n\n"
        f"Метрики:\n{metrics}"
    )
