#!/usr/bin/env python3
"""
性能诊断脚本 —— 排查视频检测速度慢的根因。
用法：python diagnose.py <视频文件路径> [--frames N] [--no-gpu]
  python diagnose.py video.mp4 --frames 500 --mode basic
  python diagnose.py video.mp4 --frames 500 --mode adaptive
python diagnose.py "C:/Users/24628/Desktop/2026-05-29 21-21-02.mp4" --frames 1000 --mode adaptive
python diagnose.py "C:/Users/24628/Desktop/2026-05-29 21-21-02.mp4" --frames 1000 --mode basic
"""


import sys, time, argparse
from collections import deque
import cv2
import numpy as np

# ==================== 完整 CONFIG（与主程序一致） ====================
CONFIG = {
    "SCALE_FACTOR": 0.5,
    "SSIM_THRESHOLD": 0.93,
    "MIN_DIFF_THRESHOLD": 5,
    "MAX_DIFF_THRESHOLD": 30,
    "MIN_CHANGE_RATIO": 0.002,
    "SIGNIFICANT_CHANGE_RATIO": 0.02,
    "ALIGNED_CHANGE_THRESHOLD": 0.02,
    "BASIC_CORR_THRESHOLD": 0.995,
    "BASIC_MIN_RAW_RATIO_STILL": 0.004,
    "FULL_CORR_THRESHOLD": 0.998,
    "FULL_STILL_RATIO": 0.002,
    "LOCAL_AREA_THRESH": 0.003,
    "LOCAL_BBOX_RATIO_MAX": 0.8,
    "LOCAL_ASPECT_RATIO_MAX": 8,
    "LAYER_MIN_VALID_POINTS": 15,
    "LAYER_MIN_MOVING_POINTS": 10,
    "LAYER_DIRECTION_CONSISTENCY": 0.75,
    "LAYER_MEAN_VEC_MIN": 0.1,
    "LAYER_COS_SIM_THRESH": 0.7,
    "FLOW_LAYER_STATIC_THRESH": 2.0,
    "FLOW_FEATURE_COUNT": 150,
    "FLOW_QUALITY_LEVEL": 0.05,
    "FLOW_MIN_DISTANCE": 10,
    "ZOOM_DIRECTION_CONSISTENCY": 0.7,
    "ZOOM_RADIAL_CORRELATION": 0.3,
    "FRAME_BUFFER_SIZE": 24,
    "HASH_THRESHOLD": 0,
    "FILTER_TRIGGER_WINDOW_MS": 200,
    "FILTER_TRIGGER_COUNT": 3,
    "FULL_FILTER_HOLD_SEC": 5,
}

# ==================== 所有检测函数（从 detection.py 复制） ====================
def compute_all_hashes(gray_img):
    resized_32 = cv2.resize(gray_img, (32, 32), interpolation=cv2.INTER_AREA)
    resized_16 = cv2.resize(resized_32, (16, 16), interpolation=cv2.INTER_AREA)
    avg_16 = resized_16.mean()
    hash_256 = (resized_16 > avg_16).flatten()
    dct = cv2.dct(np.float32(resized_32))
    dct_low = dct[:8, :8]
    avg_oped = dct_low.mean()
    bits = dct_low > avg_oped
    packed = np.packbits(bits.flatten())
    oped_hash = int.from_bytes(packed.tobytes(), 'little')
    return hash_256, oped_hash

def adaptive_threshold(frame):
    mean_val = np.mean(frame)
    t = CONFIG["MIN_DIFF_THRESHOLD"] + (mean_val / 255.0) * (CONFIG["MAX_DIFF_THRESHOLD"] - CONFIG["MIN_DIFF_THRESHOLD"])
    return max(CONFIG["MIN_DIFF_THRESHOLD"], min(CONFIG["MAX_DIFF_THRESHOLD"], t))

def fast_global_corr(img1, img2):
    f1 = img1.astype(np.float64).ravel()
    f2 = img2.astype(np.float64).ravel()
    m1 = np.mean(f1)
    m2 = np.mean(f2)
    num = np.dot(f1 - m1, f2 - m2)
    den = np.sqrt(np.dot(f1 - m1, f1 - m1) * np.dot(f2 - m2, f2 - m2))
    if den < 1e-10:
        return 1.0
    return num / den

