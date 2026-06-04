# -*- coding: utf-8 -*-
"""LAN API for mobile/desktop-to-desktop Submind workflows."""
from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import shutil
import socket
import threading
import time
import uuid
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from .. import config
from . import export


def _ctype(path: str) -> str:
    return mimetypes.guess_type(path)[0] or "application/octet-stream"


def _read_json(path: Path, fallback):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return fallback
    return fallback


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


class DeviceStore:
    def __init__(self):
        self.path = config.DEVICES_PATH
        self.lock = threading.Lock()

    def all(self) -> list[dict]:
        return _read_json(self.path, [])

    def trusted(self, device_id: str) -> bool:
        return any(d.get("id") == device_id and d.get("trusted") for d in self.all())

    def upsert(self, device: dict, trusted=False) -> dict:
        with self.lock:
            items = self.all()
            now = time.time()
            device_id = device.get("id") or uuid.uuid4().hex
            merged = {
                "id": device_id,
                "name": device.get("name") or "LAN device",
                "kind": device.get("kind") or "unknown",
                "trusted": bool(trusted),
                "updated": now,
            }
            found = False
            for item in items:
                if item.get("id") == device_id:
                    item.update(merged)
                    if item.get("paired"):
                        item["trusted"] = bool(trusted or item.get("trusted"))
                    found = True
                    merged = item
            if not found:
                merged["paired"] = bool(trusted)
                merged["created"] = now
                items.append(merged)
            _write_json(self.path, items)
            return merged

    def trust(self, device_id: str, trusted=True) -> bool:
        with self.lock:
            items = self.all()
            changed = False
            for item in items:
                if item.get("id") == device_id:
                    item["trusted"] = bool(trusted)
                    item["paired"] = bool(trusted)
                    item["updated"] = time.time()
                    changed = True
            _write_json(self.path, items)
            return changed


class JobStore:
    def __init__(self):
        self.path = config.LAN_JOBS_PATH
        self.lock = threading.Lock()

    def all(self) -> dict:
        return _read_json(self.path, {})

    def get(self, job_id: str) -> dict:
        return self.all().get(job_id, {})

    def save(self, job: dict) -> dict:
        with self.lock:
            data = self.all()
            job["updated"] = time.time()
            data[job["id"]] = job
            _write_json(self.path, data)
            return job

    def create(self, meta: dict, trusted: bool) -> dict:
        job_id = uuid.uuid4().hex[:12]
        root = config.uploads_dir() / job_id
        root.mkdir(parents=True, exist_ok=True)
        job = {
            "id": job_id,
            "status": "accepted" if trusted else "pending",
            "device": meta.get("device") or {},
            "options": meta.get("options") or {},
            "filename": meta.get("filename") or "upload.bin",
            "size": int(meta.get("size") or 0),
            "chunks": {},
            "root": str(root),
            "created": time.time(),
            "updated": time.time(),
            "message": "Ожидает подтверждения" if not trusted else "Готов к загрузке",
        }
        return self.save(job)

    def approve(self, job_id: str, approved=True) -> dict:
        job = self.get(job_id)
        if not job:
            return {}
        job["status"] = "accepted" if approved else "rejected"
        job["message"] = "Подтверждено" if approved else "Отклонено"
        return self.save(job)


