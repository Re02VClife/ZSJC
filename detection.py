# ====== Module: detection.py ======
# detection.py
"""
动画作画张数检测模块。
包含感知哈希、自适应阈值、全局平移估计、图层分离、缩放检测、
局部运动判断、基础/完整检测等功能。

GPU 加速的真实范围（2026-11 实测）：
- 只把「角点检测 + 稀疏光流」放到 GPU：大帧上收益明显（540x960 差 2.5 倍）；
- 差分/形态学不上 GPU：小帧上两次 upload + countNonZero 同步比 CPU 计算还慢，
  主流程统一走 CPU（compute_diff_mask_gpu 仅保留给大分辨率离线任务和测试）；
- 所有 GPU 能力由 gpu_backend 逐项自检，任何一项不可用就自动走 CPU，
  不会出现"显示已启用 GPU，实际全是 CPU"的情况。
"""
import opencv_env  # noqa: F401  —— 必须先于 cv2 注册 DLL 搜索路径
import cv2
import numpy as np

from config import CONFIG
from gpu_backend import (
    GPUContext,
    GPUUnavailable,
    probe as gpu_probe,
    cuda_available,
)

__all__ = [
    "GPUContext", "GPUUnavailable", "gpu_probe", "cuda_available",
    "compute_hash", "compute_all_hashes", "adaptive_threshold",
    "compute_diff_mask", "compute_diff_mask_gpu", "is_raw_change",
    "fast_global_corr", "basic_is_new_cel", "has_local_motion",
    "unified_motion_analysis", "full_is_new_cel", "full_decide",
]

# GPU 光流的最小有效特征点数（低于此值说明画面纹理太少，结果不可信）
MIN_VALID_FLOW_POINTS = 20


# ======================== 基本工具 ========================

def compute_hash(gray_img):
    """
    使用感知哈希 (pHash) 计算 64 位哈希值，用于 OP/ED 匹配。
    流程：缩放 -> DCT -> 取左上角 8x8 低频系数 -> 二值化。
    返回整数 (64 位)。
    """
    resized = cv2.resize(gray_img, (32, 32), interpolation=cv2.INTER_AREA)
    dct = cv2.dct(np.float32(resized))
    dct_low = dct[:8, :8]
    avg = dct_low.mean()
    bits = dct_low > avg
    packed = np.packbits(bits.flatten())
    return int.from_bytes(packed.tobytes(), 'little')


def compute_all_hashes(gray_img):
    """
    一次缩放，同时返回 (hash_256, oped_hash)

    hash_256 : 256 位整数（不是 bool 数组）—— 重复帧比较直接做
               异或 + int.bit_count()，比 24 次 np.sum 数组比较快一个数量级。
    oped_hash: 64 位整数（用于 OP/ED 匹配）
    """
    resized_32 = cv2.resize(gray_img, (32, 32), interpolation=cv2.INTER_AREA)
    resized_16 = cv2.resize(resized_32, (16, 16), interpolation=cv2.INTER_AREA)
    avg_16 = resized_16.mean()
    packed = np.packbits((resized_16 > avg_16).flatten())
    hash_256 = int.from_bytes(packed.tobytes(), 'little')

    dct = cv2.dct(np.float32(resized_32))
    dct_low = dct[:8, :8]
    avg_oped = dct_low.mean()
    bits = dct_low > avg_oped
    packed_oped = np.packbits(bits.flatten())
    oped_hash = int.from_bytes(packed_oped.tobytes(), 'little')
    return hash_256, oped_hash


def hash_distance(h1, h2):
    """两个 256 位整数哈希的汉明距离。"""
    return (h1 ^ h2).bit_count()


def adaptive_threshold(frame):
    """根据帧的平均亮度计算自适应差分阈值"""
    mean_val = float(cv2.mean(frame)[0])
    t = CONFIG["MIN_DIFF_THRESHOLD"] + (mean_val / 255.0) * (
        CONFIG["MAX_DIFF_THRESHOLD"] - CONFIG["MIN_DIFF_THRESHOLD"])
    return max(CONFIG["MIN_DIFF_THRESHOLD"], min(CONFIG["MAX_DIFF_THRESHOLD"], t))


# ======================== 原始变化检测 ========================

def compute_diff_mask(prev, curr, threshold=None):
    """
    统一计算差分掩膜和变化率（一次 absdiff + 阈值 + 闭运算）。
    返回 (diff, mask, ratio)。threshold 可传入以避免重复计算均值。
    """
    if threshold is None:
        threshold = adaptive_threshold(curr)
    diff = cv2.absdiff(prev, curr)
    _, mask = cv2.threshold(diff, threshold, 255, cv2.THRESH_BINARY)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    ratio = cv2.countNonZero(mask) / mask.size
    return diff, mask, ratio


