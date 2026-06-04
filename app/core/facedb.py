# -*- coding: utf-8 -*-
"""
Постоянная галерея лиц (facedb.json).

Позволяет узнавать спикеров от сессии к сессии: при анализе нового видео
кластеры лиц сравниваются с уже известными по косинусной близости
ArcFace-эмбеддингов. Если совпадение выше порога — берётся сохранённое имя.
"""
from __future__ import annotations

import json
import time
from typing import List, Optional

from .. import config


def _load() -> list:
    if config.FACEDB_PATH.exists():
        try:
            return json.loads(config.FACEDB_PATH.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def _save(entries: list) -> None:
    config.FACEDB_PATH.write_text(
        json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")


def all_people() -> list:
    """Список известных лиц без громоздких эмбеддингов."""
    out = []
    for e in _load():
        out.append({
            "uid": e["uid"], "label": e["label"],
            "samples": e.get("samples", 1),
            "thumb": e.get("thumb"),
            "updated": e.get("updated", 0),
        })
    return sorted(out, key=lambda x: -x["updated"])


def _norm(v: np.ndarray) -> np.ndarray:
    import numpy as np

    return v / (np.linalg.norm(v) + 1e-9)


def match(embedding, threshold: float) -> Optional[dict]:
    """Возвращает {uid,label} ближайшего известного лица или None."""
    import numpy as np

    entries = _load()
    if not entries:
        return None
    q = _norm(np.asarray(embedding, dtype=np.float32))
    best, best_sim = None, -1.0
    for e in entries:
        ref = _norm(np.asarray(e["embedding"], dtype=np.float32))
        sim = float(np.dot(q, ref))
        if sim > best_sim:
            best_sim, best = sim, e
    # косинусная дистанция = 1 - sim; узнаём, если дистанция меньше порога
    if best is not None and (1.0 - best_sim) <= threshold:
        return {"uid": best["uid"], "label": best["label"], "similarity": round(best_sim, 3)}
    return None


def remember(uid: str, label: str, embedding, thumb: Optional[str] = None) -> None:
    """Добавляет/обновляет лицо в галерее (бегущее среднее эмбеддинга)."""
    import numpy as np

    entries = _load()
    emb = _norm(np.asarray(embedding, dtype=np.float32))
    for e in entries:
        if e["uid"] == uid:
            n = e.get("samples", 1)
            ref = np.asarray(e["embedding"], dtype=np.float32)
            mixed = _norm((ref * n + emb) / (n + 1))
            e["embedding"] = mixed.tolist()
            e["samples"] = n + 1
            e["label"] = label or e["label"]
            e["updated"] = time.time()
            if thumb:
                e["thumb"] = thumb
            _save(entries)
            return
    entries.append({
        "uid": uid, "label": label, "embedding": emb.tolist(),
        "samples": 1, "thumb": thumb, "updated": time.time(),
    })
    _save(entries)


def rename(uid: str, label: str) -> bool:
    entries = _load()
    changed = False
    for e in entries:
        if e["uid"] == uid:
            e["label"] = label
            e["updated"] = time.time()
            changed = True
    if changed:
        _save(entries)
    return changed


def forget(uid: str) -> bool:
    entries = _load()
    new = [e for e in entries if e["uid"] != uid]
    if len(new) != len(entries):
        _save(new)
        return True
    return False