class LanServer:
    def __init__(self, web_root, on_event=None, on_complete=None):
        self.web_root = str(web_root)
        self.on_event = on_event
        self.on_complete = on_complete
        self.devices = DeviceStore()
        self.jobs = JobStore()
        self._httpd = None
        self._thread = None
        self.port = 0
        self.pair_token = uuid.uuid4().hex[:8]

    def start(self, port=0) -> int:
        if self._httpd:
            return self.port
        handler = self._make_handler()
        self._httpd = ThreadingHTTPServer(("0.0.0.0", port), handler)
        self.port = int(self._httpd.server_address[1])
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        return self.port

    def stop(self):
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None

    def urls(self) -> list[str]:
        return [f"http://{ip}:{self.port}" for ip in local_ips() if self.port]

    def pairing_payload(self) -> dict:
        url = self.urls()[0] if self.urls() else ""
        return {
            "device_id": config.device_id(),
            "device_name": socket.gethostname(),
            "token": self.pair_token,
            "url": url,
            "pair_url": f"{url}/?pair={self.pair_token}" if url else "",
            "qr_svg": qr_svg(f"{url}/?pair={self.pair_token}") if url else "",
            "urls": self.urls(),
        }

    def approve_job(self, job_id: str, approved=True) -> dict:
        return self.jobs.approve(job_id, approved)

    def trust_device(self, device_id: str, trusted=True) -> bool:
        return self.devices.trust(device_id, trusted)

    def _emit(self, event: str, payload: dict):
        if self.on_event:
            try:
                self.on_event(event, payload)
            except Exception:
                pass

    def _make_handler(self):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def _json(self, payload, status=200):
                data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Headers", "content-type,x-device-id,x-chunk-index,x-chunk-sha256")
                self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _body_json(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b"{}"
                return json.loads(raw.decode("utf-8") or "{}")

            def do_OPTIONS(self):
                self._json({"ok": True})

            def do_GET(self):
                path = urlparse(self.path).path
                if path == "/api/lan/hello":
                    self._json({
                        "ok": True,
                        "app": "Submind",
                        "device_id": config.device_id(),
                        "device_name": socket.gethostname(),
                        "pairing": outer.pairing_payload(),
                    })
                elif path == "/api/devices":
                    self._json({"ok": True, "devices": outer.devices.all()})
                elif path == "/api/jobs":
                    self._json({"ok": True, "jobs": list(outer.jobs.all().values())})
                elif path.startswith("/api/jobs/") and path.endswith("/events"):
                    job_id = path.split("/")[3]
                    self._json({"ok": True, "job": outer.jobs.get(job_id)})
                elif path.startswith("/api/projects/") and path.endswith("/bundle"):
                    project_id = path.split("/")[3]
                    self._serve_bundle(project_id)
                else:
                    self._serve_static()

            def do_POST(self):
                path = urlparse(self.path).path
                try:
                    if path == "/api/pair/request":
                        body = self._body_json()
                        trusted = body.get("token") == outer.pair_token
                        device = outer.devices.upsert(body.get("device") or body, trusted=trusted)
                        outer._emit("lan:pair_request", {"device": device, "trusted": trusted})
                        self._json({"ok": True, "trusted": trusted, "device": device})
                    elif path == "/api/pair/confirm":
                        body = self._body_json()
                        ok = outer.devices.trust(body.get("device_id", ""), True)
                        self._json({"ok": ok})
                    elif path == "/api/jobs":
                        body = self._body_json()
                        device_payload = body.get("device") or {}
                        device_id = device_payload.get("id") or self.headers.get("X-Device-Id", "")
                        if device_id:
                            outer.devices.upsert(device_payload or {"id": device_id}, trusted=False)
                        trusted = outer.devices.trusted(device_id)
                        job = outer.jobs.create(body, trusted=trusted)
                        outer._emit("lan:job_request", {"job": job, "trusted": trusted})
                        self._json({"ok": True, "job": job})
                    elif path.startswith("/api/jobs/") and path.endswith("/chunks"):
                        self._save_chunk(path.split("/")[3])
                    elif path.startswith("/api/jobs/") and path.endswith("/complete"):
                        self._complete_job(path.split("/")[3])
                    else:
                        self._json({"ok": False, "error": "not found"}, 404)
                except Exception as e:  # noqa: BLE001
                    self._json({"ok": False, "error": str(e)}, 500)

            def _save_chunk(self, job_id: str):
                job = outer.jobs.get(job_id)
                if not job or job.get("status") not in ("accepted", "uploading"):
                    self._json({"ok": False, "error": "job is not accepted"}, 403)
                    return
                idx = self.headers.get("X-Chunk-Index")
                sha = self.headers.get("X-Chunk-Sha256", "")
                if idx is None:
                    self._json({"ok": False, "error": "missing chunk index"}, 400)
                    return
                raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                digest = hashlib.sha256(raw).hexdigest()
                if sha and digest.lower() != sha.lower():
                    self._json({"ok": False, "error": "checksum mismatch"}, 400)
                    return
                part = Path(job["root"]) / f"{int(idx):08d}.part"
                part.write_bytes(raw)
                job["chunks"][str(idx)] = {"bytes": len(raw), "sha256": digest}
                job["status"] = "uploading"
                job["message"] = f"Получен chunk {idx}"
                outer.jobs.save(job)
                self._json({"ok": True, "received": idx, "sha256": digest})

            def _complete_job(self, job_id: str):
                job = outer.jobs.get(job_id)
                if not job:
                    self._json({"ok": False, "error": "job not found"}, 404)
                    return
                root = Path(job["root"])
                safe_name = Path(job.get("filename") or "upload.bin").name
                out_path = root / safe_name
                with out_path.open("wb") as out:
                    for part in sorted(root.glob("*.part")):
                        out.write(part.read_bytes())
                expected = int(job.get("size") or 0)
                actual = out_path.stat().st_size
                if expected and actual != expected:
                    job["status"] = "failed"
                    job["message"] = f"Upload size mismatch: {actual} != {expected}"
                    outer.jobs.save(job)
                    self._json({"ok": False, "error": job["message"]}, 400)
                    return
                job["status"] = "queued"
                job["video_path"] = str(out_path)
                job["message"] = "Видео поставлено в очередь обработки"
                outer.jobs.save(job)
                outer._emit("lan:job_queued", {"job": job})
                if outer.on_complete:
                    threading.Thread(target=outer.on_complete, args=(job,), daemon=True).start()
                self._json({"ok": True, "job": job})

            def _serve_bundle(self, project_id: str):
                proj = config.projects_dir() / f"{project_id}.json"
                if not proj.exists():
                    self._json({"ok": False, "error": "project not found"}, 404)
                    return
                bundle = config.cache_dir() / f"{project_id}-bundle.zip"
                data_json = json.loads(proj.read_text(encoding="utf-8"))
                segments = data_json.get("segments") or []
                persons = data_json.get("persons") or []
                speakers = {p.get("id"): p.get("label") for p in persons if p.get("id") is not None}
                with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as zf:
                    zf.write(proj, "project.json")
                    if segments:
                        zf.writestr("subtitles.srt", export.to_srt(segments))
                        zf.writestr("subtitles.vtt", export.to_vtt(segments))
                        zf.writestr("transcript.txt", export.to_txt(segments, speakers))
                        zf.writestr("summary.md", export.to_markdown(
                            data_json.get("title") or project_id,
                            segments,
                            data_json.get("summary") or "",
                            data_json.get("glossary") or [],
                            data_json.get("chapters") or [],
                            speakers,
                        ))
                data = bundle.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _serve_static(self):
                rel = unquote(urlparse(self.path).path).lstrip("/")
                if rel in ("", "index.html"):
                    rel = "index.html"
                target = os.path.normpath(os.path.join(outer.web_root, rel))
                root = os.path.normpath(outer.web_root)
                if not target.startswith(root) or not os.path.isfile(target):
                    self._json({"ok": False, "error": "not found"}, 404)
                    return
                data = Path(target).read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", _ctype(target))
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)

        return Handler


