# -*- coding: utf-8 -*-
"""Хранение результата обработки видео."""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

from .. import config

_SCHEMA = {
    "language": None,
    "duration": 0.0,
    "segments": [],
}


def new_project(video_path: str) -> dict:
    p = {
        "id": uuid.uuid4().hex[:12],
        "created": time.time(),
        "updated": time.time(),
        "video_path": video_path,
        "title": Path(video_path).stem,
    }
    p.update({k: (v.copy() if isinstance(v, (list, dict)) else v) for k, v in _SCHEMA.items()})
    return p


def _backfill(d: dict) -> dict:
    for k, v in _SCHEMA.items():
        d.setdefault(k, v.copy() if isinstance(v, (list, dict)) else v)
    return d


def save(project: dict) -> str:
    project["updated"] = time.time()
    path = config.projects_dir() / f"{project['id']}.json"
    path.write_text(json.dumps(project, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)


def load(project_id: str) -> dict:
    path = config.projects_dir() / f"{project_id}.json"
    return _backfill(json.loads(path.read_text(encoding="utf-8")))


def list_projects() -> list:
    items = []
    for p in config.projects_dir().glob("*.json"):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            items.append({
                "id": d["id"], "title": d.get("title", "—"),
                "updated": d.get("updated", 0),
                "duration": d.get("duration", 0),
                "video_path": d.get("video_path", ""),
                "has_segments": bool(d.get("segments")),
            })
        except Exception:
            continue
    return sorted(items, key=lambda x: -x["updated"])


def delete(project_id: str) -> bool:
    path = config.projects_dir() / f"{project_id}.json"
    if path.exists():
        path.unlink()
        return True
    return False
