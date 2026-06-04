# -*- coding: utf-8 -*-
"""Проверка и установка опциональных ML-зависимостей через managed runtime."""
from __future__ import annotations

import threading
from typing import Callable, List, Optional

from . import runtime


def check() -> list:
    """Статус каждой группы зависимостей для UI первого запуска."""
    return runtime.check_features()


def install(
    keys: List[str],
    on_progress: Optional[Callable[[dict], None]] = None,
    gpu: bool = False,
) -> dict:
    """Ставит выбранные группы в app-managed runtime через uv."""
    selected = list(keys or [])
    if gpu and "gpu" not in selected:
        selected.append("gpu")
    installer = runtime.RuntimeInstaller(on_progress=on_progress)
    return installer.install(selected)


def install_async(keys, on_progress, gpu=False):
    t = threading.Thread(target=install, args=(keys, on_progress, gpu), daemon=True)
    t.start()
    return t
