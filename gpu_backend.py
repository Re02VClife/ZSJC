# ====== Module: gpu_backend.py ======
# gpu_backend.py
"""
GPU 后端（OpenCV CUDA）：可用性探测、能力自检与正确的原语封装。

本模块只做三件事：
1. 探测 CUDA 是否可用（设备数 > 0 不代表能用，必须真实跑一遍）；
2. 能力自检：逐项执行差分、形态学、warpAffine、角点、光流，任何一项抛错
   就把它标记为不可用，而不是让异常在热路径里被静默吞掉；
3. 封装 GPU 原语，屏蔽 OpenCV Python 绑定的坑。

两个必须知道的绑定行为（本机 OpenCV 4.10.0 实测）：
- 多数 cv2.cuda 函数的 dst 输出参数**不会填充传入的 GpuMat**，而是返回新的
  GpuMat。正确写法是 `_, mask = cv2.cuda.threshold(...)`、
  `out = morph.apply(src)`。传 dst 只会得到空 Mat；继续对空 Mat 调
  apply/countNonZero 会触发 CUDA illegal memory access，让整个进程崩溃。
- SparsePyrLKOpticalFlow.calc 需要 4~6 个位置参数，返回
  (nextPts, status, err)；prevPts 必须是 1xN、CV_32FC2 的 GpuMat。

性能实测（RTX 3080，灰度图，Python 侧计时）：
| 操作                                   | CPU     | GPU     |
|----------------------------------------|---------|---------|
| 差分+阈值+闭运算+countNonZero 324x576   | 0.044ms | 0.80ms  |
| 完整运动分析（角点+光流+对齐）324x576   | 2.68ms  | 2.52ms  |
| 完整运动分析（角点+光流+对齐）540x960   | 8.14ms  | 3.18ms  |

结论：**小帧的简单差分不要上 GPU**（两次上传 + 一次 countNonZero 同步比
CPU 计算还贵），只有光流这类重计算在大帧上才划算。因此主流程只把
「角点检测 + 稀疏光流」放到 GPU，其余保持 CPU；阈值由
CONFIG["GPU_FLOW_MIN_PIXELS"] 控制（默认 20 万像素，约 540x370）。
"""
import os
import threading

import opencv_env  # noqa: F401  —— 导入即注册 DLL 搜索路径，必须先于 cv2
import cv2
import numpy as np

from config import CONFIG


class GPUUnavailable(RuntimeError):
    """CUDA 设备或 OpenCV CUDA 模块不可用。"""


class GPUInfo:
    """GPU 探测与自检结果。"""

    def __init__(self):
        self.available = False          # 设备数 > 0
        self.device_count = 0
        self.diff = False               # absdiff + threshold
        self.morph = False              # 形态学闭运算
        self.warp = False               # warpAffine
        self.corners = False            # GoodFeaturesToTrack
        self.flow = False               # SparsePyrLK
        self.errors = {}                # 失败项 -> 错误信息

    @property
    def flow_ready(self):
        """角点 + 光流 + 对齐可用，才允许走 GPU 运动分析。"""
        return self.available and self.corners and self.flow and self.warp

    @property
    def diff_ready(self):
        return self.available and self.diff and self.morph

    def summary(self):
        if not self.available:
            return f"GPU 不可用（设备数={self.device_count}）"
        if self.flow_ready:
            return f"GPU 可用（{self.device_count} 设备，光流就绪）"
        return f"GPU 部分可用（角点={self.corners} 光流={self.flow} 对齐={self.warp}）"

    def __repr__(self):
        return (f"<GPUInfo available={self.available} diff={self.diff} morph={self.morph} "
                f"warp={self.warp} corners={self.corners} flow={self.flow} errors={self.errors}>")


_probe_cache = None
_probe_lock = threading.Lock()


def _record(info, key, exc):
    info.errors[key] = f"{type(exc).__name__}: {exc}"