def local_ips() -> list[str]:
    ips = []
    try:
        host = socket.gethostname()
        for item in socket.getaddrinfo(host, None, socket.AF_INET):
            ip = item[4][0]
            if not ip.startswith("127.") and ip not in ips:
                ips.append(ip)
    except Exception:
        pass
    if not ips:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            if not ip.startswith("127."):
                ips.append(ip)
        except Exception:
            pass
    return ips


def qr_svg(text: str) -> str:
    """Return an SVG QR code, with a visible fallback if qrcode is unavailable."""
    try:
        import io
        import qrcode
        import qrcode.image.svg

        img = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage)
        buf = io.BytesIO()
        img.save(buf)
        return buf.getvalue().decode("utf-8")
    except Exception:
        pass

    # Fallback is not a standards-complete QR encoder. The URL is displayed in
    # text next to the code, so pairing remains possible without the dependency.
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    size = 29
    cells = []
    for y in range(size):
        for x in range(size):
            finder = (
                (x < 7 and y < 7)
                or (x >= size - 7 and y < 7)
                or (x < 7 and y >= size - 7)
            )
            if finder:
                on = x in (0, 6, size - 7, size - 1) or y in (0, 6, size - 7, size - 1) or (2 <= x % (size - 7 or 1) <= 4 and 2 <= y % (size - 7 or 1) <= 4)
            else:
                b = digest[(x * 7 + y * 13) % len(digest)]
                on = ((b >> ((x + y) % 8)) & 1) == 1
            if on:
                cells.append(f'<rect x="{x}" y="{y}" width="1" height="1"/>')
    return f'<svg viewBox="0 0 {size} {size}" xmlns="http://www.w3.org/2000/svg">{"".join(cells)}</svg>'
