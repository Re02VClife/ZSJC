# ====== Module: opencv_env.py ======
# opencv_env.py
"""
OpenCV / CUDA 运行库搜索路径引导（必须在 import cv2 之前执行）。

为什么需要这一层：
- 本项目使用自编译的 OpenCV 4.10 CUDA 版（opencv_world4100.dll），
  它依赖 CUDA 11.8 运行库（cublas64_11 / cufft64_10 / npp* 等）。
- Python 3.8+ 不再用 PATH 搜索扩展模块的依赖 DLL，必须显式
  os.add_dll_directory()，否则会出现
  "ImportError: DLL load failed while importing cv2: 找不到指定的模块"。
- 打包成 exe 后 DLL 在 _internal/opencv_dlls/，路径与源码运行不同；
  换机器后 CUDA toolkit 版本也可能不是 v11.8。

本模块在 **import 时立即执行一次 setup()**，因此任何模块只要在
`import cv2` 之前 `import opencv_env` 即可。setup() 的结果保存在
_STATE 中，诊断脚本 / 设置界面可以读取实际加载到的目录。

可通过环境变量 ZSJC_OPENCV_DLL_DIR 追加自定义 DLL 目录（优先级最高）。
"""
import glob
import os
import re
import sys

_STATE = {
    "done": False,
    "dirs": [],          # 已成功加入搜索路径的目录
    "cuda_bin": None,    # 探测到的 CUDA bin 目录
    "errors": [],        # [(path, reason)]
}


def _version_key(path):
    """把 .../CUDA/v11.8 转成可比较的版本元组，取最高版本。"""
    m = re.search(r"v(\d+)\.(\d+)", os.path.basename(path))
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def _candidate_dirs():
    """按优先级返回候选 DLL 目录（保持顺序、去重）。"""
    seen, out = set(), []

    def add(path):
        if not path:
            return
        path = os.path.abspath(path)
        if path in seen or not os.path.isdir(path):
            return
        seen.add(path)
        out.append(path)

    # 0) 用户显式指定
    for p in os.environ.get("ZSJC_OPENCV_DLL_DIR", "").split(os.pathsep):
        add(p)

    # 1) 打包运行
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", "")
        add(os.path.join(meipass, "opencv_dlls"))
        add(meipass)
        add(os.path.join(os.path.dirname(sys.executable), "opencv_dlls"))

    # 2) 源码运行：项目目录下自带的 DLL
    project_dir = os.path.dirname(os.path.abspath(__file__))
    add(os.path.join(project_dir, "opencv_dlls"))
    add(project_dir)

    # 3) 当前解释器的 site-packages（venv 里放的是自编译 cv2.pyd + opencv_world4100.dll）
    add(os.path.join(sys.prefix, "Lib", "site-packages"))
    if getattr(sys, "base_prefix", sys.prefix) != sys.prefix:
        add(os.path.join(sys.base_prefix, "Lib", "site-packages"))

    # 4) CUDA 运行库：优先环境变量，其次扫描已安装的最高版本
    for var in ("CUDA_PATH", "CUDA_HOME"):
        root = os.environ.get(var)
        if root:
            add(os.path.join(root, "bin"))
    program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    cuda_root = os.path.join(program_files, "NVIDIA GPU Computing Toolkit", "CUDA")
    if os.path.isdir(cuda_root):
        for d in sorted(glob.glob(os.path.join(cuda_root, "v*")), key=_version_key, reverse=True):
            add(os.path.join(d, "bin"))

    return out


def setup(force=False):
    """注册 DLL 搜索目录（幂等）。返回 _STATE。"""
    if _STATE["done"] and not force:
        return _STATE
    _STATE["dirs"] = []
    _STATE["errors"] = []
    _STATE["cuda_bin"] = None

    if not hasattr(os, "add_dll_directory"):
        # 非 Windows 平台：保持原样即可
        _STATE["done"] = True
        return _STATE

    for d in _candidate_dirs():
        try:
            # 必须保留返回的 handle，否则目录会在 GC 后被移除
            _STATE.setdefault("_handles", []).append(os.add_dll_directory(d))
            _STATE["dirs"].append(d)
            base = os.path.basename(d).lower()
            if base == "bin" and "cuda" in d.lower():
                if _STATE["cuda_bin"] is None:
                    _STATE["cuda_bin"] = d
        except OSError as exc:
            _STATE["errors"].append((d, str(exc)))

    # 某些依赖（cuDNN / NVRTC 等）仍走 PATH，把 CUDA bin 前置一份
    cuda_bin = _STATE["cuda_bin"]
    if cuda_bin:
        path = os.environ.get("PATH", "")
        if cuda_bin.lower() not in path.lower():
            os.environ["PATH"] = cuda_bin + os.pathsep + path

    _STATE["done"] = True
    return _STATE


def describe():
    """人类可读的引导结果，诊断用。"""
    s = setup()
    lines = ["OpenCV DLL 搜索目录:"]
    lines += [f"  - {d}" for d in s["dirs"]]
    if s["errors"]:
        lines.append("失败目录:")
        lines += [f"  - {d}: {err}" for d, err in s["errors"]]
    lines.append(f"CUDA bin: {s['cuda_bin'] or '未找到'}")
    return "\n".join(lines)


# ---- 导入即执行：保证后续 import cv2 之前路径已就绪 ----
setup()