def compute_diff_mask_gpu(gpu_ctx, curr_mean=None):
    """
    GPU 版差分掩膜（保留给大分辨率离线任务/测试）。

    注意：在 324x576 这类实时检测尺寸上，GPU 版本实测 0.80ms，
    CPU 版本只要 0.044ms（两次上传 + countNonZero 同步的开销占主导），
    所以主流程**不使用**本函数。返回值保持 (diff_gpu, mask_gpu, ratio) 三元组
    以兼容既有调用方，其中 diff_gpu 恒为 None。
    """
    if curr_mean is None:
        src = gpu_ctx._curr_src  # 上传时保留的 CPU 帧引用
        curr_mean = float(np.mean(src)) if src is not None else 128.0
    t = CONFIG["MIN_DIFF_THRESHOLD"] + (curr_mean / 255.0) * (
        CONFIG["MAX_DIFF_THRESHOLD"] - CONFIG["MIN_DIFF_THRESHOLD"])
    threshold = max(CONFIG["MIN_DIFF_THRESHOLD"],
                    min(CONFIG["MAX_DIFF_THRESHOLD"], t))
    ratio, mask_gpu = gpu_ctx.diff_mask(threshold)
    return None, mask_gpu, ratio


def is_raw_change(prev, curr, precomputed_ratio=None):
    """原始变化检测（仅基于差分面积比），可接受预计算的 ratio 避免重复计算"""
    if precomputed_ratio is None:
        _, _, precomputed_ratio = compute_diff_mask(prev, curr)
    return precomputed_ratio >= CONFIG["MIN_CHANGE_RATIO"]


def fast_global_corr(img1, img2):
    """
    两幅等尺寸灰度图的归一化互相关系数（-1~1），常量图返回 1.0。

    曾经用 cv2.matchTemplate(TM_CCOEFF_NORMED) 实现，实测有两个问题：
    1. 同样尺寸下慢 2.3~2.8 倍（576x324: 2.6ms vs 1.1ms）；
    2. 常量图语义不稳定（576x324 全灰返回 0.0，而 numpy 版是 1.0），
       会把纯色/静止画面误判成完全不同。
    因此这里回到 numpy 实现，用 float32 控制内存带宽。
    """
    f1 = img1.astype(np.float32).ravel()
    f2 = img2.astype(np.float32).ravel()
    f1 -= f1.mean()
    f2 -= f2.mean()
    den = float(np.sqrt(np.dot(f1, f1) * np.dot(f2, f2)))
    if den < 1e-10:
        return 1.0
    return float(np.dot(f1, f2) / den)


def masked_corr(img1, img2, mask):
    """
    只在 mask 非零区域计算归一化互相关系数，语义等价于
    cv2.matchTemplate(img1, img2, TM_CCOEFF_NORMED, mask=mask)[0,0]，
    但快一个数量级以上。

    实测（768x405，mask 覆盖约 98% 像素）：
      matchTemplate + mask : 23 ms
      本函数               : 0.6 ms
    这是完整检测里最大的单点开销，换成 numpy 布尔索引后整体提速约 70%。
    """
    sel = mask if mask.dtype == bool else (mask > 0)
    n = int(np.count_nonzero(sel))
    if n < 16:
        return 1.0
    a = img1[sel]
    b = img2[sel]
    fa = a.astype(np.float32)
    fb = b.astype(np.float32)
    fa -= fa.mean()
    fb -= fb.mean()
    den = float(np.sqrt(np.dot(fa, fa) * np.dot(fb, fb)))
    if den < 1e-10:
        return 1.0
    return float(np.dot(fa, fb) / den)


# ======================== 基础检测 ========================

def basic_is_new_cel(prev, curr, precomputed_ratio=None):
    """
    基础检测，返回 (是否为新张, 移动类型描述)。

    等价化简（原逻辑有 4 个分支但只有一条返回 False 的路径）：
    - 变化率 < MIN_CHANGE_RATIO                       -> 静止
    - 变化率 >= BASIC_MIN_RAW_RATIO_STILL             -> 新作画
    - 变化率落在两者之间，才需要算相关系数判断是否极慢平移 -> 否则也是新作画
    这样大部分帧跳过 fast_global_corr（1ms 级），与原判定结果完全一致。
    """
    if precomputed_ratio is None:
        _, _, precomputed_ratio = compute_diff_mask(prev, curr)

    if precomputed_ratio < CONFIG["MIN_CHANGE_RATIO"]:
        return False, "静止"
    if precomputed_ratio >= CONFIG["BASIC_MIN_RAW_RATIO_STILL"]:
        return True, "新作画(基础)"
    if fast_global_corr(prev, curr) > CONFIG["BASIC_CORR_THRESHOLD"]:
        return False, "极慢平移/静止"
    return True, "新作画(基础)"


