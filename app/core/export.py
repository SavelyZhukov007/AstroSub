# -*- coding: utf-8 -*-
"""Экспорт субтитров и конспектов в разные форматы."""
from __future__ import annotations

from typing import List, Optional


def _ts(seconds: float, comma=True) -> str:
    if seconds < 0:
        seconds = 0
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    sep = "," if comma else "."
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def to_srt(segments: List[dict]) -> str:
    out = []
    for i, s in enumerate(segments, 1):
        out.append(str(i))
        out.append(f"{_ts(s['start'])} --> {_ts(s['end'])}")
        out.append(s["text"])
        out.append("")
    return "\n".join(out)


def to_vtt(segments: List[dict]) -> str:
    out = ["WEBVTT", ""]
    for s in segments:
        out.append(f"{_ts(s['start'], comma=False)} --> {_ts(s['end'], comma=False)}")
        out.append(s["text"])
        out.append("")
    return "\n".join(out)


def to_ass(segments: List[dict]) -> str:
    head = (
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 1920\nPlayResY: 1080\n\n"
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, "
        "OutlineColour, BackColour, Bold, Outline, Shadow, Alignment, "
        "MarginL, MarginR, MarginV, Encoding\n"
        "Style: Default,Arial,52,&H00FFFFFF,&H00101010,&H64000000,0,2,1,2,80,80,60,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, "
        "MarginV, Effect, Text\n"
    )

    def ats(sec):
        cs = int(round(sec * 100))
        h, cs = divmod(cs, 360000)
        m, cs = divmod(cs, 6000)
        s, cs = divmod(cs, 100)
        return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"

    lines = []
    for s in segments:
        txt = s["text"].replace("\n", "\\N")
        lines.append(f"Dialogue: 0,{ats(s['start'])},{ats(s['end'])},Default,,0,0,0,,{txt}")
    return head + "\n".join(lines) + "\n"


def to_txt(segments: List[dict], speakers: Optional[dict] = None) -> str:
    out = []
    for s in segments:
        sp = ""
        if speakers and s.get("speaker") is not None:
            sp = speakers.get(s["speaker"], f"Спикер {s['speaker'] + 1}") + ": "
        out.append(sp + s["text"])
    return "\n".join(out)


def to_markdown(title: str, segments: List[dict], summary: str = "",
                glossary: Optional[list] = None, chapters: Optional[list] = None,
                speakers: Optional[dict] = None) -> str:
    md = [f"# {title}", ""]
    if chapters:
        md += ["## Главы", ""]
        for c in chapters:
            md.append(f"- `{_ts(c['time'], comma=False)[:8]}` {c['title']}")
        md.append("")
    if summary:
        md += ["## Конспект", "", summary, ""]
    if glossary:
        md += ["## Глоссарий", ""]
        for g in glossary:
            md.append(f"- **{g['term']}** — {g['definition']}")
        md.append("")
    md += ["## Полная расшифровка", ""]
    md.append(to_txt(segments, speakers))
    return "\n".join(md)


EXPORTERS = {
    "srt": ("srt", to_srt),
    "vtt": ("vtt", to_vtt),
    "ass": ("ass", to_ass),
    "txt": ("txt", to_txt),
    "md": ("md", to_markdown),
}
