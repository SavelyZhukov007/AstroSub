# -*- coding: utf-8 -*-
"""Точка входа Submind."""
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from app import config
    config.bootstrap_runtime_packages()
    from app.api import Api
    from app.core.single_instance import ensure_single_instance
else:
    from . import config
    config.bootstrap_runtime_packages()
    from .api import Api
    from .core.single_instance import ensure_single_instance

import webview


def main():
    guard = ensure_single_instance("Submind")
    api = Api()
    # поднимаем локальный сервер: статика UI + медиа по http (безопасный контекст
    # для камеры/микрофона и корректное проигрывание видео)
    try:
        api._server.start(web_root=config.web_dir())
        url = api._server.index_url()
    except Exception:
        url = str(config.web_dir() / "index.html")

    webview.create_window(
        title="Submind — субтитры и эмоции",
        url=url,
        js_api=api,
        width=1360,
        height=880,
        min_size=(1040, 680),
        background_color="#16130F",
        text_select=True,
    )
    try:
        webview.start(debug=False)
    finally:
        guard.release()


if __name__ == "__main__":
    main()