# ======================== 局部运动判断 ========================

def has_local_motion(binary_mask):
    """判断变化掩膜是否为局部运动（如口型）"""
    num_labels, labels, stats, _centroids = cv2.connectedComponentsWithStats(
        binary_mask, connectivity=8)
    if num_labels <= 1:
        return False
    areas = stats[1:, cv2.CC_STAT_AREA]
    if len(areas) == 0:
        return False
    max_area = float(np.max(areas))
    total_pixels = binary_mask.size
    if max_area / total_pixels < CONFIG["LOCAL_AREA_THRESH"]:
        return False

    # 所有前景点的联合包围盒（等价于原实现的全图 np.where，但直接取连通域统计）
    lefts = stats[1:, cv2.CC_STAT_LEFT]
    tops = stats[1:, cv2.CC_STAT_TOP]
    rights = lefts + stats[1:, cv2.CC_STAT_WIDTH]
    bottoms = tops + stats[1:, cv2.CC_STAT_HEIGHT]
    x_range = int(rights.max()) - int(lefts.min())
    y_range = int(bottoms.max()) - int(tops.min())
    bbox_area_ratio = (x_range * y_range) / total_pixels
    if bbox_area_ratio > CONFIG["LOCAL_BBOX_RATIO_MAX"]:
        return False

    max_idx = int(np.argmax(areas)) + 1
    w = int(stats[max_idx, cv2.CC_STAT_WIDTH])
    h = int(stats[max_idx, cv2.CC_STAT_HEIGHT])
    aspect_ratio = max(w, h) / (min(w, h) + 1)
    if aspect_ratio > CONFIG["LOCAL_ASPECT_RATIO_MAX"]:
        return False
    return True


# ======================== 运动分析原语（CPU / GPU 共享判定） ========================

def _track_cpu(prev, curr, p0):
    """CPU 稀疏光流，p0 为 (N,2)；返回 (p1(N,2), status(N,))。"""
    p1, status, _err = cv2.calcOpticalFlowPyrLK(
        prev, curr, p0.reshape(-1, 1, 2).astype(np.float32), None)
    if p1 is None or status is None:
        return None, None
    return p1.reshape(-1, 2).astype(np.float32), status.reshape(-1)


def _detect_corners_cpu(prev):
    corners = cv2.goodFeaturesToTrack(
        prev,
        maxCorners=int(CONFIG["FLOW_FEATURE_COUNT"]),
        qualityLevel=float(CONFIG["FLOW_QUALITY_LEVEL"]),
        minDistance=float(CONFIG["FLOW_MIN_DISTANCE"]),
        mask=None,
    )
    if corners is None:
        return None
    return np.ascontiguousarray(np.float32(corners).reshape(-1, 2))


def _estimate_global_shift(p0, p1, status):
    """由首段光流估计全局平移；返回 (has_shift, dx, dy)。"""
    if p1 is None or status is None:
        return False, 0.0, 0.0
    valid = status.reshape(-1) == 1
    if int(valid.sum()) < MIN_VALID_FLOW_POINTS:
        return False, 0.0, 0.0
    vecs = p1[valid] - p0[valid]
    dx = float(np.median(vecs[:, 0]))
    dy = float(np.median(vecs[:, 1]))
    if np.hypot(dx, dy) >= float(CONFIG.get("GLOBAL_SHIFT_MIN_PIXELS", 0.3)):
        return True, dx, dy
    return False, dx, dy


