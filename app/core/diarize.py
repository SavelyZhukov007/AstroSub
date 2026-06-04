# -*- coding: utf-8 -*-
"""
Лёгкая диаризация: к каждой реплике субтитров привязывается person из
лицевой таймлинии по перекрытию времени (active-speaker эвристика).
Без тяжёлого pyannote — работает офлайн на уже посчитанных лицах.
"""
from __future__ import annotations

from typing import List


def assign_speakers(segments: List[dict], face_timeline: List[dict]) -> List[dict]:
    """Для каждого сегмента ставит speaker = id личности, чаще всего
    присутствующей на экране в интервале реплики."""
    if not face_timeline:
        return segments

    fl = sorted(face_timeline, key=lambda x: x["t"])
    times = [f["t"] for f in fl]

    import bisect
    for seg in segments:
        lo = bisect.bisect_left(times, seg["start"])
        hi = bisect.bisect_right(times, seg["end"])
        window = fl[lo:hi] if hi > lo else _nearest(fl, times, seg["start"])
        if not window:
            continue
        votes = {}
        for f in window:
            votes[f["person"]] = votes.get(f["person"], 0) + 1
        seg["speaker"] = max(votes, key=votes.get)
    return segments


def _nearest(fl, times, t):
    import bisect
    if not fl:
        return []
    i = min(range(len(times)), key=lambda k: abs(times[k] - t))
    return [fl[i]]
