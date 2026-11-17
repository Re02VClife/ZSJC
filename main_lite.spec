# -*- mode: python ; coding: utf-8 -*-
# 轻量版 spec：CPU-only（官方 PyPI opencv-python），不打包自编译的 CUDA DLL。
# 用途：给没有 NVIDIA 显卡 / 不想下载 1.4GB 包的用户。
import os

# 数据文件（用户设置与运行记录）
_datas = []
for _df in ['cel_counter_settings.json', 'cel_counter_records.json']:
    _p = os.path.join(SPECPATH, _df)
    if os.path.isfile(_p):
        _datas.append((_p, '.'))

a = Analysis(
    ['main.py'],
    pathex=[SPECPATH],
    binaries=[],
    datas=_datas,
    hiddenimports=[
        'opencv_env',
        'gpu_backend',
        'async_pipeline',
        'cv2',
        'PIL',
        'dxcam',
        'numpy',
        'tkinter',
        'tkinter.filedialog',
        'tkinter.simpledialog',
        'tkinter.messagebox',
        'tkinter.colorchooser',
        'tkinter.ttk',
        'json',
        'csv',
        'threading',
        'queue',
        'collections',
        'copy',
        'subprocess',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'matplotlib',
        'scipy',
        'pandas',
        'tornado',
        'snakeviz',
        'viztracer',
        'objprint',
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='张数计数器-轻量版',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=[os.path.join(SPECPATH, 'app.ico')],
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    name='张数计数器-轻量版',
)