def _detect_layer_and_zoom(p0, p1, status, shape):
    """
    对齐后的残余运动判断：同向大位移 -> 图层分离运镜；
    径向一致且位移随半径增长 -> 缩放运镜。
    返回 (is_layer_move, is_zoom)。
    """
    is_layer_move = False
    is_zoom = False
    if p1 is None or status is None:
        return is_layer_move, is_zoom
    valid = status.reshape(-1) == 1
    if int(valid.sum()) < MIN_VALID_FLOW_POINTS:
        return is_layer_move, is_zoom

    vecs_res = p1[valid] - p0[valid]
    pts = p0[valid]
    norms_res = np.linalg.norm(vecs_res, axis=1)

    # ---- 图层分离 ----
    moving_mask = norms_res > CONFIG["FLOW_LAYER_STATIC_THRESH"]
    if int(np.sum(moving_mask)) >= CONFIG["LAYER_MIN_MOVING_POINTS"]:
        moving_vecs = vecs_res[moving_mask]
        mean_vec = np.mean(moving_vecs, axis=0)
        if np.linalg.norm(mean_vec) >= CONFIG["LAYER_MEAN_VEC_MIN"]:
            cos_sim = np.dot(moving_vecs, mean_vec) / (
                np.linalg.norm(moving_vecs, axis=1) * np.linalg.norm(mean_vec) + 1e-8)
            consistency = float(np.mean(cos_sim > CONFIG["LAYER_COS_SIM_THRESH"]))
            if consistency > CONFIG["LAYER_DIRECTION_CONSISTENCY"]:
                is_layer_move = True

    # ---- 缩放 ----
    h, w = shape[:2]
    center = np.array([w / 2, h / 2])
    radial_vecs = pts - center
    radial_norms = np.linalg.norm(radial_vecs, axis=1)
    far_mask = radial_norms > 5.0
    if int(np.sum(far_mask)) >= 10:
        final_mask = far_mask & (norms_res > 0.5)
        if int(np.sum(final_mask)) >= 10:
            radial_f = radial_vecs[final_mask]
            radial_n_f = radial_norms[final_mask]
            vec_n_f = norms_res[final_mask]

            radial_unit = radial_f / (radial_n_f[:, np.newaxis] + 1e-8)
            vec_unit = vecs_res[final_mask] / (vec_n_f[:, np.newaxis] + 1e-8)
            cos_sim_rad = np.abs(np.sum(vec_unit * radial_unit, axis=1))
            consistency_rad = float(np.mean(cos_sim_rad > 0.85))

            if consistency_rad >= CONFIG.get("ZOOM_DIRECTION_CONSISTENCY", 0.7):
                if len(vec_n_f) > 5:
                    corr = np.corrcoef(radial_n_f, vec_n_f)[0, 1]
                    if corr >= CONFIG.get("ZOOM_RADIAL_CORRELATION", 0.3):
                        is_zoom = True

    return is_layer_move, is_zoom


def unified_motion_analysis(prev, curr, use_optical_flow=True, use_gpu=False,
                            lk_cuda=None, gpu_ctx=None, frames_uploaded=False):
    """
    统一运动分析：一次角点 + 两次光流，检测全局平移、图层分离、缩放运镜。

    GPU 只接管「角点检测 + 光流」；对齐（warpAffine）在 CPU 完成，
    因为把对齐帧再下载回 CPU 的传输成本和 CPU 直接做几乎一样，
    省掉这一步能让 GPU 路径更简单、结果与 CPU 路径完全一致。

    frames_uploaded=True 表示调用方已经把这一对帧传进 gpu_ctx（避免二次上传）。
    返回: (has_shift, dx, dy, curr_aligned, is_layer_move, is_zoom)
    """
    shape = curr.shape
    # 是否值得用 GPU（像素数门槛 + 能力自检）
    gpu_ok = bool(use_gpu and gpu_ctx is not None
                  and gpu_ctx.usable_for_flow(shape))
    if gpu_ok and not frames_uploaded:
        try:
            gpu_ctx.upload_pair(prev, curr)
        except Exception:  # noqa: BLE001
            gpu_ok = False

    # ---------- 角点（必须在 prev 上检测，才能用于 prev -> curr 的光流） ----------
    p0 = None
    if gpu_ok:
        try:
            p0 = gpu_ctx.corners("prev")
        except Exception:  # noqa: BLE001
            p0 = None
            gpu_ok = False  # 角点失败说明 CUDA 链路有问题，后面直接走 CPU 更稳
    if p0 is None or len(p0) < MIN_VALID_FLOW_POINTS:
        p0 = _detect_corners_cpu(prev)
        gpu_ok = False if p0 is None else gpu_ok

    if p0 is None or len(p0) < 2:
        return False, 0.0, 0.0, curr, False, False

    # ---------- 第一次光流：prev -> curr ----------
    p1 = status = None
    if gpu_ok:
        try:
            p1, status = gpu_ctx.track(p0, target="curr", lk_cuda=lk_cuda)
        except Exception:  # noqa: BLE001
            p1 = status = None
            gpu_ok = False
    if p1 is None:
        p1, status = _track_cpu(prev, curr, p0)

    has_shift, dx, dy = _estimate_global_shift(p0, p1, status)

    # ---------- 对齐 ----------
    curr_aligned = curr
    if has_shift:
        h, w = shape[:2]
        M = np.float32([[1, 0, -dx], [0, 1, -dy]])
        curr_aligned = cv2.warpAffine(curr, M, (w, h),
                                      borderMode=cv2.BORDER_REPLICATE)

    if not use_optical_flow:
        return has_shift, dx, dy, curr_aligned, False, False

    # ---------- 第二次光流：prev -> curr_aligned ----------
    p1a = stata = None
    if gpu_ok and has_shift:
        # 只有真正做了对齐才有意义；未平移时直接复用第一次结果
        try:
            gpu_ctx.warp_translate(dx, dy)
            p1a, stata = gpu_ctx.track(p0, target="aligned", lk_cuda=lk_cuda)
        except Exception:  # noqa: BLE001
            p1a = stata = None
    if p1a is None:
        p1a, stata = _track_cpu(prev, curr_aligned, p0)

    is_layer_move, is_zoom = _detect_layer_and_zoom(p0, p1a, stata, shape)
    return has_shift, dx, dy, curr_aligned, is_layer_move, is_zoom


