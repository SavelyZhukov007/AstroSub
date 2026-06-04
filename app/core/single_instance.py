# -*- coding: utf-8 -*-
"""Single instance guard and stale Submind process cleanup."""
from __future__ import annotations

import os
import socket
import subprocess
import sys
from dataclasses import dataclass

LOCK_PORT = 41593


@dataclass
class InstanceGuard:
    socket: socket.socket | None = None

    def release(self):
        if self.socket:
            try:
                self.socket.close()
            except OSError:
                pass
            self.socket = None


def ensure_single_instance(app_name: str = "Submind") -> InstanceGuard:
    """Offer to close existing Submind processes, then acquire a local socket lock."""
    current = os.getpid()
    others = find_other_processes(app_name, current)
    if others and confirm_close_existing(app_name, len(others)):
        close_processes(others)

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(("127.0.0.1", LOCK_PORT))
        sock.listen(1)
        return InstanceGuard(sock)
    except OSError:
        show_message(app_name, "Submind уже запущен. Закройте старый экземпляр и попробуйте снова.")
        raise SystemExit(0)


def find_other_processes(app_name: str, current_pid: int) -> list[int]:
    if os.name == "nt":
        return _find_windows(app_name, current_pid)
    return _find_posix(app_name, current_pid)


def _find_windows(app_name: str, current_pid: int) -> list[int]:
    script = (
        "Get-CimInstance Win32_Process | "
        f"Where-Object {{ $_.ProcessId -ne {current_pid} -and "
        f"($_.Name -ieq '{app_name}.exe' -or $_.CommandLine -match 'app\\\\main.py') }} | "
        "Select-Object -ExpandProperty ProcessId"
    )
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True, text=True, timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return [int(x) for x in out.stdout.split() if x.isdigit()]
    except Exception:
        return []


def _find_posix(app_name: str, current_pid: int) -> list[int]:
    try:
        out = subprocess.run(["ps", "-eo", "pid=,comm=,args="], capture_output=True, text=True, timeout=5)
    except Exception:
        return []
    pids = []
    needle = app_name.lower()
    for line in out.stdout.splitlines():
        parts = line.strip().split(None, 2)
        if not parts or not parts[0].isdigit():
            continue
        pid = int(parts[0])
        if pid == current_pid:
            continue
        text = line.lower()
        if needle in text or "app/main.py" in text:
            pids.append(pid)
    return pids


def close_processes(pids: list[int]) -> None:
    if not pids:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/T", *sum((["/PID", str(pid)] for pid in pids), [])],
            capture_output=True, text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    else:
        for pid in pids:
            try:
                os.kill(pid, 15)
            except OSError:
                pass


def confirm_close_existing(app_name: str, count: int) -> bool:
    text = (
        f"Найдено других процессов {app_name}: {count}.\n\n"
        "Закрыть их и продолжить запуск текущего приложения?"
    )
    if os.name == "nt":
        try:
            import ctypes
            return ctypes.windll.user32.MessageBoxW(None, text, app_name, 0x24) == 6
        except Exception:
            return False
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        ok = messagebox.askyesno(app_name, text)
        root.destroy()
        return ok
    except Exception:
        pass
    try:
        return input(text + " [y/N] ").strip().lower() in ("y", "yes", "д", "да")
    except Exception:
        return False


def show_message(title: str, text: str) -> None:
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, text, title, 0x40)
            return
        except Exception:
            pass
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        messagebox.showinfo(title, text)
        root.destroy()
        return
    except Exception:
        pass
    sys.stderr.write(text + "\n")
