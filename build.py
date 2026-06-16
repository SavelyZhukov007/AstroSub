#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Submind build helper.

Команды:
    python build.py go                скачать модели/зависимости и собрать Submind.exe
    python build.py install [--yes]   установить зависимости Python
    python build.py build  [--onedir] собрать standalone .exe через PyInstaller
    python build.py run                запустить приложение из исходников
    python build.py clean              удалить build/, dist/, *.spec, __pycache__
    python build.py doctor             проверить окружение (python, ffmpeg, EmotionAI)

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
import urllib.request
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
MODELS_DIR = ROOT / "models"
BUILD_META_DIR = ROOT / ".build-meta"
BUILD_INFO = BUILD_META_DIR / "build-info.json"
ROOT_EXE = ROOT / (APP_NAME + (".exe" if os.name == "nt" else ""))

EMOTION_MODEL_FILES = [
    (
        "emotion/intel/face-detection-retail-0004/FP16/face-detection-retail-0004.xml",
        "https://storage.openvinotoolkit.org/repositories/open_model_zoo/2022.3/models_bin/1/face-detection-retail-0004/FP16/face-detection-retail-0004.xml",
    ),
    (
        "emotion/intel/face-detection-retail-0004/FP16/face-detection-retail-0004.bin",
        "https://storage.openvinotoolkit.org/repositories/open_model_zoo/2022.3/models_bin/1/face-detection-retail-0004/FP16/face-detection-retail-0004.bin",
    ),
    (
        "emotion/intel/emotions-recognition-retail-0003/FP16/emotions-recognition-retail-0003.xml",
        "https://storage.openvinotoolkit.org/repositories/open_model_zoo/2022.3/models_bin/1/emotions-recognition-retail-0003/FP16/emotions-recognition-retail-0003.xml",
    ),
    (
        "emotion/intel/emotions-recognition-retail-0003/FP16/emotions-recognition-retail-0003.bin",
        "https://storage.openvinotoolkit.org/repositories/open_model_zoo/2022.3/models_bin/1/emotions-recognition-retail-0003/FP16/emotions-recognition-retail-0003.bin",
    ),
]

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


def copy_file(src: Path, dst: Path):
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    say(f"  скопирован {src} -> {dst}", C_DIM)


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

    say("Кладу runtime-библиотеки в корневую packages/...", C_HEAD)
    packages = ROOT / "packages"
    if packages.exists():
        shutil.rmtree(packages)
    packages.mkdir(parents=True, exist_ok=True)
    run([
        sys.executable, "-m", "pip", "install",
        "--upgrade",
        "--target", str(packages),
        "-r", str(REQUIREMENTS),
    ])

    head("Проверка внешних зависимостей")
    _check_ffmpeg()
    ensure_emotion_models()

    say("\nГотово. Запуск: python build.py run", C_OK)


def _check_ffmpeg():
    if which("ffmpeg"):
        say("  ffmpeg найден: " + which("ffmpeg"), C_OK)
    else:
        say("  ffmpeg НЕ найден в PATH.", C_WARN)
        say("    Windows : winget install Gyan.FFmpeg  (или скачать с ffmpeg.org)", C_DIM)
        say("    macOS   : brew install ffmpeg", C_DIM)
        say("    Linux   : sudo apt install ffmpeg", C_DIM)


def ensure_emotion_models():
    head("Проверка моделей EmotionAI")
    for rel, url in EMOTION_MODEL_FILES:
        dst = MODELS_DIR / rel
        if dst.exists() and dst.stat().st_size > 0:
            say("  есть " + str(dst.relative_to(ROOT)), C_OK)
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        say("  скачиваю " + str(dst.relative_to(ROOT)), C_HEAD)
        try:
            urllib.request.urlretrieve(url, dst)
        except Exception as exc:
            if dst.exists():
                dst.unlink(missing_ok=True)
            raise SystemExit(f"Не удалось скачать {url}: {exc}") from exc
    say("  модели EmotionAI готовы", C_OK)


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
    if MODELS_DIR.exists():
        cmd += ["--add-data", f"{MODELS_DIR}{sep}models"]
    if not args.onedir:
        cmd.append("--onefile")
    if icon.exists():
        cmd += ["--icon", str(icon)]

    # Тяжёлые ML-зависимости ставятся в окружение/managed runtime рядом с проектом,
    # а не запекаются в bootloader.
    for mod in ("faster_whisper", "ctranslate2", "cv2", "numpy", "openvino", "torch"):
        cmd += ["--exclude-module", mod]
    cmd += ["--hidden-import", "pip._internal.cli.main"]
    cmd += ["--hidden-import", "fileinput"]

    cmd.append(str(ENTRY))
    run(cmd)

    out = ROOT / "dist" / (APP_NAME + (".exe" if os.name == "nt" else ""))
    say(f"\nГотово. Исполняемый файл: {out}", C_OK)
    say("Для запуска рядом с корневыми web/ и models используйте python build.py go.", C_DIM)


def cmd_go(args):
    head("Полная подготовка Submind")
    args.yes = True
    args.gpu = False
    cmd_install(args)
    ensure_emotion_models()
    args.onedir = False
    cmd_build(args)
    built = ROOT / "dist" / (APP_NAME + (".exe" if os.name == "nt" else ""))
    if built.exists():
        copy_file(built, ROOT_EXE)
        say(f"\nГотово. Запускайте: {ROOT_EXE}", C_OK)


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
    ensure_emotion_models()
    try:
        import openvino as _openvino  # noqa: F401
        say("  openvino установлен", C_OK)
    except Exception:
        say("  openvino не установлен", C_WARN)


def main():
    parser = argparse.ArgumentParser(prog="build.py", description="Submind build helper")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("go", help="скачать модели/зависимости и собрать exe").set_defaults(func=cmd_go)

    p_inst = sub.add_parser("install", help="установить зависимости")
    p_inst.add_argument("--yes", action="store_true", help="без подтверждения")
    p_inst.add_argument("--gpu", action="store_true", help=argparse.SUPPRESS)
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
