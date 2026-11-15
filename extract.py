#!/usr/bin/env python3
"""
离线视频张数提取（无 UI，极速模式）。

用法: python extract.py <视频文件> [--output result.csv] [--no-gpu] [--no-async]

默认启用异步双缓冲：GPU 运动分析在 worker 线程跑，主线程同时结算上一帧的
CPU 判定，两者重叠。统计会滞后一帧，但每条记录用帧自己的时间戳，总数与
滑动窗口不受影响。`--no-async` 可退回串行（用于对比/排障）。
"""
import sys, os, time, argparse
from collections import deque

import opencv_env  # noqa: F401  —— 注册 OpenCV/CUDA DLL 搜索路径（必须先于 cv2）
import cv2
import numpy as np

# 多进程并行时每个子进程必须限制 OpenCV 线程数，否则 8 进程 × 16 线程
# 在 8 核上严重超订。extract_parallel 会给子进程设 ZSJC_CV_THREADS=1。
_cv_threads = int(os.environ.get("ZSJC_CV_THREADS", "0") or 0)
if _cv_threads > 0:
    cv2.setNumThreads(_cv_threads)

from config import CONFIG
from detection import (
    compute_diff_mask,
    basic_is_new_cel, full_is_new_cel, full_decide, is_full_still,
    unified_motion_analysis,
    GPUContext, gpu_probe,
)
from async_pipeline import MotionPipeline
from video_source import FFmpegNvDecSource, FrameSource


