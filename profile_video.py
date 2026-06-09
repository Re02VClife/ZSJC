#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
视频处理性能分析脚本（自动发现主模块版）
用法：
    python profile_video.py <视频文件路径> [--max-frames N] [--gpu]
示例：
    python profile_video.py test.mp4 --max-frames 500
    python profile_video.py test.mp4 --gpu
说明：
    会自动寻找同目录下包含 FrameSource / AnimeCelCounter 等定义的 .py 文件，
    无需关心主程序的文件名。
"""

import sys
import os
import time
import argparse
import importlib
import glob
import numpy as np
import cv2


# ---------- 自动发现主模块 ----------
def load_main_module(module_path=None):
    """加载包含 FrameSource 和 AnimeCelCounter 的主模块。
    增强版：打印搜索过程，自动排除无效文件。
    """
    current_dir = os.path.dirname(os.path.abspath(__file__))
    print(f"工作目录: {current_dir}")

    # 如果用户指定了文件，优先尝试
    if module_path:
        if not os.path.isabs(module_path):
            module_path = os.path.join(current_dir, module_path)
        print(f"正在尝试加载指定模块: {os.path.basename(module_path)}")
        if not os.path.exists(module_path):
            print(f"错误：文件不存在 -> {module_path}")
            return None
        try:
            spec = importlib.util.spec_from_file_location("_main_mod_", module_path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            if hasattr(mod, 'FrameSource') and hasattr(mod, 'AnimeCelCounter'):
                print(f"✓ 成功加载: {os.path.basename(module_path)}")
                return mod
            else:
                print(f"⚠ 文件已加载，但缺少 FrameSource 或 AnimeCelCounter")
                return None
        except Exception as e:
            print(f"❌ 加载时出错: {e}")
            return None

    # 自动搜索
    print("开始自动搜索当前目录下的 .py 文件...")
    py_files = glob.glob(os.path.join(current_dir, "*.py"))
    print(f"找到以下 .py 文件: {[os.path.basename(f) for f in py_files]}")

    candidates = []
    for filepath in py_files:
        basename = os.path.basename(filepath)
        if basename == os.path.basename(__file__):
            print(f"跳过自身: {basename}")
            continue
        try:
            spec = importlib.util.spec_from_file_location("_main_mod_", filepath)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            has_frame = hasattr(mod, 'FrameSource')
            has_counter = hasattr(mod, 'AnimeCelCounter')
            print(f"检查 {basename}: FrameSource={has_frame}, AnimeCelCounter={has_counter}")
            if has_frame and has_counter:
                candidates.append((basename, mod))
        except Exception as e:
            print(f"跳过 {basename} (无法加载): {e}")

    if len(candidates) == 1:
        print(f"✓ 已自动发现主模块: {candidates[0][0]}")
        return candidates[0][1]
    elif len(candidates) > 1:
        print("⚠ 发现多个包含必要类的 .py 文件，请用 --module 指定一个:")
        for name, _ in candidates:
            print(f"   {name}")
        return None
    else:
        print("❌ 未能找到包含 FrameSource 和 AnimeCelCounter 的主程序文件。")
        print("   → 请检查主程序文件是否在同一目录，且内部定义了这两个类。")
        return None
# ---------- 加载主模块 ----------
main_mod = load_main_module('main.py')   # 或改为 'ZhangShuJianCe.py'
if main_mod is None:
    # 如果指定文件名失败，尝试自动搜索
    main_mod = load_main_module()
if main_mod is None:
    print("无法加载主模块，程序退出。")
    sys.exit(1)

# 从主模块导入所有需要的对象
CONFIG = main_mod.CONFIG
adaptive_threshold = main_mod.adaptive_threshold
is_raw_change = main_mod.is_raw_change
basic_is_new_cel = main_mod.basic_is_new_cel
full_is_new_cel = main_mod.full_is_new_cel
compute_hash = main_mod.compute_hash
compute_oped_hash = main_mod.compute_oped_hash
fast_global_corr = main_mod.fast_global_corr
FrameSource = main_mod.FrameSource

# 避免 GUI 干扰
CONFIG["GPU_ENABLED"] = False  # 默认关闭 GPU，命令行可覆盖
CONFIG["OPED_DETECTION_ENABLED"] = False
CONFIG["USE_CUSTOM_COLORS"] = False


# ------------------------------------------------------------
# 计时器辅助类
# ------------------------------------------------------------
class PerfTimer:
    def __init__(self):
        self.reset()

    def reset(self):
        self.timings = {
            "read": [],
            "preprocess": [],
            "raw_detect": [],
            "basic_detect": [],
            "full_detect": [],
            "hash": [],
            "total_frame": []
        }
        self._start = None

    def start(self, key=None):
        self._start = time.perf_counter()

    def stop(self, key):
        elapsed = time.perf_counter() - self._start
        if key in self.timings:
            self.timings[key].append(elapsed)
        return elapsed

    def print_summary(self):
        print("\n======== 性能统计 (单位: 毫秒) ========")
        header = f"{'阶段':<20} {'平均':>10} {'最小':>10} {'最大':>10} {'中位数':>10} {'占比%':>10}"
        print(header)
        print("-" * len(header))
        total_avg = np.mean(self.timings["total_frame"]) * 1000
        if total_avg == 0:
            total_avg = 1e-9
        for key in ["read", "preprocess", "raw_detect", "basic_detect", "full_detect", "hash", "total_frame"]:
            arr = np.array(self.timings[key]) * 1000
            if len(arr) == 0:
                continue
            avg = np.mean(arr)
            pct = avg / total_avg * 100 if key != "total_frame" else 100.0
            print(f"{key:<20} {avg:10.3f} {np.min(arr):10.3f} {np.max(arr):10.3f} {np.median(arr):10.3f} {pct:9.1f}%")

        fps = 1000.0 / total_avg if total_avg > 0 else 0
        print(f"\n平均帧处理时间: {total_avg:.3f} ms  → 理论最大处理速度: {fps:.1f} fps")
        # 瓶颈提示
        avg_read = np.mean(self.timings["read"]) * 1000
        if avg_read > 0.5 * total_avg:
            print("⚠ 瓶颈在于视频读取 (I/O 或解码)。")
        elif np.mean(self.timings["full_detect"]) * 1000 > 0.3 * total_avg:
            print("⚠ 瓶颈在于完整检测 (光流/平移/缩放等)。")
        elif np.mean(self.timings["basic_detect"]) * 1000 > 0.3 * total_avg:
            print("⚠ 瓶颈在于基础检测。")
        else:
            print("✔ 各阶段耗时较为均衡。")


# ------------------------------------------------------------
# 主分析流程
# ------------------------------------------------------------
def run_profile(video_path, max_frames=1000, use_gpu=False):
    if not os.path.exists(video_path):
        print(f"视频文件不存在: {video_path}")
        return

    try:
        source = FrameSource(
            mode='video',
            video_path=video_path,
            scale_factor=CONFIG["SCALE_FACTOR"],
            use_gpu=use_gpu
        )
    except Exception as e:
        print(f"无法打开视频: {e}")
        return

    print(f"视频信息: {source.frame_shape} @ {source.video_fps:.2f} fps, 总帧数 {source.video_total_frames}")
    if use_gpu:
        CONFIG["GPU_ENABLED"] = True
        print("GPU 加速已启用")
    else:
        CONFIG["GPU_ENABLED"] = False

    timer = PerfTimer()
    prev_frame = None
    frame_count = 0

    hash_buffer = []
    raw_detection_timestamps = []
    full_filter_active = False
    full_filter_active_until = 0.0
    use_hash_filter = CONFIG.get("HASH_THRESHOLD", 0) >= 0
    use_optical_flow = True

    start_run = time.perf_counter()

    while frame_count < max_frames:
        # ---- 读取帧 ----
        timer.start("read")
        ts, gray, _ = source.next_frame()
        if gray is None:
            break
        timer.stop("read")

        # ---- 预处理（已内建缩放，无额外操作）----
        timer.start("preprocess")
        # 无额外操作
        timer.stop("preprocess")

        now_frame = gray

        # ---- 开始核心检测 ----
        timer.start("total_frame")
        new_cel = 0
        move_type = "静止"

        if prev_frame is not None:
            if prev_frame.shape != now_frame.shape:
                prev_frame = None
                timer.stop("total_frame")
                continue

            timer.start("raw_detect")
            raw_detected = is_raw_change(prev_frame, now_frame)
            timer.stop("raw_detect")

            timer.start("basic_detect")
            basic_detected, move_type = basic_is_new_cel(prev_frame, now_frame)
            timer.stop("basic_detect")

            if basic_detected:
                raw_detection_timestamps.append(ts)

            # 模拟动态过滤窗口维护
            window_sec = CONFIG["FILTER_TRIGGER_WINDOW_MS"] / 1000.0
            raw_detection_timestamps = [t for t in raw_detection_timestamps if ts - t <= window_sec]
            if len(raw_detection_timestamps) >= CONFIG["FILTER_TRIGGER_COUNT"]:
                if not full_filter_active:
                    full_filter_active = True
                full_filter_active_until = ts + CONFIG["FULL_FILTER_HOLD_SEC"]
            elif full_filter_active and ts > full_filter_active_until:
                full_filter_active = False

            if full_filter_active:
                timer.start("full_detect")
                final_detected, move_type = full_is_new_cel(
                    prev_frame, now_frame, use_optical_flow, use_gpu=use_gpu
                )
                timer.stop("full_detect")
            else:
                final_detected = basic_detected
                timer.start("full_detect")
                timer.stop("full_detect")

            # 哈希过滤
            timer.start("hash")
            if use_hash_filter:
                curr_hash = compute_hash(now_frame)
                duplicate = False
                for h in hash_buffer[:-1]:
                    if np.sum(curr_hash != h) <= CONFIG["HASH_THRESHOLD"]:
                        duplicate = True
                        break
                if not duplicate and final_detected:
                    new_cel = 1
            else:
                new_cel = 1 if final_detected else 0
            timer.stop("hash")

            if use_hash_filter and curr_hash is not None:
                hash_buffer.append(curr_hash)
                if len(hash_buffer) > CONFIG["FRAME_BUFFER_SIZE"]:
                    hash_buffer.pop(0)
        else:
            # 首帧仅记录哈希
            timer.start("raw_detect");
            timer.stop("raw_detect")
            timer.start("basic_detect");
            timer.stop("basic_detect")
            timer.start("full_detect");
            timer.stop("full_detect")
            timer.start("hash")
            if use_hash_filter:
                hash_buffer.append(compute_hash(now_frame))
            timer.stop("hash")

        timer.stop("total_frame")

        prev_frame = now_frame
        frame_count += 1

        if frame_count % 100 == 0:
            elapsed = time.perf_counter() - start_run
            print(f"已处理 {frame_count} 帧, 耗时 {elapsed:.2f}s, 当前速度 {frame_count / elapsed:.1f} fps")

    source.close()
    timer.print_summary()
    print(f"\n总共处理了 {frame_count} 帧。")


# ------------------------------------------------------------
# 命令行入口
# ------------------------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="视频处理性能分析")
    parser.add_argument("video_path", help="视频文件路径")
    parser.add_argument("--max-frames", type=int, default=1000, help="最大处理帧数 (默认1000)")
    parser.add_argument("--gpu", action="store_true", help="尝试启用 GPU 加速")
    args = parser.parse_args()

    gpu_available = False
    try:
        gpu_available = cv2.cuda.getCudaEnabledDeviceCount() > 0
    except:
        pass
    if args.gpu and not gpu_available:
        print("警告: 未检测到 OpenCV CUDA 支持，将回退到 CPU。")
        args.gpu = False

    run_profile(args.video_path, max_frames=args.max_frames, use_gpu=args.gpu)