# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = [('C:\\Projects\\sub\\AstroSub\\web', 'web'), ('C:\\Projects\\sub\\AstroSub\\.build-meta\\build-info.json', '.'), ('C:\\Projects\\sub\\AstroSub\\models', 'models')]
binaries = [('C:\\Users\\savel\\AppData\\Local\\Programs\\Python\\Python310\\python3.dll', '.'), ('C:\\Users\\savel\\AppData\\Local\\Programs\\Python\\Python310\\vcruntime140.dll', '.'), ('C:\\Users\\savel\\AppData\\Local\\Programs\\Python\\Python310\\vcruntime140_1.dll', '.')]
hiddenimports = ['pip._internal.cli.main', 'fileinput']
tmp_ret = collect_all('webview')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('pip')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['C:\\Projects\\sub\\AstroSub\\app\\main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['faster_whisper', 'ctranslate2', 'cv2', 'numpy', 'openvino', 'torch'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='Submind',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['C:\\Projects\\sub\\AstroSub\\web\\assets\\icon.ico'],
)
