#!/usr/bin/env python3
"""
性能诊断脚本 —— 逐阶段计时，定位视频检测慢在哪。

用法：
    python diagnose.py <视频文件> [--frames N] [--mode basic|adaptive|full] [--gpu]

说明：本脚本不再复制一份检测算法（旧版有 250 行副本，早已与 detection.py
分叉，测出来的数字和正式运行不是一回事）。现在直接调用 detection.py，
需要对比 GPU 时加 --gpu，得到的耗时与主程序/提取器一致。
"""
import argparse
import time
from collections import deque

import opencv_env  # noqa: F401  —— 必须先于 cv2 注册 DLL 搜索路径
import cv2
import numpy as np

from config import CONFIG
from detection import (
    compute_all_hashes, compute_diff_mask, basic_is_new_cel,
    full_is_new_cel, GPUContext, gpu_probe,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("video")
    parser.add_argument("--frames", type=int, default=500)
    parser.add_argument("--mode", choices=["basic", "adaptive", "full"], default="basic")
    parser.add_argument("--gpu", action="store_true", help="完整检测用 GPU 光流")
    args = parser.parse_args()

    gpu_ctx = None
    use_gpu = False
    if args.gpu:
        info = gpu_probe()
        print(f"[GPU] {info.summary()}")
        if info.flow_ready:
            gpu_ctx = GPUContext(info)
            use_gpu = True
        else:
            print("[GPU] 光流链路不可用，退回 CPU")

    cap = cv2.VideoCapture(args.video)
    if not cap.isOpened():
        raise SystemExit(f"无法打开视频: {args.video}")

    full_filter_active = False
    full_filter_active_until = 0.0
    raw_detection_timestamps = deque()

    times = {
        "read+gray": [],
        "hash": [],
        "diff": [],
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
        small = cv2.resize(gray, (0, 0), fx=CONFIG["SCALE_FACTOR"],
                           fy=CONFIG["SCALE_FACTOR"])
        t_read = time.perf_counter() - t0

        t_hash = time.perf_counter()
        compute_all_hashes(small)
        t_hash = time.perf_counter() - t_hash

        if prev_frame is not None:
            t0 = time.perf_counter()
            _d, _m, raw_ratio = compute_diff_mask(prev_frame, small)
            t_diff = time.perf_counter() - t0

            t0 = time.perf_counter()
            basic_detected, move_type = basic_is_new_cel(
                prev_frame, small, precomputed_ratio=raw_ratio)
            t_basic = time.perf_counter() - t0

            final_detected = basic_detected
            t_full = 0.0

            if args.mode == "adaptive":
                if basic_detected:
                    raw_detection_timestamps.append(time.perf_counter())
                window_sec = CONFIG["FILTER_TRIGGER_WINDOW_MS"] / 1000.0
                now = time.perf_counter()
                while (raw_detection_timestamps
                       and raw_detection_timestamps[0] < now - window_sec):
                    raw_detection_timestamps.popleft()
                if len(raw_detection_timestamps) >= CONFIG["FILTER_TRIGGER_COUNT"]:
                    full_filter_active = True
                    full_filter_active_until = now + CONFIG["FULL_FILTER_HOLD_SEC"]
                elif full_filter_active and now > full_filter_active_until:
                    full_filter_active = False
                if full_filter_active:
                    t0 = time.perf_counter()
                    uploaded = False
                    if use_gpu and gpu_ctx.usable_for_flow(small.shape):
                        try:
                            gpu_ctx.upload_pair(prev_frame, small)
                            uploaded = True
                        except Exception:  # noqa: BLE001
                            uploaded = False
                    final_detected, move_type = full_is_new_cel(
                        prev_frame, small, True,
                        use_gpu=use_gpu, gpu_ctx=gpu_ctx if uploaded else None,
                        precomputed_ratio=raw_ratio, frames_uploaded=uploaded)
                    t_full = time.perf_counter() - t0
                    times["full_detect_count"] += 1
                else:
                    final_detected = basic_detected

            elif args.mode == "full":
                t0 = time.perf_counter()
                uploaded = False
                if use_gpu and gpu_ctx.usable_for_flow(small.shape):
                    try:
                        gpu_ctx.upload_pair(prev_frame, small)
                        uploaded = True
                    except Exception:  # noqa: BLE001
                        uploaded = False
                final_detected, move_type = full_is_new_cel(
                    prev_frame, small, True,
                    use_gpu=use_gpu, gpu_ctx=gpu_ctx if uploaded else None,
                    precomputed_ratio=raw_ratio, frames_uploaded=uploaded)
                t_full = time.perf_counter() - t0

            times["read+gray"].append(t_read)
            times["hash"].append(t_hash)
            times["diff"].append(t_diff)
            times["basic_detect"].append(t_basic)
            if t_full > 0:
                times["full_detect"].append(t_full)

        prev_frame = small
        frame_count += 1
        times["total_frames"] += 1

    cap.release()
    total_real_time = time.perf_counter() - t_total_start
    if times["total_frames"] == 0:
        raise SystemExit("没有读到任何帧")

    print(f"\n模式: {args.mode} | GPU: {'yes' if use_gpu else 'no'}")
    print(f"处理帧数: {times['total_frames']}")
    print(f"总耗时: {total_real_time:.2f} 秒")
    print(f"实际处理速率: {times['total_frames'] / total_real_time:.1f} fps\n")

    print("{:<20} {:>10} {:>10}".format("阶段", "平均(ms)", "占比"))
    items = [
        ("read+gray", "读取+灰度缩放"),
        ("hash", "哈希计算"),
        ("diff", "差分掩膜"),
        ("basic_detect", "基础检测"),
    ]
    if args.mode != "basic" and times["full_detect"]:
        items.append(("full_detect", "完整检测"))
    per_frame_ms = total_real_time / times["total_frames"] * 1000
    for key, name in items:
        data = times.get(key, [])
        if not data:
            continue
        avg = float(np.mean(data)) * 1000
        pct = avg / per_frame_ms * 100
        print(f"{name:<20} {avg:>8.2f} {pct:>8.1f}%")
    if args.mode == "adaptive":
        print(f"\n完整检测调用次数: {times['full_detect_count']} "
              f"(占比 {times['full_detect_count'] / times['total_frames'] * 100:.1f}%)")


if __name__ == "__main__":
    main()