def main():
    parser = argparse.ArgumentParser(description="离线视频张数提取")
    parser.add_argument("video", help="视频文件路径")
    parser.add_argument("--output", "-o", default="cel_result.csv", help="输出 CSV (默认 cel_result.csv)")
    parser.add_argument("--no-gpu", action="store_true", help="禁用 GPU")
    parser.add_argument("--nvdec", action="store_true",
                        help="用 FFmpeg NVDEC 管道解码（默认用 OpenCV 解码："
                             "1080p 实测 NVDEC 整帧回传反而更慢）")
    parser.add_argument("--async", dest="use_async", action="store_true",
                        help="启用异步双缓冲（GPU 线程与 CPU 判定重叠；"
                             "实测在 Python GIL 下收益不稳定，默认关闭）")
    parser.add_argument("--scale", type=float, default=CONFIG["SCALE_FACTOR"], help="缩放比例")
    parser.add_argument("--seek-start", type=float, default=0.0, help="起始秒数 (并行分片用)")
    parser.add_argument("--max-duration", type=float, default=0.0, help="最多处理秒数 (0=全部)")
    parser.add_argument("--progress-file", default="", help="进度文件路径 (并行模式)")
    args = parser.parse_args()

    # ─── GPU 初始化（逐项自检，只有光流链路可用才启用）───
    use_gpu = not args.no_gpu
    gpu_ctx = None
    lk_cuda = None
    if use_gpu and not CONFIG.get("GPU_FORCE_FALLBACK", False):
        info = gpu_probe()
        if info.flow_ready:
            try:
                gpu_ctx = GPUContext(info)
                lk_cuda = gpu_ctx.lk
                print(f"[GPU] {info.summary()}")
            except Exception as e:  # noqa: BLE001
                print(f"[GPU] 初始化失败 ({e})，回退 CPU")
                use_gpu = False
                gpu_ctx = None
        else:
            print(f"[GPU] {info.summary()}，使用 CPU")
            use_gpu = False
    else:
        use_gpu = False

    # ─── 帧源：默认 OpenCV 解码（1080p 下比 NVDEC 整帧回传更快）───
    frame_source = None
    if use_gpu and args.nvdec:
        try:
            frame_source = FFmpegNvDecSource(
                video_path=args.video,
                scale_factor=args.scale,
                hash_enabled=True)
            print(f"[输入] NVDEC 硬解管道  {frame_source.frame_shape[1]}x{frame_source.frame_shape[0]}")
        except Exception as e:  # noqa: BLE001
            print(f"[输入] NVDEC 失败 ({e})，回退 OpenCV")

    if frame_source is None:
        frame_source = FrameSource(
            mode='video', video_path=args.video,
            scale_factor=args.scale, use_gpu=use_gpu,
            hw_accel=CONFIG.get("VIDEO_HW_ACCEL", True),
            hash_enabled=True)
        print(f"[输入] OpenCV VideoCapture  {frame_source.frame_shape[1]}x{frame_source.frame_shape[0]}")

    video_fps = frame_source.video_fps
    total_frames = frame_source.video_total_frames
    duration = frame_source.video_duration
    print(f"[视频] {video_fps:.2f} fps, {total_frames} 帧, {duration:.2f} 秒")

    # ─── Seek 到起始位置（并行分片用）───
    if args.seek_start > 0:
        frame_source.seek(args.seek_start)
        print(f"[Seek] 跳至 {args.seek_start:.1f}s")
    max_ts = args.seek_start + args.max_duration if args.max_duration > 0 else float("inf")

    # ─── 检测状态 ───
    last_frame = None
    frame_buffer = deque(maxlen=CONFIG["FRAME_BUFFER_SIZE"])
    raw_timestamps = deque()
    full_filter_active = False
    full_filter_active_until = 0.0
    hash_threshold = CONFIG["HASH_THRESHOLD"]

    # ─── 统计 ───
    total_cels = 0
    total_raw = 0
    total_translation = 0
    total_flow = 0
    total_hash = 0
    total_still = 0
    total_local = 0
    total_zoom = 0
    total_other = 0

    recent_cels = deque()
    recent_raw = deque()
    recent_sum_cels = 0
    recent_sum_raw = 0
    csv_rows = [("time_sec", "cels_per_sec", "raw_per_sec", "total_cels", "move_type")]

    frame_count = 0
    t_start = time.perf_counter()

    # ─── 异步流水线 ───
    pipeline = None
    if args.use_async and use_gpu and gpu_ctx is not None:
        pipeline = MotionPipeline(unified_motion_analysis, gpu_ctx, lk_cuda,
                                  use_optical_flow=True)
    pending = None  # (ctx, token)

    def settle(ctx, final_detected, move_type):
        """把一帧的最终判定计入统计（哈希去重 / 过滤分类 / 滑动窗口 / CSV）。"""
        nonlocal total_cels, total_hash, total_translation, total_flow
        nonlocal total_zoom, total_still, total_local, total_other
        nonlocal recent_sum_cels, recent_sum_raw

        ts = ctx["ts"]
        raw_new = ctx["raw_new"]
        duplicate = ctx["duplicate"]

        new_cel = 1 if (not duplicate and final_detected) else 0

        if raw_new == 1 and new_cel == 0:
            if duplicate and final_detected:
                total_hash += 1
            elif move_type == "全局平移":
                total_translation += 1
            elif move_type == "图层分离运镜":
                total_flow += 1
            elif move_type == "缩放运镜":
                total_zoom += 1
            elif move_type == "极慢平移/静止":
                total_still += 1
            elif move_type == "局部变化(过滤)":
                total_local += 1
            else:
                total_other += 1

        total_cels += new_cel

        recent_cels.append((ts, new_cel))
        recent_sum_cels += new_cel
        while recent_cels and ts - recent_cels[0][0] > 1.0:
            recent_sum_cels -= recent_cels.popleft()[1]

        recent_raw.append((ts, raw_new))
        recent_sum_raw += raw_new
        while recent_raw and ts - recent_raw[0][0] > 1.0:
            recent_sum_raw -= recent_raw.popleft()[1]

        span = ts - recent_cels[0][0] if recent_cels else 0.001
        cels_per_sec = recent_sum_cels / max(span, 0.001) if recent_cels else 0.0
        span_r = ts - recent_raw[0][0] if recent_raw else 0.001
        raw_per_sec = recent_sum_raw / max(span_r, 0.001) if recent_raw else 0.0

        last_csv_time = csv_rows[-1][0]
        if last_csv_time == "time_sec" or float(ts) - float(last_csv_time) >= 1.0:
            csv_rows.append((f"{ts:.1f}", f"{cels_per_sec:.2f}", f"{raw_per_sec:.2f}",
                             str(total_cels), move_type))

    def settle_pending():
        """结算上一帧的异步完整检测结果（GPU 线程此时可能在算下一帧）。"""
        nonlocal pending
        if pending is None:
            return
        ctx, _token = pending
        pending = None
        try:
            _, motion = pipeline.collect()
        except Exception:  # noqa: BLE001
            motion = (False, 0.0, 0.0, ctx["curr"], False, False)
        final_detected, mt = full_decide(ctx["prev"], ctx["curr"], motion, ctx["ratio"])
        settle(ctx, final_detected, mt)

    # ─── 主循环 ───
    import queue as qmod
    _consecutive_empty = 0
    while True:
        data = frame_source.next_frame()
        if data is None or data[0] is None:
            # 等待解码线程产出帧
            try:
                data = frame_source.frame_queue.get(timeout=3.0)
            except qmod.Empty:
                break
        if data is None or data[0] is None:
            _consecutive_empty += 1
            if _consecutive_empty > 5:
                break
            continue
        _consecutive_empty = 0
        ts, now_frame, _, v_hash, v_oped = data
        if ts is None or ts < 0:
            ts = 0.0
        # 达到最大时长则停止
        if ts >= max_ts:
            break
        frame_count += 1

        hash_256 = v_hash
        oped_hash = v_oped

        if last_frame is None or last_frame.shape != now_frame.shape:
            last_frame = now_frame
            if hash_256 is not None:
                frame_buffer.append(hash_256)
            continue

        # ─── 差分（统一走 CPU，小帧 GPU 反而慢）───
        _, _, pre_ratio = compute_diff_mask(last_frame, now_frame)

        raw_new = 1 if pre_ratio >= CONFIG["MIN_CHANGE_RATIO"] else 0
        total_raw += raw_new

        basic_detected, move_type = basic_is_new_cel(
            last_frame, now_frame, precomputed_ratio=pre_ratio)
        if basic_detected:
            raw_timestamps.append(ts)

        # 动态触发
        window_sec = CONFIG["FILTER_TRIGGER_WINDOW_MS"] / 1000.0
        while raw_timestamps and raw_timestamps[0] < ts - window_sec:
            raw_timestamps.popleft()

        if len(raw_timestamps) >= CONFIG["FILTER_TRIGGER_COUNT"]:
            if not full_filter_active:
                full_filter_active = True
            full_filter_active_until = ts + CONFIG["FULL_FILTER_HOLD_SEC"]
        elif full_filter_active and ts > full_filter_active_until:
            full_filter_active = False

        ctx = {"ts": ts, "hash": hash_256, "raw_new": raw_new,
               "ratio": pre_ratio, "prev": last_frame, "curr": now_frame}

        # 哈希去重必须按帧到达顺序做，不能放到异步结算里：
        # 否则 frame_buffer 的 append 会滞后一帧，buf[:-1] 排除的“最近一帧”
        # 就不是真正的上一帧，重复帧会漏判（实测异步比同步多算 5 张）。
        duplicate = False
        if hash_256 is not None:
            buf = list(frame_buffer)
            for h in buf[:-1]:
                if np.sum(hash_256 != h) <= hash_threshold:
                    duplicate = True
                    break
            frame_buffer.append(hash_256)
        ctx["duplicate"] = duplicate

        prev_frame = last_frame
        last_frame = now_frame

        if (full_filter_active and pipeline is not None
                and gpu_ctx.usable_for_flow(now_frame.shape)):
            if is_full_still(prev_frame, ctx["curr"], pre_ratio):
                settle_pending()
                settle(ctx, False, "极慢平移/静止")
            else:
                # 先提交当前帧，让 GPU 线程开始算，再结算上一帧的 CPU 部分
                pipeline.submit(ts, prev_frame, ctx["curr"])
                settle_pending()
                pending = (ctx, ts)
        elif full_filter_active:
            settle_pending()
            uploaded = False
            if (use_gpu and gpu_ctx is not None
                    and gpu_ctx.usable_for_flow(ctx["curr"].shape)):
                try:
                    gpu_ctx.upload_pair(prev_frame, ctx["curr"])
                    uploaded = True
                except Exception:  # noqa: BLE001
                    uploaded = False
            final_detected, mt = full_is_new_cel(
                prev_frame, ctx["curr"], use_optical_flow=True,
                use_gpu=use_gpu, lk_cuda=lk_cuda, precomputed_ratio=pre_ratio,
                gpu_ctx=gpu_ctx if uploaded else None,
                frames_uploaded=uploaded)
            settle(ctx, final_detected, mt)
        else:
            settle_pending()
            settle(ctx, basic_detected, move_type)

        # 进度
        if frame_count % 200 == 0 and duration > 0:
            elapsed = time.perf_counter() - t_start
            pct = ts / duration * 100
            fps = frame_count / elapsed
            mode = "满" if full_filter_active else "基"
            if args.progress_file:
                try:
                    with open(args.progress_file, "w") as pf:
                        pf.write(f"{pct:.1f}\n{fps:.0f}\n{total_cels}\n{mode}")
                except Exception:  # noqa: BLE001
                    pass
            else:
                print(f"\r  进度 {pct:.0f}% | {fps:.0f} fps | 张数 {total_cels} | [{mode}]",
                      end="")

    # 收尾：结算最后一帧
    if pending is not None:
        settle_pending()
    if pipeline is not None:
        pipeline.close()

    # ─── 结束 ───
    t_total = time.perf_counter() - t_start
    print(f"\r  完成! {frame_count} 帧 / {t_total:.1f}s = {frame_count/t_total:.0f} fps")
    print(f"\n{'='*50}")
    print(f"  总帧数:     {frame_count}")
    print(f"  过滤前张数: {total_raw}")
    print(f"  过滤后张数: {total_cels}")
    print(f"  平均张/秒:  {total_cels/duration:.2f}" if duration > 0 else "")
    print(f"  过滤明细:")
    print(f"    平移: {total_translation}  光流: {total_flow}  哈希: {total_hash}")
    print(f"    静止: {total_still}  局部: {total_local}  缩放: {total_zoom}  其他: {total_other}")
    print(f"{'='*50}")

    # 写 CSV
    with open(args.output, "w", encoding="utf-8-sig", newline="") as f:
        for row in csv_rows:
            f.write(",".join(row) + "\n")
    print(f"\n结果已保存: {args.output}")

    # 清理
    frame_source.close()
    if gpu_ctx:
        gpu_ctx.release()


if __name__ == "__main__":
    main()
