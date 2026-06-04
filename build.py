#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Submind build helper.

Команды:
    python build.py install [--yes]   установить все зависимости (pip + проверки ffmpeg/ollama)
    python build.py build  [--onedir] собрать standalone .exe через PyInstaller
    python build.py run                запустить приложение из исходников
    python build.py clean              удалить build/, dist/, *.spec, __pycache__
    python build.py doctor             проверить окружение (python, ffmpeg, ollama, gpu)

Идея: один файл управляет жизненным циклом. На выходе build даёт dist/Submind.exe
(на Windows) который запускается двойным кликом.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent
APP_NAME = "Submind"
ENTRY = ROOT / "app" / "main.py"
REQUIREMENTS = ROOT / "requirements.txt"
WEB_DIR = ROOT / "web"
BUILD_META_DIR = ROOT / ".build-meta"
BUILD_INFO = BUILD_META_DIR / "build-info.json"

C_RESET = "\033[0m"
C_DIM = "\033[2m"
C_OK = "\033[92m"
C_WARN = "\033[93m"
C_ERR = "\033[91m"
C_HEAD = "\033[96m"


def say(msg, color=C_RESET):
    sys.stdout.write(f"{color}{msg}{C_RESET}\n")
    sys.stdout.flush()


def head(msg):
    say("\n" + "=" * 64, C_DIM)
    say("  " + msg, C_HEAD)
    say("=" * 64, C_DIM)


def run(cmd, check=True, env=None):
    say("  $ " + " ".join(str(c) for c in cmd), C_DIM)
    res = subprocess.run(cmd, env=env)
    if check and res.returncode != 0:
        raise SystemExit(f"Команда завершилась с кодом {res.returncode}")
    return res.returncode


def which(name):
    return shutil.which(name)


# --------------------------------------------------------------------------- #
#  install
# --------------------------------------------------------------------------- #
def cmd_install(args):
    head("Установка зависимостей Submind")

    if not REQUIREMENTS.exists():
        raise SystemExit("Не найден requirements.txt")

    if not args.yes:
        say("Будут установлены пакеты из requirements.txt в текущее окружение Python.")
        ans = input("Продолжить? [y/N] ").strip().lower()
        if ans not in ("y", "yes", "д", "да"):
            say("Отменено.", C_WARN)
            return

    say("Обновляю pip / setuptools / wheel...", C_HEAD)
    run([sys.executable, "-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel"])

    say("Устанавливаю зависимости проекта...", C_HEAD)
    run([sys.executable, "-m", "pip", "install", "-r", str(REQUIREMENTS)])

    # GPU-ускорение (опционально): пробуем поставить onnxruntime-gpu, не падаем при ошибке
    if args.gpu:
        say("Пробую поставить GPU-ускорение (onnxruntime-gpu)...", C_HEAD)
        run([sys.executable, "-m", "pip", "install", "onnxruntime-gpu"], check=False)

    head("Проверка внешних зависимостей")
    _check_ffmpeg()
    _check_ollama()

    say("\nГотово. Запуск: python build.py run", C_OK)


def _check_ffmpeg():
    if which("ffmpeg"):
        say("  ffmpeg найден: " + which("ffmpeg"), C_OK)
    else:
        say("  ffmpeg НЕ найден в PATH.", C_WARN)
        say("    Windows : winget install Gyan.FFmpeg  (или скачать с ffmpeg.org)", C_DIM)
        say("    macOS   : brew install ffmpeg", C_DIM)
        say("    Linux   : sudo apt install ffmpeg", C_DIM)


def _check_ollama():
    if which("ollama"):
        say("  ollama найден: " + which("ollama"), C_OK)
        try:
            out = subprocess.run(["ollama", "list"], capture_output=True, text=True, timeout=10)
            models = [l.split()[0] for l in out.stdout.splitlines()[1:] if l.strip()]
            qwen = [m for m in models if "qwen" in m.lower()]
            if qwen:
                say("  Найдены модели qwen: " + ", ".join(qwen), C_OK)
            else:
                say("  Модель qwen не найдена. Поставьте, например:", C_WARN)
                say("    ollama pull qwen2.5:7b-instruct", C_DIM)
        except Exception:
            pass
    else:
        say("  ollama НЕ найден. Конспекты/«Подробнее» не будут работать без него.", C_WARN)
        say("    Установка: https://ollama.com  затем  ollama pull qwen2.5:7b-instruct", C_DIM)


