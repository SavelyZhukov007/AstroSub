# -*- coding: utf-8 -*-
"""
Локальный HTTP-сервер.

1. Отдаёт текущий медиафайл по /media/<token> с поддержкой Range —
   иначе встроенный WebView (Edge WebView2) часто показывает чёрный экран.
2. Отдаёт статику UI (web/) с http://127.0.0.1 — это «безопасный контекст»,
   в котором работают камера и микрофон (getUserMedia) для FaceID и голоса.
   По file:// доступ к камере/микрофону браузер блокирует.
"""
from __future__ import annotations

import mimetypes
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse


class _State:
    media_path = None
    web_root = None
    data_root = None
    token = "submind"


def _ctype(path: str) -> str:
    t, _ = mimetypes.guess_type(path)
    return t or "application/octet-stream"


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    # ---- медиа с поддержкой Range -----------------------------------
    def _serve_media(self, head_only=False):
        parts = self.path.split("/")
        if len(parts) < 3 or unquote(parts[2]) != _State.token:
            self.send_error(404); return
        path = _State.media_path
        if not path or not os.path.isfile(path):
            self.send_error(404); return
        size = os.path.getsize(path)
        ctype = _ctype(path)
        rng = self.headers.get("Range")
        if rng and rng.startswith("bytes="):
            try:
                s, e = rng[6:].split("-", 1)
                start = int(s) if s else 0
                end = int(e) if e else size - 1
            except ValueError:
                start, end = 0, size - 1
            start = max(0, start); end = min(end, size - 1)
            length = end - start + 1
            self.send_response(206)
            self.send_header("Content-Type", ctype)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.send_header("Content-Length", str(length))
            self.end_headers()
            if not head_only:
                self._pipe(path, start, length)
        else:
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(size))
            self.end_headers()
            if not head_only:
                self._pipe(path, 0, size)

    def _pipe(self, path, start, length):
        chunk = 256 * 1024
        try:
            with open(path, "rb") as f:
                f.seek(start); remaining = length
                while remaining > 0:
                    data = f.read(min(chunk, remaining))
                    if not data:
                        break
                    self.wfile.write(data); remaining -= len(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    # ---- статика UI --------------------------------------------------
    def _serve_static(self, head_only=False):
        root = _State.web_root
        if not root:
            self.send_error(404); return
        rel = unquote(urlparse(self.path).path).lstrip("/")
        if rel in ("", "index.html"):
            rel = "index.html"
        target = os.path.normpath(os.path.join(root, rel))
        if not target.startswith(os.path.normpath(root)) or not os.path.isfile(target):
            self.send_error(404); return
        data = open(target, "rb").read()
        self.send_response(200)
        self.send_header("Content-Type", _ctype(target))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if not head_only:
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass

    # ---- локальные картинки (превью лиц) ----------------------------
    def _serve_local(self, head_only=False):
        import tempfile
        from urllib.parse import parse_qs
        q = parse_qs(urlparse(self.path).query)
        p = (q.get("p") or [""])[0]
        p = os.path.normpath(p)
        allowed = [os.path.normpath(tempfile.gettempdir())]
        if _State.data_root:
            allowed.append(os.path.normpath(_State.data_root))
        if not p or not os.path.isfile(p) or not any(p.startswith(a) for a in allowed):
            self.send_error(404); return
        data = open(p, "rb").read()
        self.send_response(200)
        self.send_header("Content-Type", _ctype(p))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if not head_only:
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def _route(self, head_only=False):
        if self.path.startswith("/media/"):
            self._serve_media(head_only)
        elif self.path.startswith("/local"):
            self._serve_local(head_only)
        else:
            self._serve_static(head_only)

    def do_GET(self):
        self._route(False)

    def do_HEAD(self):
        self._route(True)


class MediaServer:
    def __init__(self):
        self._httpd = None
        self._thread = None
        self.port = 0

    def start(self, web_root=None) -> int:
        if web_root:
            _State.web_root = str(web_root)
        if self._httpd:
            return self.port
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.port = self._httpd.server_address[1]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        return self.port

    def base_url(self) -> str:
        self.start()
        return f"http://127.0.0.1:{self.port}"

    def index_url(self) -> str:
        return self.base_url() + "/index.html"

    def set_data_root(self, root):
        _State.data_root = str(root)

    def local_url(self, path: str) -> str:
        from urllib.parse import quote
        return f"{self.base_url()}/local?p={quote(str(path))}"

    def serve(self, path: str) -> str:
        self.start()
        _State.media_path = path
        return f"{self.base_url()}/media/{_State.token}"

    def stop(self):
        if self._httpd:
            self._httpd.shutdown(); self._httpd = None
