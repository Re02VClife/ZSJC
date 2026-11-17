# -*- mode: python ; coding: utf-8 -*-
# SPECPATH = PyInstaller 内置变量，指向 spec 文件所在目录的绝对路径
import os

# 自编译 OpenCV CUDA DLL（opencv_dlls 目录下的 DLL 需要随 exe 分发）
_opencv_dll_dir = os.path.join(SPECPATH, 'opencv_dlls')
_opencv_dlls = []
if os.path.isdir(_opencv_dll_dir):
    for _f in os.listdir(_opencv_dll_dir):
        if _f.endswith('.dll'):
            _opencv_dlls.append((os.path.join(_opencv_dll_dir, _f), 'opencv_dlls'))

# 数据文件
_datas = []
for _df in ['cel_counter_settings.json', 'cel_counter_records.json']:
    _p = os.path.join(SPECPATH, _df)
    if os.path.isfile(_p):
        _datas.append((_p, '.'))

a = Analysis(
    ['main.py'],
    pathex=[SPECPATH],
    binaries=_opencv_dlls,
    datas=_datas,
    hiddenimports=[
        'opencv_env',
        'gpu_backend',
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
    name='张数计数器',
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

# 过滤掉 opencv-python 自带的大体积 CUDA 库（光流+图像处理不需要
# cuBLAS/cuFFT/NPP 等）。注意：自编译的 opencv_world4100.dll 本身依赖这些
# 库，所以只有在 opencv_dlls 里放的是完整 CUDA 构建、且目标是本机运行时，
# 才能安全过滤；跨机器分发请保留，否则会缺少 DLL。
_unneeded_cuda_prefixes = (
    'cublas', 'cufft', 'cudnn', 'curand', 'cusparse', 'cusolver',
    'nvrtc', 'nvjpeg',
    'nppc', 'nppial', 'nppicc', 'nppidei', 'nppif', 'nppig',
    'nppim', 'nppist', 'nppitc',
)
# 自定义 opencv_dlls 已在 _internal/opencv_dlls/ 中，避免在 _internal/ 根目录重复
_custom_opencv_dlls = {'opencv_world4100.dll', 'opencv_img_hash4100.dll',
                       'opencv_videoio_ffmpeg4100_64.dll'}
_filtered_binaries = []
for _b in a.binaries:
    # _b is a TOC entry: (dest_name, src_path, typecode)
    _fname = os.path.basename(_b[0])
    _in_custom_dir = _b[0].startswith('opencv_dlls')
    # 只过滤 opencv-python 自动收集到根目录的那批 CUDA 库；
    # opencv_dlls/ 下是自编译 OpenCV 的运行时依赖，跨机器分发必须保留。
    if _fname.lower().startswith(_unneeded_cuda_prefixes) and not _in_custom_dir:
        continue
    # 跳过已在 opencv_dlls/ 子目录中的同名 DLL（避免重复打包 210MB+）
    if _fname.lower() in _custom_opencv_dlls and not _in_custom_dir:
        continue
    _filtered_binaries.append(_b)

coll = COLLECT(
    exe,
    _filtered_binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=['opencv_*.dll'],
    name='张数计数器',
)