def _probe_impl():
    info = GPUInfo()
    try:
        info.device_count = int(cv2.cuda.getCudaEnabledDeviceCount())
    except Exception as exc:  # noqa: BLE001
        _record(info, "device", exc)
        return info
    if info.device_count <= 0:
        return info
    info.available = True

    # 自检图：32x48 太小容易没有角点，用 64x96
    rng = np.random.default_rng(0)
    base = (rng.random((72, 96)) * 255).astype(np.uint8)
    moved = base.copy()
    moved[16:48, 32:80] = 220

    g1, g2 = cv2.cuda_GpuMat(), cv2.cuda_GpuMat()
    try:
        g1.upload(base)
        g2.upload(moved)
    except Exception as exc:  # noqa: BLE001
        _record(info, "upload", exc)
        return info

    mask = None
    # ---- absdiff + threshold ----
    try:
        diff = cv2.cuda.absdiff(g1, g2)
        _, mask = cv2.cuda.threshold(diff, 20.0, 255.0, cv2.THRESH_BINARY)
        info.diff = bool(mask is not None and not mask.empty()
                         and cv2.cuda.countNonZero(mask) > 0)
    except Exception as exc:  # noqa: BLE001
        _record(info, "diff", exc)

    # ---- 形态学闭运算 ----
    try:
        if mask is not None and not mask.empty():
            f = cv2.cuda.createMorphologyFilter(
                cv2.MORPH_CLOSE, cv2.CV_8UC1, np.ones((3, 3), np.uint8))
            closed = f.apply(mask)
            expect = cv2.morphologyEx(mask.download(), cv2.MORPH_CLOSE,
                                      np.ones((3, 3), np.uint8))
            info.morph = bool(not closed.empty()
                              and np.array_equal(closed.download(), expect))
    except Exception as exc:  # noqa: BLE001
        _record(info, "morph", exc)

    # ---- warpAffine ----
    try:
        M = np.float32([[1, 0, 1.5], [0, 1, -1.0]])
        warped = cv2.cuda.warpAffine(g1, M, (96, 72), flags=cv2.INTER_LINEAR,
                                     borderMode=cv2.BORDER_REPLICATE)
        ref = cv2.warpAffine(base, M, (96, 72), flags=cv2.INTER_LINEAR,
                             borderMode=cv2.BORDER_REPLICATE)
        # 亚像素插值的定点舍入可能差 1 个灰阶，允许 ±1（像素级大幅偏离才算失败）
        if warped is not None and warped.size() == (96, 72):
            diff = np.abs(warped.download().astype(np.int16)
                          - ref.astype(np.int16))
            info.warp = bool(int(diff.max()) <= 1)
    except Exception as exc:  # noqa: BLE001
        _record(info, "warp", exc)

    # ---- 角点 ----
    pts = None
    try:
        det = cv2.cuda.createGoodFeaturesToTrackDetector(
            cv2.CV_8UC1, maxCorners=120, qualityLevel=0.01, minDistance=5.0)
        raw = det.detect(g2)
        if raw is not None and not raw.empty():
            pts = np.ascontiguousarray(
                raw.download().reshape(-1, 2).astype(np.float32))
            info.corners = len(pts) > 0
    except Exception as exc:  # noqa: BLE001
        _record(info, "corners", exc)

    # ---- 光流 ----
    if pts is not None and len(pts) > 0:
        try:
            lk = cv2.cuda.SparsePyrLKOpticalFlow.create(
                winSize=(21, 21), maxLevel=3, iters=30)
            gp1 = cv2.cuda_GpuMat()
            gp1.upload(pts.reshape(1, -1, 2))
            next_pts, status, _err = lk.calc(
                g1, g2, gp1, cv2.cuda_GpuMat(), cv2.cuda_GpuMat(), cv2.cuda_GpuMat())
            st = status.download() if status is not None else None
            info.flow = bool(st is not None and st.size > 0 and int(st.sum()) > 0)
        except Exception as exc:  # noqa: BLE001
            _record(info, "flow", exc)

    return info