# --------------------------------------------------------------------------- #
#  build
# --------------------------------------------------------------------------- #
def _clean_build_artifacts():
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/IM", f"{APP_NAME}.exe", "/T"],
            capture_output=True, text=True,
        )
    targets = [ROOT / "build", ROOT / "dist", BUILD_META_DIR]
    targets += list(ROOT.glob("*.spec"))
    for p in targets:
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
            say("  удалён каталог " + str(p), C_DIM)
        elif p.exists():
            p.unlink()
            say("  удалён файл " + str(p), C_DIM)
    for pyc in ROOT.rglob("__pycache__"):
        shutil.rmtree(pyc, ignore_errors=True)


def _write_build_info():
    BUILD_META_DIR.mkdir(parents=True, exist_ok=True)
    build_id = time.strftime("%Y%m%d-%H%M%S")
    BUILD_INFO.write_text(
        json.dumps({"build_id": build_id, "app": APP_NAME}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return build_id


def cmd_build(args):
    head(f"Сборка {APP_NAME} в исполняемый файл")

    say("Очищаю следы предыдущей сборки...", C_HEAD)
    _clean_build_artifacts()
    build_id = _write_build_info()
    say(f"Новый build-id: {build_id}", C_DIM)

    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        say("PyInstaller не установлен. Ставлю...", C_WARN)
        run([sys.executable, "-m", "pip", "install", "pyinstaller"])

    sep = ";" if os.name == "nt" else ":"
    icon = ROOT / "web" / "assets" / "icon.ico"

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--name", APP_NAME,
        "--windowed",                       # без чёрной консоли
        "--collect-all", "webview",
        "--collect-all", "pip",
        "--add-data", f"{WEB_DIR}{sep}web",
        "--add-data", f"{BUILD_INFO}{sep}.",
    ]
    if not args.onedir:
        cmd.append("--onefile")
    if icon.exists():
        cmd += ["--icon", str(icon)]

    # Тяжёлые ML-зависимости ставятся мастером первого запуска в managed runtime
    # (%APPDATA%\Submind\runtime\.venv или платформенный аналог).
    # Так новый exe стартует чистым, а обновления не тащат хвосты старой сборки.
    for mod in ("faster_whisper", "ctranslate2", "onnxruntime", "cv2", "numpy", "sklearn", "insightface", "mediapipe", "torch"):
        cmd += ["--exclude-module", mod]
    cmd += ["--hidden-import", "pip._internal.cli.main"]
    cmd += ["--hidden-import", "qrcode.image.svg"]

    cmd.append(str(ENTRY))
    run(cmd)

    out = ROOT / "dist" / (APP_NAME + (".exe" if os.name == "nt" else ""))
    say(f"\nГотово. Исполняемый файл: {out}", C_OK)
    say("Первый запуск предложит скачать ML-пакеты и CUDA-ускорение, если найдена видеокарта.", C_DIM)


# --------------------------------------------------------------------------- #
#  run / clean / doctor
# --------------------------------------------------------------------------- #
def cmd_run(_args):
    head("Запуск Submind из исходников")
    run([sys.executable, str(ENTRY)])


def cmd_clean(_args):
    head("Очистка артефактов сборки")
    _clean_build_artifacts()
    say("Готово.", C_OK)


def cmd_doctor(_args):
    head("Диагностика окружения")
    say(f"  Python : {sys.version.split()[0]}  ({sys.executable})")
    _check_ffmpeg()
    _check_ollama()
    try:
        import onnxruntime as ort
        say("  onnxruntime providers: " + ", ".join(ort.get_available_providers()), C_OK)
    except Exception:
        say("  onnxruntime не установлен (распознавание лиц недоступно).", C_WARN)
    try:
        import torch
        say("  torch CUDA: " + ("да" if torch.cuda.is_available() else "нет"), C_DIM)
    except Exception:
        pass


def main():
    parser = argparse.ArgumentParser(prog="build.py", description="Submind build helper")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_inst = sub.add_parser("install", help="установить зависимости")
    p_inst.add_argument("--yes", action="store_true", help="без подтверждения")
    p_inst.add_argument("--gpu", action="store_true", help="поставить onnxruntime-gpu")
    p_inst.set_defaults(func=cmd_install)

    p_build = sub.add_parser("build", help="собрать .exe")
    p_build.add_argument("--onedir", action="store_true", help="папка вместо одного файла")
    p_build.set_defaults(func=cmd_build)

    sub.add_parser("run", help="запустить из исходников").set_defaults(func=cmd_run)
    sub.add_parser("clean", help="очистить артефакты").set_defaults(func=cmd_clean)
    sub.add_parser("doctor", help="проверить окружение").set_defaults(func=cmd_doctor)

    args = parser.parse_args()
    try:
        args.func(args)
    except KeyboardInterrupt:
        say("\nПрервано.", C_WARN)


if __name__ == "__main__":
    main()
