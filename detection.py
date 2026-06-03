# detection.py
"""
动画作画张数检测模块。
包含感知哈希、自适应阈值、全局平移估计、图层分离、缩放检测、
局部运动判断、基础/完整检测等功能。
"""

import cv2
import numpy as np
from config import CONFIG

# ======================== 基本工具 ========================

def compute_hash(gray_img):
    """计算感知哈希（16x16）"""
    resized = cv2.resize(gray_img, (16, 16), interpolation=cv2.INTER_AREA)
    avg = resized.mean()
    return (resized > avg).flatten()



def adaptive_threshold(frame):
    """根据帧的平均亮度计算自适应差分阈值"""
    mean_val = np.mean(frame)
    t = CONFIG["MIN_DIFF_THRESHOLD"] + (mean_val / 255.0) * (CONFIG["MAX_DIFF_THRESHOLD"] - CONFIG["MIN_DIFF_THRESHOLD"])
    return max(CONFIG["MIN_DIFF_THRESHOLD"], min(CONFIG["MAX_DIFF_THRESHOLD"], t))





# ======================== 原始变化检测 ========================

def is_raw_change(prev, curr):
    """原始变化检测（仅基于差分面积比）"""
    diff = cv2.absdiff(prev, curr)
    thresh = adaptive_threshold(curr)
    _, mask = cv2.threshold(diff, thresh, 255, cv2.THRESH_BINARY)
    kernel = np.ones((3, 3), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    ratio = np.count_nonzero(mask) / mask.size
    return ratio >= CONFIG["MIN_CHANGE_RATIO"]



def fast_global_corr(img1, img2):
    """快速计算两幅等尺寸灰度图的整体归一化相关系数（-1~1）"""
    f1 = img1.astype(np.float64).ravel()
    f2 = img2.astype(np.float64).ravel()
    m1 = np.mean(f1)
    m2 = np.mean(f2)
    num = np.dot(f1 - m1, f2 - m2)
    den = np.sqrt(np.dot(f1 - m1, f1 - m1) * np.dot(f2 - m2, f2 - m2))
    if den < 1e-10:
        return 1.0
    return num / den
# ======================== 基础检测 ========================

def basic_is_new_cel(prev, curr):
    """基础检测，返回 (是否为新张, 移动类型描述)"""
    corr = fast_global_corr(prev, curr)   # 替换模板匹配

    if corr > CONFIG["BASIC_CORR_THRESHOLD"]:
        raw_diff = cv2.absdiff(prev, curr)
        raw_thresh = adaptive_threshold(curr)
        _, raw_mask = cv2.threshold(raw_diff, raw_thresh, 255, cv2.THRESH_BINARY)
        raw_ratio = np.count_nonzero(raw_mask) / raw_mask.size
        if raw_ratio < CONFIG["BASIC_MIN_RAW_RATIO_STILL"]:
            return False, "极慢平移/静止"
        if raw_ratio >= CONFIG["SIGNIFICANT_CHANGE_RATIO"]:
            return True, "新作画(基础)"
        else:
            return True, "新作画(基础)"

    diff_thresh = adaptive_threshold(curr)
    raw_diff = cv2.absdiff(prev, curr)
    _, raw_mask = cv2.threshold(raw_diff, diff_thresh, 255, cv2.THRESH_BINARY)
    kernel = np.ones((3, 3), np.uint8)
    raw_mask = cv2.morphologyEx(raw_mask, cv2.MORPH_CLOSE, kernel)
    raw_ratio = np.count_nonzero(raw_mask) / raw_mask.size

    if raw_ratio < CONFIG["MIN_CHANGE_RATIO"]:
        return False, "静止"
    if raw_ratio >= CONFIG["SIGNIFICANT_CHANGE_RATIO"]:
        return True, "新作画(基础)"
    return True, "新作画(基础)"

# ======================== 局部运动判断 ========================

def has_local_motion(binary_mask):
    """判断变化掩膜是否为局部运动（如口型）"""
    num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(binary_mask, connectivity=8)
    if num_labels <= 1:
        return False
    areas = stats[1:, cv2.CC_STAT_AREA]
    if len(areas) == 0:
        return False
    max_area = np.max(areas)
    total_pixels = binary_mask.size
    if max_area / total_pixels < CONFIG["LOCAL_AREA_THRESH"]:
        return False
    ys, xs = np.where(binary_mask > 0)
    if len(xs) < 20:
        return False
    x_range = np.max(xs) - np.min(xs)
    y_range = np.max(ys) - np.min(ys)
    bbox_area_ratio = (x_range * y_range) / total_pixels
    if bbox_area_ratio > CONFIG["LOCAL_BBOX_RATIO_MAX"]:
        return False
    max_label = np.argmax(areas) + 1
    max_mask = (labels == max_label).astype(np.uint8)
    contours, _ = cv2.findContours(max_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if contours:
        cnt = max(contours, key=cv2.contourArea)
        x, y, w, h = cv2.boundingRect(cnt)
        aspect_ratio = max(w, h) / (min(w, h) + 1)
        if aspect_ratio > CONFIG["LOCAL_ASPECT_RATIO_MAX"]:
            return False
    return True

# ======================== 统一运动分析（一次角点，两次光流） ========================

def unified_motion_analysis(prev, curr, use_optical_flow=True):
    """
    一次角点提取 + 两次光流追踪，同时完成：
      - 全局平移估计（返回对齐帧）
      - 图层分离运镜检测
      - 缩放运镜检测
    返回: (has_shift, dx, dy, curr_aligned, is_layer_move, is_zoom)
    """
    max_corners = 200
    corners = cv2.goodFeaturesToTrack(
        prev,
        maxCorners=max_corners,
        qualityLevel=CONFIG["FLOW_QUALITY_LEVEL"],
        minDistance=CONFIG["FLOW_MIN_DISTANCE"],
        mask=None
    )

    # 默认值
    has_shift = False
    dx, dy = 0.0, 0.0
    curr_aligned = curr
    is_layer_move = False
    is_zoom = False

    if corners is None:
        return has_shift, dx, dy, curr_aligned, is_layer_move, is_zoom

    p1 = np.float32(corners).reshape(-1, 2)

    # 第一次追踪：prev -> curr，估计全局平移
    p2_curr, status_curr, _ = cv2.calcOpticalFlowPyrLK(prev, curr, p1, None)
    if p2_curr is not None and np.sum(status_curr) >= 20:
        valid = status_curr.flatten() == 1
        vecs_curr = p2_curr[valid] - p1[valid]
        dx = np.median(vecs_curr[:, 0])
        dy = np.median(vecs_curr[:, 1])
        if np.sqrt(dx*dx + dy*dy) >= 0.3:
            has_shift = True
            h, w = curr.shape
            M = np.float32([[1, 0, -dx], [0, 1, -dy]])
            curr_aligned = cv2.warpAffine(curr, M, (w, h), borderMode=cv2.BORDER_REPLICATE)
    if not use_optical_flow:
        return has_shift, dx, dy, curr_aligned, False, False

    # 第二次追踪：prev -> curr_aligned，分析残余运动（图层分离 + 缩放）
    p2_aligned, status_aligned, _ = cv2.calcOpticalFlowPyrLK(prev, curr_aligned, p1, None)
    if p2_aligned is not None and np.sum(status_aligned) >= 20:
        valid = status_aligned.flatten() == 1
        vecs_res = p2_aligned[valid] - p1[valid]
        pts = p1[valid]
        norms_res = np.linalg.norm(vecs_res, axis=1)

        # ----- 图层分离检测 -----
        moving_mask = norms_res > CONFIG["FLOW_LAYER_STATIC_THRESH"]
        if np.sum(moving_mask) >= CONFIG["LAYER_MIN_MOVING_POINTS"]:
            moving_vecs = vecs_res[moving_mask]
            mean_vec = np.mean(moving_vecs, axis=0)
            if np.linalg.norm(mean_vec) >= CONFIG["LAYER_MEAN_VEC_MIN"]:
                cos_sim = np.dot(moving_vecs, mean_vec) / (
                    np.linalg.norm(moving_vecs, axis=1) * np.linalg.norm(mean_vec) + 1e-8
                )
                consistency = np.mean(cos_sim > CONFIG["LAYER_COS_SIM_THRESH"])
                if consistency > CONFIG["LAYER_DIRECTION_CONSISTENCY"]:
                    is_layer_move = True

        # ----- 缩放检测 -----
        h, w = prev.shape
        center = np.array([w/2, h/2])
        radial_vecs = pts - center
        radial_norms = np.linalg.norm(radial_vecs, axis=1)
        far_mask = radial_norms > 5.0
        if np.sum(far_mask) >= 10:
            final_mask = far_mask & (norms_res > 0.5)
            if np.sum(final_mask) >= 10:
                pts_f = pts[final_mask]
                vecs_f = vecs_res[final_mask]
                radial_f = radial_vecs[final_mask]
                radial_n_f = radial_norms[final_mask]
                vec_n_f = norms_res[final_mask]

                # 方向径向一致性
                radial_unit = radial_f / (radial_n_f[:, np.newaxis] + 1e-8)
                vec_unit = vecs_f / (vec_n_f[:, np.newaxis] + 1e-8)
                cos_sim_rad = np.abs(np.sum(vec_unit * radial_unit, axis=1))
                consistency_rad = np.mean(cos_sim_rad > 0.85)

                # 位移大小与距离中心的相关性
                if consistency_rad >= CONFIG.get("ZOOM_DIRECTION_CONSISTENCY", 0.7):
                    if len(vec_n_f) > 5:
                        corr = np.corrcoef(radial_n_f, vec_n_f)[0, 1]
                        if corr >= CONFIG.get("ZOOM_RADIAL_CORRELATION", 0.3):
                            is_zoom = True

    return has_shift, dx, dy, curr_aligned, is_layer_move, is_zoom


# ======================== 完整检测 ========================

def full_is_new_cel(prev, curr, use_optical_flow):
    """
    完整检测，返回 (是否为新张, 移动类型描述)
    使用统一运动分析进行全局平移、图层分离、缩放检测，
    并对齐后差分，结合局部运动、相似度等进行综合判断。
    """
    # 相关性及初始差分检查（用于极慢平移/静止直接返回）
    corr = fast_global_corr(prev, curr)  # 替换模板匹配

    if corr > CONFIG["FULL_CORR_THRESHOLD"]:
        raw_diff = cv2.absdiff(prev, curr)
        raw_thresh = adaptive_threshold(curr)
        _, raw_mask = cv2.threshold(raw_diff, raw_thresh, 255, cv2.THRESH_BINARY)
        raw_ratio = np.count_nonzero(raw_mask) / raw_mask.size
        if raw_ratio < CONFIG["FULL_STILL_RATIO"]:
            return False, "极慢平移/静止"

    diff_thresh = adaptive_threshold(curr)
    raw_diff = cv2.absdiff(prev, curr)
    _, raw_mask = cv2.threshold(raw_diff, diff_thresh, 255, cv2.THRESH_BINARY)
    raw_ratio = np.count_nonzero(raw_mask) / raw_mask.size

    # 统一运动分析（替代原来的独立全局平移估计和图层分离检测）
    has_shift, dx, dy, curr_aligned, is_layer_move, is_zoom = unified_motion_analysis(prev, curr)

    # 对齐后差分
    diff = cv2.absdiff(prev, curr_aligned)
    _, mask = cv2.threshold(diff, diff_thresh, 255, cv2.THRESH_BINARY)
    kernel = np.ones((3, 3), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    change_ratio = np.count_nonzero(mask) / mask.size

    # 全局平移过滤：有平移 + 对齐后变化小 → 不是新张
    if raw_ratio >= CONFIG["MIN_CHANGE_RATIO"] and change_ratio < CONFIG["ALIGNED_CHANGE_THRESHOLD"] and has_shift:
        return False, "全局平移"

    # 进一步分析（变化面积超过对齐阈值）
    if change_ratio >= CONFIG["ALIGNED_CHANGE_THRESHOLD"]:
        if has_local_motion(mask):
            return True, "新作画"

        # 图层分离运镜
        if use_optical_flow and is_layer_move:
            return False, "图层分离运镜"
        # 缩放运镜
        if use_optical_flow and is_zoom:
            return False, "缩放运镜"

        if change_ratio >= CONFIG["SIGNIFICANT_CHANGE_RATIO"]:
            return True, "新作画"

    # 剩余微小变化的补充检查
    if change_ratio < CONFIG["SIGNIFICANT_CHANGE_RATIO"]:
        mask_inv = cv2.bitwise_not(mask)
        score = cv2.matchTemplate(prev, curr_aligned, cv2.TM_CCOEFF_NORMED, mask=mask_inv)[0][0]
        if score >= CONFIG["SSIM_THRESHOLD"]:
            return False, "局部变化(过滤)"

    return True, "新作画"