def basic_is_new_cel(prev, curr):
    corr = fast_global_corr(prev, curr)
    if corr > CONFIG["BASIC_CORR_THRESHOLD"]:
        raw_diff = cv2.absdiff(prev, curr)
        raw_thresh = adaptive_threshold(curr)
        _, raw_mask = cv2.threshold(raw_diff, raw_thresh, 255, cv2.THRESH_BINARY)
        kernel = np.ones((3, 3), np.uint8)
        raw_mask = cv2.morphologyEx(raw_mask, cv2.MORPH_CLOSE, kernel)
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

def has_local_motion(binary_mask):
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

def unified_motion_analysis(prev, curr, use_optical_flow=True, use_gpu=False):
    max_corners = 200
    corners = cv2.goodFeaturesToTrack(prev, maxCorners=max_corners,
                                     qualityLevel=CONFIG["FLOW_QUALITY_LEVEL"],
                                     minDistance=CONFIG["FLOW_MIN_DISTANCE"], mask=None)
    has_shift = False
    dx, dy = 0.0, 0.0
    curr_aligned = curr
    is_layer_move = False
    is_zoom = False
    if corners is None:
        return has_shift, dx, dy, curr_aligned, is_layer_move, is_zoom

    p1 = np.float32(corners).reshape(-1, 2)
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

    p2_aligned, status_aligned, _ = cv2.calcOpticalFlowPyrLK(prev, curr_aligned, p1, None)
    if p2_aligned is not None and np.sum(status_aligned) >= 20:
        valid = status_aligned.flatten() == 1
        vecs_res = p2_aligned[valid] - p1[valid]
        pts = p1[valid]
        norms_res = np.linalg.norm(vecs_res, axis=1)
        moving_mask = norms_res > CONFIG["FLOW_LAYER_STATIC_THRESH"]
        if np.sum(moving_mask) >= CONFIG["LAYER_MIN_MOVING_POINTS"]:
            moving_vecs = vecs_res[moving_mask]
            mean_vec = np.mean(moving_vecs, axis=0)
            if np.linalg.norm(mean_vec) >= CONFIG["LAYER_MEAN_VEC_MIN"]:
                cos_sim = np.dot(moving_vecs, mean_vec) / (
                    np.linalg.norm(moving_vecs, axis=1) * np.linalg.norm(mean_vec) + 1e-8)
                consistency = np.mean(cos_sim > CONFIG["LAYER_COS_SIM_THRESH"])
                if consistency > CONFIG["LAYER_DIRECTION_CONSISTENCY"]:
                    is_layer_move = True
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
                radial_unit = radial_f / (radial_n_f[:, np.newaxis] + 1e-8)
                vec_unit = vecs_f / (vec_n_f[:, np.newaxis] + 1e-8)
                cos_sim_rad = np.abs(np.sum(vec_unit * radial_unit, axis=1))
                consistency_rad = np.mean(cos_sim_rad > 0.85)
                if consistency_rad >= CONFIG.get("ZOOM_DIRECTION_CONSISTENCY", 0.7):
                    if len(vec_n_f) > 5:
                        corr = np.corrcoef(radial_n_f, vec_n_f)[0, 1]
                        if corr >= CONFIG.get("ZOOM_RADIAL_CORRELATION", 0.3):
                            is_zoom = True
    return has_shift, dx, dy, curr_aligned, is_layer_move, is_zoom