# ======================== 完整检测 ========================

def full_decide(prev, curr, motion, precomputed_ratio, use_optical_flow=True):
    """
    完整检测的 CPU 判定部分：输入 unified_motion_analysis 的结果，
    输出 (是否新张, 移动类型)。

    单独拆出来是为了让「GPU 运动分析」和「CPU 判定」可以跑在不同线程里
    （异步双缓冲），避免两边各写一套判定逻辑而慢慢分叉。
    """
    has_shift, _dx, _dy, curr_aligned, is_layer_move, is_zoom = motion
    if curr_aligned is None:
        curr_aligned = curr
    threshold = adaptive_threshold(curr)
    _diff, mask, change_ratio = compute_diff_mask(prev, curr_aligned, threshold)

    # ---- 全局平移过滤 ----
    if (precomputed_ratio >= CONFIG["MIN_CHANGE_RATIO"]
            and has_shift
            and change_ratio < CONFIG["ALIGNED_CHANGE_THRESHOLD"]):
        return False, "全局平移"

    if change_ratio >= CONFIG["ALIGNED_CHANGE_THRESHOLD"]:
        if has_local_motion(mask):
            return True, "新作画"
        if use_optical_flow and is_layer_move:
            return False, "图层分离运镜"
        if use_optical_flow and is_zoom:
            return False, "缩放运镜"
        if change_ratio >= CONFIG["SIGNIFICANT_CHANGE_RATIO"]:
            return True, "新作画"

    # ---- 剩余微小变化的补充检查：未变化区域的背景相似度 ----
    if change_ratio < CONFIG["SIGNIFICANT_CHANGE_RATIO"]:
        mask_inv = cv2.bitwise_not(mask)
        score = masked_corr(prev, curr_aligned, mask_inv)
        if score >= CONFIG["SSIM_THRESHOLD"]:
            return False, "局部变化(过滤)"

    return True, "新作画"


def is_full_still(prev, curr, precomputed_ratio):
    """完整检测的前置快速判断：极慢平移/静止。"""
    if precomputed_ratio >= CONFIG["FULL_STILL_RATIO"]:
        return False
    return fast_global_corr(prev, curr) > CONFIG["FULL_CORR_THRESHOLD"]


def full_is_new_cel(prev, curr, use_optical_flow, use_gpu=False, lk_cuda=None,
                    precomputed_ratio=None, gpu_ctx=None, frames_uploaded=False):
    """
    完整检测，返回 (是否为新张, 移动类型描述)
    使用统一运动分析做全局平移、图层分离、缩放检测，
    再对「对齐后差分」做局部运动 / 背景相似度判断。
    """
    if precomputed_ratio is None:
        _, _, precomputed_ratio = compute_diff_mask(prev, curr)

    if is_full_still(prev, curr, precomputed_ratio):
        return False, "极慢平移/静止"

    motion = unified_motion_analysis(
        prev, curr, use_optical_flow, use_gpu=use_gpu, lk_cuda=lk_cuda,
        gpu_ctx=gpu_ctx, frames_uploaded=frames_uploaded)
    return full_decide(prev, curr, motion, precomputed_ratio, use_optical_flow)