def probe(force=False):
    """探测 GPU 能力（结果缓存；force=True 重新探测）。"""
    global _probe_cache
    with _probe_lock:
        if _probe_cache is None or force:
            _probe_cache = _probe_impl()
        return _probe_cache


def is_available():
    return probe().available


class GPUContext:
    """
    GPU 资源缓存与光流原语。

    设计要点：
    - 不持有自建 Stream：默认流下所有调用同步，避免 upload/download 与
      计算跨流竞态（本项目曾因此出现"数据没上传完就去算"的问题）。
    - 每帧上传的 CPU 图像引用会被保留，调用方可用 has_pair() 判断能否复用，
      避免同一对帧被重复上传。
    - 所有 GPU 调用只使用返回值，不依赖传入的 dst（见模块 docstring）。
    """

    def __init__(self, info=None):
        self.info = info or probe()
        if not self.info.available:
            raise GPUUnavailable("OpenCV CUDA 设备不可用")
        if not self.info.flow_ready:
            raise GPUUnavailable(
                "OpenCV CUDA 光流能力不完整："
                f"角点={self.info.corners} 光流={self.info.flow} 对齐={self.info.warp} "
                f"{self.info.errors}")

        self._prev = cv2.cuda_GpuMat()
        self._curr = cv2.cuda_GpuMat()
        self._aligned = cv2.cuda_GpuMat()
        self._aligned_mask = cv2.cuda_GpuMat()
        self._prev_src = None
        self._curr_src = None
        self._shape = None

        self._morph = None
        self._lk = None
        self._gftt = None
        # 光流点复用：上一轮 track 的落点在本轮 prev 上，可直接当角点用，
        # 省掉一次 GFTT（实测 1.33ms/帧）；每 N 帧强制重检避免漂移。
        self._cached_points = None
        self._cache_host = None
        self._reuse_left = 0
        # 对齐差分链共用一个 stream：warp → absdiff → threshold → morph →
        # countNonZero 全部异步提交，最后 waitForCompletion 一次，
        # 避免每步都同步（默认流下每个调用都是一次同步点）。
        self._stream = cv2.cuda.Stream()

    # ---------------- 帧上传 ----------------

    def upload_pair(self, prev, curr):
        """上传一对相邻帧；尺寸变化时重建 GpuMat 由 upload 自动处理。

        优化：如果上一轮的 curr 正好是本轮的 prev（正常逐帧检测都是这样），
        直接把两个 GpuMat 交换、只上传 curr，省掉一次 0.3MB 的上传。
        """
        if prev is None or curr is None:
            raise ValueError("upload_pair 需要两帧")
        if prev.shape != curr.shape:
            raise ValueError(f"两帧尺寸不一致: {prev.shape} vs {curr.shape}")
        if self._curr_src is prev and not self._curr.empty():
            self._prev, self._curr = self._curr, self._prev
            self._prev_src = prev
            self._curr.upload(np.ascontiguousarray(curr))
            self._curr_src = curr
        else:
            self._prev.upload(np.ascontiguousarray(prev))
            self._curr.upload(np.ascontiguousarray(curr))
            self._prev_src = prev
            self._curr_src = curr
        self._shape = curr.shape

    # 兼容旧调用方（extract.py / kazumi_processor.py 曾用分开上传）
    def upload_prev(self, prev):
        self._prev.upload(np.ascontiguousarray(prev))
        self._prev_src = prev
        if self._shape is None and self._curr_src is not None:
            self._shape = self._curr_src.shape

    def upload_curr(self, curr):
        self._curr.upload(np.ascontiguousarray(curr))
        self._curr_src = curr
        if self._shape is None and self._prev_src is not None:
            self._shape = self._prev_src.shape

    @property
    def lk(self):
        """兼容旧代码的 `ctx.lk` 写法。"""
        return self._lk_obj()

    def has_pair(self, prev, curr):
        """判断显存里是否正好是这一对帧（同一对象、同尺寸）。"""
        return (self._prev_src is prev and self._curr_src is curr
                and self._shape == curr.shape)

    @property
    def shape(self):
        return self._shape

    def ensure_pair(self, prev, curr):
        if not self.has_pair(prev, curr):
            self.upload_pair(prev, curr)

    # ---------------- 缓存对象 ----------------

    def _morph_filter(self):
        if self._morph is None:
            self._morph = cv2.cuda.createMorphologyFilter(
                cv2.MORPH_CLOSE, cv2.CV_8UC1, np.ones((3, 3), np.uint8))
        return self._morph

    def _lk_obj(self, lk_cuda=None):
        if lk_cuda is not None:
            return lk_cuda
        if self._lk is None:
            self._lk = cv2.cuda.SparsePyrLKOpticalFlow.create(
                winSize=(21, 21), maxLevel=3, iters=30)
        return self._lk

    def _gftt_obj(self):
        if self._gftt is None:
            self._gftt = cv2.cuda.createGoodFeaturesToTrackDetector(
                cv2.CV_8UC1,
                maxCorners=int(CONFIG["FLOW_FEATURE_COUNT"]),
                qualityLevel=float(CONFIG["FLOW_QUALITY_LEVEL"]),
                minDistance=float(CONFIG["FLOW_MIN_DISTANCE"]))
        return self._gftt

    # ---------------- 原语 ----------------

    def usable_for_flow(self, shape):
        """按像素数门槛判断是否值得走 GPU 光流（小帧 GPU 反而更慢）。"""
        if not self.info.flow_ready or shape is None:
            return False
        h, w = shape[:2]
        return h * w >= int(CONFIG.get("GPU_FLOW_MIN_PIXELS", 200_000))

    def corners(self, src="curr"):
        """GPU 角点检测，返回 (N,2) float32 ndarray 或 None。

        src="prev" 时如果上一轮光流的落点正好落在本帧 prev 上（同一 ndarray），
        直接复用那些点，省掉一次 GFTT；复用次数由 FLOW_POINT_REUSE_FRAMES 限制。
        """
        if (src == "prev" and self._cached_points is not None
                and self._cache_host is self._prev_src
                and self._reuse_left > 0):
            self._reuse_left -= 1
            return self._cached_points
        gmat = self._curr if src == "curr" else self._prev
        raw = self._gftt_obj().detect(gmat)
        if raw is None or raw.empty():
            return None
        pts = raw.download().reshape(-1, 2).astype(np.float32)
        return np.ascontiguousarray(pts) if len(pts) else None

    def track(self, p0, target="curr", lk_cuda=None):
        """
        prev -> target 的稀疏光流。
        p0: (N,2) 或 (1,N,2) float32；返回 (p1(N,2) float32, status(N,) uint8)。
        """
        p0 = np.ascontiguousarray(p0, dtype=np.float32).reshape(1, -1, 2)
        gp0 = cv2.cuda_GpuMat()
        gp0.upload(p0)
        next_img = self._curr if target == "curr" else self._aligned
        if next_img is None or next_img.empty():
            next_img = self._curr
        next_pts, status, _err = self._lk_obj(lk_cuda).calc(
            self._prev, next_img, gp0,
            cv2.cuda_GpuMat(), cv2.cuda_GpuMat(), cv2.cuda_GpuMat())
        p1 = next_pts.download().reshape(-1, 2).astype(np.float32)
        st = status.download().reshape(-1)
        if target == "curr" and int(np.sum(st == 1)) >= 20:
            # 落点在本轮 curr 上，下一轮它可以当 prev 的角点
            self._cached_points = p1
            self._cache_host = self._curr_src
            self._reuse_left = int(CONFIG.get("FLOW_POINT_REUSE_FRAMES", 0))
        return p1, st

    def warp_translate(self, dx, dy, use_stream=False):
        """对 curr 做平移对齐，结果存入 self._aligned（GPU 上完成）。"""
        h, w = self._shape
        M = np.float32([[1, 0, -dx], [0, 1, -dy]])
        if use_stream:
            self._aligned = cv2.cuda.warpAffine(
                self._curr, M, (w, h), flags=cv2.INTER_LINEAR,
                borderMode=cv2.BORDER_REPLICATE, stream=self._stream)
        else:
            self._aligned = cv2.cuda.warpAffine(
                self._curr, M, (w, h), flags=cv2.INTER_LINEAR,
                borderMode=cv2.BORDER_REPLICATE)
        return self._aligned

    def aligned_cpu(self):
        return None if self._aligned is None or self._aligned.empty() else self._aligned.download()

    def aligned_mask_cpu(self):
        return (None if self._aligned_mask is None or self._aligned_mask.empty()
                else self._aligned_mask.download())

    def aligned_change_ratio(self, threshold):
        """
        在 GPU 上完成「对齐帧 vs prev」的 absdiff → threshold → 闭运算 →
        countNonZero，整条链在同一个 stream 上异步提交，只在最后同步一次，
        返回 change_ratio（float）。

        相比原实现（warp + download 对齐帧 + CPU 差分）少一次 0.3MB 下载
        和三次 CPU 运算；纯平移帧（change_ratio 很小）可以据此直接返回，
        连掩膜都不需要下载。
        """
        if (self._prev is None or self._prev.empty()
                or self._aligned is None or self._aligned.empty()):
            raise ValueError("aligned_change_ratio: 显存中的帧为空")
        stream = self._stream
        diff = cv2.cuda.absdiff(self._prev, self._aligned, stream=stream)
        _, mask = cv2.cuda.threshold(
            diff, float(threshold), 255.0, cv2.THRESH_BINARY, stream=stream)
        closed = self._morph_filter().apply(mask, stream=stream)
        cnt = cv2.cuda.countNonZero(closed, cv2.cuda_GpuMat(), stream)
        stream.waitForCompletion()
        self._aligned_mask = closed
        h, w = self._shape
        val = cnt.download() if cnt is not None and not cnt.empty() else None
        if val is None:
            raise RuntimeError("aligned_change_ratio: countNonZero 未返回结果")
        return float(np.asarray(val).reshape(-1)[0]) / float(h * w)

    def diff_mask(self, threshold, first="prev", second="curr"):
        """
        计算 first 与 second 的差分掩膜。
        返回 (change_ratio, mask_gpu)；mask_gpu 留在显存，需要时再 download。
        """
        a = self._prev if first == "prev" else self._aligned
        b = self._curr if second == "curr" else self._aligned
        if a is None or a.empty() or b is None or b.empty():
            raise ValueError("diff_mask: 显存中的帧为空")
        diff = cv2.cuda.absdiff(a, b)
        _, mask = cv2.cuda.threshold(diff, float(threshold), 255.0, cv2.THRESH_BINARY)
        closed = self._morph_filter().apply(mask)
        h, w = self._shape
        ratio = cv2.cuda.countNonZero(closed) / float(h * w)
        return ratio, closed

    def aligned_diff(self, threshold):
        """prev 与对齐帧的差分（对齐帧在显存中，避免二次上传）。"""
        return self.diff_mask(threshold, first="prev", second="aligned")

    def download(self, gpu_mat):
        return gpu_mat.download()

    def release(self):
        # GpuMat 没有显式 release，置空并交给 GC / OpenCV 显存池
        self._prev = cv2.cuda_GpuMat()
        self._curr = cv2.cuda_GpuMat()
        self._aligned = cv2.cuda_GpuMat()
        self._prev_src = None
        self._curr_src = None
        self._shape = None
        self._morph = None
        self._lk = None
        self._gftt = None


# ---- 兼容旧接口 ----
# 注意：不要在 import 时执行 probe()。多进程批量提取时每个子进程都
# 初始化一次 CUDA 上下文会互相争抢，`--no-gpu` 也会白付这份开销
# （实测 8 进程批量时每进程 ~1GB 内存、启动慢数秒）。
def cuda_available():
    return is_available()