def full_is_new_cel(prev, curr, use_optical_flow, use_gpu=False):
    corr = fast_global_corr(prev, curr)
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
    has_shift, dx, dy, curr_aligned, is_layer_move, is_zoom = unified_motion_analysis(
        prev, curr, use_optical_flow, use_gpu=False)
    diff = cv2.absdiff(prev, curr_aligned)
    _, mask = cv2.threshold(diff, diff_thresh, 255, cv2.THRESH_BINARY)
    kernel = np.ones((3, 3), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    change_ratio = np.count_nonzero(mask) / mask.size
    if raw_ratio >= CONFIG["MIN_CHANGE_RATIO"] and change_ratio < CONFIG["ALIGNED_CHANGE_THRESHOLD"] and has_shift:
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
    if change_ratio < CONFIG["SIGNIFICANT_CHANGE_RATIO"]:
        mask_inv = cv2.bitwise_not(mask)
        score = cv2.matchTemplate(prev, curr_aligned, cv2.TM_CCOEFF_NORMED, mask=mask_inv)[0][0]
        if score >= CONFIG["SSIM_THRESHOLD"]:
            return False, "局部变化(过滤)"
    return True, "新作画"

# ==================== 主程序 ====================
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("video")
    parser.add_argument("--frames", type=int, default=500)
    parser.add_argument("--mode", choices=["basic", "adaptive", "full"], default="basic")
    args = parser.parse_args()

    cap = cv2.VideoCapture(args.video)
    full_filter_active = False
    full_filter_active_until = 0
    raw_detection_timestamps = deque()

    times = {
        "read+gray": [],
        "hash": [],
        "basic_detect": [],
        "full_detect": [],
        "full_detect_count": 0,
        "total_frames": 0,
    }

    prev_frame = None
    frame_count = 0
    t_total_start = time.perf_counter()

    while frame_count < args.frames:
        ret, frame = cap.read()
        if not ret:
            break

        t0 = time.perf_counter()
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        small = cv2.resize(gray, (0,0), fx=CONFIG["SCALE_FACTOR"], fy=CONFIG["SCALE_FACTOR"])
        t_read = time.perf_counter() - t0

        t_hash = time.perf_counter()
        hash_256, oped_hash = compute_all_hashes(small)
        t_hash = time.perf_counter() - t_hash

        if prev_frame is not None:
            t_basic_start = time.perf_counter()
            basic_detected, move_type = basic_is_new_cel(prev_frame, small)
            t_basic = time.perf_counter() - t_basic_start

            final_detected = basic_detected
            t_full = 0.0

            if args.mode == "adaptive":
                if basic_detected:
                    raw_detection_timestamps.append(time.perf_counter())
                window_sec = CONFIG["FILTER_TRIGGER_WINDOW_MS"] / 1000.0
                now = time.perf_counter()
                while raw_detection_timestamps and raw_detection_timestamps[0] < now - window_sec:
                    raw_detection_timestamps.popleft()
                if len(raw_detection_timestamps) >= CONFIG["FILTER_TRIGGER_COUNT"]:
                    if not full_filter_active:
                        full_filter_active = True
                    full_filter_active_until = now + CONFIG["FULL_FILTER_HOLD_SEC"]
                elif full_filter_active and now > full_filter_active_until:
                    full_filter_active = False
                if full_filter_active:
                    t_full_start = time.perf_counter()
                    final_detected, move_type = full_is_new_cel(prev_frame, small, True, use_gpu=False)
                    t_full = time.perf_counter() - t_full_start
                    times["full_detect_count"] += 1
                else:
                    final_detected = basic_detected

            elif args.mode == "full":
                t_full_start = time.perf_counter()
                final_detected, move_type = full_is_new_cel(prev_frame, small, True, use_gpu=False)
                t_full = time.perf_counter() - t_full_start

            times["read+gray"].append(t_read)
            times["hash"].append(t_hash)
            times["basic_detect"].append(t_basic)
            if t_full > 0:
                times["full_detect"].append(t_full)

        prev_frame = small
        frame_count += 1
        times["total_frames"] += 1

    cap.release()
    total_real_time = time.perf_counter() - t_total_start

    print(f"\n模式: {args.mode}")
    print(f"处理帧数: {times['total_frames']}")
    print(f"总耗时: {total_real_time:.2f} 秒")
    print(f"实际处理速率: {times['total_frames']/total_real_time:.1f} fps\n")

    print("{:<20} {:>10} {:>10}".format("阶段", "平均(ms)", "占比"))
    items = [
        ("read+gray", "读取+灰度缩放"),
        ("hash", "哈希计算"),
        ("basic_detect", "基础检测"),
    ]
    if args.mode != "basic" and times["full_detect"]:
        items.append(("full_detect", "完整检测"))
    for key, name in items:
        data = times.get(key, [])
        if not data:
            continue
        avg = np.mean(data) * 1000
        pct = avg / (total_real_time / times["total_frames"] * 1000) * 100
        print(f"{name:<20} {avg:>8.2f} {pct:>8.1f}%")
    if args.mode == "adaptive":
        print(f"\n完整检测调用次数: {times['full_detect_count']} (占比 {times['full_detect_count']/times['total_frames']*100:.1f}%)")

if __name__ == "__main__":
    main()