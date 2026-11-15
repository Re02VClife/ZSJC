"""
番剧缓存处理器：合并 TS 分片 → 离线检测 → 生成报告。
GUI 线程安全：处理在后台线程执行，通过回调更新进度。
"""
import os, sys, time, threading, subprocess, tempfile, json, shutil, queue
from pathlib import Path
from collections import deque, defaultdict

import numpy as np
from config import CONFIG
from detection import (
    compute_diff_mask,
    compute_all_hashes,
    basic_is_new_cel, full_is_new_cel,
    GPUContext, gpu_probe,
)
from video_source import FrameSource


def find_ffmpeg():
    """自动查找 FFmpeg 可执行文件"""
    candidates = ["ffmpeg", "ffmpeg.exe"]
    try:
        base = Path(os.environ["LOCALAPPDATA"]) / "Microsoft/WinGet/Packages"
        for d in base.glob("Gyan.FFmpeg.Shared_*"):
            for exe in d.glob("ffmpeg-*/bin/ffmpeg.exe"):
                candidates.insert(0, str(exe))
    except Exception:
        pass
    import subprocess as sp
    for c in candidates:
        try:
            sp.check_call([c, "-version"], stdout=sp.DEVNULL, stderr=sp.DEVNULL)
            return c
        except Exception:
            continue
    return "ffmpeg"


def find_ts_files(directory):
    """递归查找目录下所有 seg_*.ts 文件，按子目录分组排序"""
    groups = defaultdict(list)
    for f in Path(directory).rglob("seg_*.ts"):
        groups[str(f.parent)].append(str(f))
    result = []
    for subdir in sorted(groups.keys()):
        result.extend(sorted(groups[subdir]))
    return result


def find_video_files(directory):
    """查找目录下所有视频文件：优先 seg_*.ts（kazumi缓存），否则 *.mp4。
    返回 (files: list, file_type: 'ts'|'mp4'|'none')
    """
    # 优先查找 TS 分片
    ts_files = list(Path(directory).rglob("seg_*.ts"))
    if ts_files:
        groups = defaultdict(list)
        for f in ts_files:
            groups[str(f.parent)].append(str(f))
        result = []
        for subdir in sorted(groups.keys()):
            result.extend(sorted(groups[subdir]))
        return result, 'ts'

    # 回退查找 MP4 文件
    mp4_files = sorted(Path(directory).rglob("*.mp4"))
    if mp4_files:
        return [str(f) for f in mp4_files], 'mp4'

    return [], 'none'


class KazumiProcessor:
    """番剧缓存处理器：合并 + 检测 + 报告"""

    def __init__(self, callback_progress=None, callback_done=None):
        """
        callback_progress(phase, percent, message) — 进度回调（主线程安全）
        callback_done(report) — 完成回调
        """
        self.cb_progress = callback_progress
        self.cb_done = callback_done
        self._cancel = threading.Event()
        self._gpu_ctx = None
        self._lk_cuda = None
        self._reader_thread = None
        self._reader_stop = None

    def cancel(self):
        self._cancel.set()

    def process(self, directory, scale=None, num_workers=None):
        """主入口：在后台线程调用。scale 默认 0.5。
        num_workers: 并行分段数，None=读配置，1=串行，>1=多线程分段并行处理
        """
        if scale is None:
            scale = 0.5
        if num_workers is None:
            num_workers = CONFIG.get("KAZUMI_PARALLEL_WORKERS", 1)
        if num_workers > 1:
            threading.Thread(target=self._run_parallel,
                           args=(directory, scale, num_workers), daemon=True).start()
        else:
            threading.Thread(target=self._run,
                           args=(directory, scale), daemon=True).start()

    def _report(self, phase, percent, msg):
        if self.cb_progress:
            self.cb_progress(phase, percent, msg)

    def _run(self, directory, scale):
        t_start = time.perf_counter()
        try:
            # ─── Phase 1: 扫描 ───
            self._report("扫描", 0, "查找视频文件...")
            video_files, file_type = find_video_files(directory)
            if not video_files:
                self._report("错误", 0, f"未找到视频文件（seg_*.ts / *.mp4）: {directory}")
                return
            file_label = "TS分片" if file_type == 'ts' else "MP4"
            self._report("扫描", 100, f"找到 {len(video_files)} 个{file_label}文件")

            if self._cancel.is_set():
                return

            # ─── Phase 2: FFmpeg 管道 ───
            self._report("准备", 0, "启动 FFmpeg 流式管道...")
            ffmpeg = find_ffmpeg()
            tmpdir = tempfile.mkdtemp(prefix="cel_kazumi_")
            # TS 分片跳过首段（通常只有头信息无完整关键帧），MP4 不跳过
            if file_type == 'ts' and len(video_files) > 1:
                _start_idx = 1
                video_files = video_files[_start_idx:]
            else:
                _start_idx = 0
            ts_effective = video_files  # 保持后续引用一致

            # 构建 concat 文件（TS 必须 concat；单一 MP4 也走 concat 以统一流程）
            concat_file = os.path.join(tmpdir, "concat.txt")
            with open(concat_file, "w", encoding="utf-8") as f:
                for vf in video_files:
                    # FFmpeg concat 用正斜杠，避免 Windows 反斜杠被误解析为转义符
                    f.write(f"file '{vf.replace(chr(92), '/')}'\n")

            # 探测视频参数
            ffdir = os.path.dirname(ffmpeg)
            ffprobe = os.path.join(ffdir, os.path.basename(ffmpeg).replace("ffmpeg", "ffprobe"))
            probe = subprocess.run(
                [ffprobe, "-v", "quiet", "-select_streams", "v:0",
                 "-show_entries", "stream=width,height,r_frame_rate,avg_frame_rate",
                 "-of", "csv=p=0", ts_effective[0]],
                capture_output=True, text=True, timeout=10)
            first_line = probe.stdout.strip().split("\n")[0]
            info = first_line.split(",")
            orig_w, orig_h = int(info[0]), int(info[1])

            def _parse_fps(s):
                """安全解析帧率字符串（如 24000/1001 或 23.976）"""
                if not s or s == '0/0':
                    return 0.0
                if '/' in s:
                    a, b = s.split('/')
                    return float(a) / float(b) if float(b) != 0 else 0.0
                return float(s)

            fps_r = _parse_fps(info[2])
            fps_avg = _parse_fps(info[3]) if len(info) > 3 else 0.0
            # 取两者中在合理范围(10~60)且偏小的值；avg_frame_rate 通常更可靠
            candidates = [f for f in (fps_avg, fps_r) if 10.0 < f < 60.0]
            video_fps = candidates[0] if candidates else (fps_avg or fps_r or 24.0)
            CROP_W, CROP_H = 0.8, 0.75
            out_w = int(orig_w * CROP_W * scale) // 2 * 2
            out_h = int(orig_h * CROP_H * scale) // 2 * 2
            frame_bytes = out_w * out_h

            # 用 ffprobe 探测合并后视频的实际时长（精确帧数，规避文件大小估算偏差）
            try:
                dur_probe = subprocess.run(
                    [ffprobe, "-v", "quiet", "-f", "concat", "-safe", "0",
                     "-i", concat_file, "-show_entries", "format=duration",
                     "-of", "csv=p=0"],
                    capture_output=True, text=True, timeout=15)
                est_duration = float(dur_probe.stdout.strip())
            except Exception:
                est_duration = 0
            if est_duration <= 0:
                total_size = sum(os.path.getsize(f) for f in ts_effective)
                est_total_frames = int(total_size * 8 / (orig_w * orig_h * 0.15))  # ~0.15 bit/pixel
                est_duration = est_total_frames / video_fps if video_fps > 0 else len(ts_effective) * 6
            est_total_frames = int(est_duration * video_fps)
            self._report("准备", 50, f"管道就绪 | {out_w}x{out_h} | 预估{est_total_frames}帧~{est_duration/60:.1f}分")

            # FFmpeg 管道命令
            pipe_cmd = [
                ffmpeg, "-f", "concat", "-safe", "0", "-i", concat_file,
                "-vf", f"crop=iw*{CROP_W}:ih*{CROP_H}:iw*0.1:ih*0.1,scale={out_w}:{out_h},format=gray",
                "-f", "rawvideo", "-pix_fmt", "gray", "-vsync", "0", "-an", "-threads", "1",
                "pipe:1"
            ]
            proc = subprocess.Popen(pipe_cmd, stdin=subprocess.DEVNULL,
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

            # ─── Phase 3: 初始化 GPU（逐项自检，只有光流链路可用才启用）───
            self._report("准备", 50, "初始化 GPU...")
            use_gpu = False
            self._gpu_ctx = None
            self._lk_cuda = None
            info = gpu_probe()
            if info.flow_ready and not CONFIG.get("GPU_FORCE_FALLBACK", False):
                try:
                    self._gpu_ctx = GPUContext(info)
                    self._lk_cuda = self._gpu_ctx.lk
                    use_gpu = True
                except Exception as e:  # noqa: BLE001
                    self._gpu_ctx = None
                    self._lk_cuda = None
                    print(f"[GPU] 初始化失败: {e}")
            self._report("检测", 0,
                         f"{video_fps:.0f}fps | 预估{est_total_frames}帧~{est_duration/60:.1f}分 "
                         f"| {'GPU 光流' if use_gpu else 'CPU'}")

            # ─── Phase 4: 启动读取线程（producer）+ 帧队列 ───
            # 线程数：1 读取线程 + 1 检测线程（当前主线程）= 2 个 Python 线程 + FFmpeg 子进程
            frame_queue = queue.Queue(maxsize=120)
            all_hashes = []  # [(frame_idx, ts, hash_64), ...] 用于 OP/ED 批量后处理
            self._reader_stop = threading.Event()

            def reader_loop():
                """读取线程：从 FFmpeg stdout 读裸帧 → numpy → 队列"""
                buf = bytearray(frame_bytes)
                idx = 0
                try:
                    while not self._reader_stop.is_set() and not self._cancel.is_set():
                        n = proc.stdout.readinto(buf)
                        if n < frame_bytes:
                            break
                        ts = idx / video_fps if video_fps > 0 else 0.0
                        # .copy() 确保数据独立于 buf
                        gray = np.frombuffer(buf, dtype=np.uint8).reshape(out_h, out_w).copy()
                        # 计算 OP/ED 哈希（在主线程外完成，分摊 CPU）
                        try:
                            hash_256, oped_hash = compute_all_hashes(gray)
                        except Exception:
                            hash_256, oped_hash = None, None
                        frame_queue.put((idx, ts, gray, hash_256, oped_hash), timeout=1.0)
                        idx += 1
                except Exception:
                    pass
                finally:
                    frame_queue.put(None)  # 结束哨兵

            self._reader_thread = threading.Thread(target=reader_loop, daemon=True)
            self._reader_thread.start()

            # ─── Phase 5: 检测（主线程消费队列）───
            last_frame = None
            frame_buffer = deque(maxlen=CONFIG["FRAME_BUFFER_SIZE"])
            raw_timestamps = deque()
            full_filter_active = False
            full_filter_active_until = 0.0

            total_cels = 0
            total_raw = 0
            total_translation = 0; total_flow = 0; total_hash = 0
            total_still = 0; total_local = 0; total_zoom = 0; total_other = 0

            recent_cels = deque(); recent_raw = deque()
            recent_sum_cels = 0; recent_sum_raw = 0

            frame_count = 0
            last_report_time = 0
            # OP/ED 哈希采样间隔
            oped_sample_interval = CONFIG.get("OPED_SAMPLE_INTERVAL_SEC", 1.0)
            last_oped_sample_ts = -999

            while not self._cancel.is_set():
                try:
                    item = frame_queue.get(timeout=0.5)
                except queue.Empty:
                    continue
                if item is None:
                    break
                frame_idx, ts, now_frame, hash_256, oped_hash = item
                frame_count = frame_idx + 1

                # 采样哈希用于 OP/ED 批量后处理
                if oped_hash is not None and ts - last_oped_sample_ts >= oped_sample_interval:
                    all_hashes.append((frame_idx, ts, oped_hash))
                    last_oped_sample_ts = ts

                if last_frame is None or last_frame.shape != now_frame.shape:
                    last_frame = now_frame
                    continue

                # --- 差分（统一走 CPU；GPU 只负责大帧的角点+光流）---
                _, _, pre_ratio = compute_diff_mask(last_frame, now_frame)

                raw_new = 1 if pre_ratio >= CONFIG["MIN_CHANGE_RATIO"] else 0
                total_raw += raw_new

                basic_detected, move_type = basic_is_new_cel(last_frame, now_frame, precomputed_ratio=pre_ratio)
                if basic_detected:
                    raw_timestamps.append(ts)

                window_sec = CONFIG["FILTER_TRIGGER_WINDOW_MS"] / 1000.0
                while raw_timestamps and raw_timestamps[0] < ts - window_sec:
                    raw_timestamps.popleft()

                if len(raw_timestamps) >= CONFIG["FILTER_TRIGGER_COUNT"]:
                    if not full_filter_active: full_filter_active = True
                    full_filter_active_until = ts + CONFIG["FULL_FILTER_HOLD_SEC"]
                elif full_filter_active and ts > full_filter_active_until:
                    full_filter_active = False

                # --- 重复帧过滤 ---
                duplicate = False
                if hash_256 is not None:
                    for h in frame_buffer:
                        if np.sum(hash_256 != h) <= CONFIG["HASH_THRESHOLD"]:
                            duplicate = True
                            break
                    frame_buffer.append(hash_256)

                if full_filter_active:
                    # GPU：按像素门槛决定是否上传整帧（小帧上传反而更慢）
                    uploaded = False
                    if (use_gpu and self._gpu_ctx is not None
                            and self._gpu_ctx.usable_for_flow(now_frame.shape)):
                        try:
                            self._gpu_ctx.upload_pair(last_frame, now_frame)
                            uploaded = True
                        except Exception:  # noqa: BLE001
                            uploaded = False
                    final_detected, move_type = full_is_new_cel(
                        last_frame, now_frame, use_optical_flow=True,
                        use_gpu=use_gpu, lk_cuda=self._lk_cuda,
                        precomputed_ratio=pre_ratio,
                        gpu_ctx=self._gpu_ctx if uploaded else None,
                        frames_uploaded=uploaded)
                else:
                    final_detected = basic_detected

                new_cel = 1 if (final_detected and not duplicate) else 0
                if raw_new == 1 and new_cel == 0:
                    if duplicate and final_detected:
                        total_hash += 1
                    elif move_type == "全局平移": total_translation += 1
                    elif move_type == "图层分离运镜": total_flow += 1
                    elif move_type == "缩放运镜": total_zoom += 1
                    elif move_type in ("极慢平移/静止", "静止"): total_still += 1
                    elif move_type == "局部变化(过滤)": total_local += 1
                    else: total_other += 1

                total_cels += new_cel

                recent_cels.append((ts, new_cel)); recent_sum_cels += new_cel
                while recent_cels and ts - recent_cels[0][0] > 1.0:
                    recent_sum_cels -= recent_cels.popleft()[1]

                last_frame = now_frame

                # 进度报告（每 0.5 秒一次）
                if ts - last_report_time >= 0.5:
                    pct = min(frame_count / est_total_frames * 100, 99.9)
                    span = ts - recent_cels[0][0] if recent_cels else 0.001
                    cps = recent_sum_cels / max(span, 0.001) if recent_cels else 0.0
                    elapsed = time.perf_counter() - t_start
                    self._report("检测", pct,
                                 f"帧{frame_count} | {cps:.1f}张/秒 | 累计{total_cels}张 | "
                                 f"{'完整' if full_filter_active else '基础'} | {elapsed:.0f}s")
                    last_report_time = ts

            # 等待读取线程结束
            self._reader_stop.set()
            if self._reader_thread and self._reader_thread.is_alive():
                self._reader_thread.join(timeout=3)

            # ─── 清理 ───
            try: proc.kill()
            except: pass
            if self._gpu_ctx:
                self._gpu_ctx.release()
            shutil.rmtree(tmpdir, ignore_errors=True)

            # 以 ffprobe 探测的合并时长为准（避免 r_frame_rate 异常偏高导致时长偏短）
            actual_duration = est_duration if est_duration > 0 else (
                frame_count / video_fps if video_fps > 0 else 0)

            # ─── Phase 6: OP/ED 批量后处理（遍历哈希查找重复段）───
            self._report("OP/ED", 0, f"遍历 {len(all_hashes)} 个哈希样本检测 OP/ED...")
            oped_intervals = self._detect_oped_batch(all_hashes)
            oped_cels_deducted = 0
            oped_time_deducted = 0.0
            if oped_intervals:
                avg_cps = total_cels / actual_duration if actual_duration > 0 else 0
                for op_start, op_end in oped_intervals:
                    dur = op_end - op_start
                    oped_time_deducted += dur
                    oped_cels_deducted += int(dur * avg_cps)
                self._report("OP/ED", 100,
                             f"检测到 {len(oped_intervals)} 个 OP/ED 段，扣除 ~{oped_cels_deducted} 张 / {oped_time_deducted:.1f}s")

            elapsed = time.perf_counter() - t_start
            net_cels = max(0, total_cels - oped_cels_deducted)
            net_time = actual_duration - oped_time_deducted
            avg_cels = net_cels / net_time if net_time > 0 else 0

            report = {
                "directory": directory,
                "segments": len(ts_effective),
                "file_type": file_type,
                "duration_sec": round(actual_duration, 1),
                "duration_min": round(actual_duration / 60, 1),
                "total_frames": frame_count,
                "video_fps": round(video_fps, 1),
                "total_raw": total_raw,
                "total_cels": total_cels,
                "net_cels": net_cels,
                "oped_deducted_cels": oped_cels_deducted,
                "oped_deducted_time": round(oped_time_deducted, 1),
                "oped_intervals": len(oped_intervals),
                "avg_cels_per_sec": round(avg_cels, 2),
                "elapsed_sec": round(elapsed, 1),
                "speedup": round(actual_duration / elapsed, 1) if elapsed > 0 else 0,
                "filtering": {
                    "translation": total_translation,
                    "optical_flow": total_flow,
                    "hash": total_hash,
                    "still": total_still,
                    "local": total_local,
                    "zoom": total_zoom,
                    "other": total_other,
                },
                "scale": scale,
            }
            self._report("完成", 100,
                         f"{net_cels} 张 (扣OP/ED {oped_cels_deducted}) | 平均 {avg_cels:.1f} 张/秒 | 耗时 {elapsed:.0f}s")
            if self.cb_done:
                self.cb_done(report)

        except Exception as e:
            import traceback
            self._report("错误", 0, f"处理异常: {e}\n{traceback.format_exc()}")

    def _detect_oped_batch(self, all_hashes):
        """批量 OP/ED 检测：单次遍历哈希序列，找到重复出现的片段

        all_hashes: [(frame_idx, ts, hash_64), ...]  按时间排序的哈希采样
        返回: [(start_ts, end_ts), ...]  OP/ED 区间列表
        """
        if not all_hashes or not CONFIG.get("OPED_HASH_ENABLED", True):
            return []

        enter_dist = CONFIG.get("OPED_ENTER_HAMMING", 5)
        min_duration = CONFIG.get("OPED_MIN_DURATION", 30)
        self_exclude = CONFIG.get("OPED_SELF_EXCLUDE_SEC", 90)
        search_window = self_exclude * 2  # 最多往前搜索这么久

        n = len(all_hashes)
        # 标记每帧是否为 OP/ED 候选（找到更早的历史匹配）
        is_oped = [False] * n

        # 单次前向遍历 + 有限回溯
        for i in range(n):
            frame_idx_i, ts_i, h_i = all_hashes[i]
            # 向后搜索（时间由近到远），限制在 search_window 内
            for j in range(i - 1, -1, -1):
                frame_idx_j, ts_j, h_j = all_hashes[j]
                dt = ts_i - ts_j
                if dt < self_exclude:
                    continue          # 仍在自排除窗口内
                if dt > search_window:
                    break            # 超出搜索范围，停止
                if (h_i ^ h_j).bit_count() <= enter_dist:
                    is_oped[i] = True
                    break

        # 将连续 True 合并为区间
        intervals = []
        in_seg = False
        seg_start = None
        for i in range(n):
            if is_oped[i] and not in_seg:
                in_seg = True
                seg_start = all_hashes[i][1]
            elif not is_oped[i] and in_seg:
                seg_end = all_hashes[i - 1][1]
                if seg_end - seg_start >= min_duration:
                    intervals.append((seg_start, seg_end))
                in_seg = False
        if in_seg:
            seg_end = all_hashes[-1][1]
            if seg_end - seg_start >= min_duration:
                intervals.append((seg_start, seg_end))

        # 合并间隙 ≤1 秒的相邻区间（同一 OP/ED 因短暂不匹配被断开）
        if len(intervals) > 1:
            intervals.sort()
            merged = [intervals[0]]
            for start, end in intervals[1:]:
                if start - merged[-1][1] <= 1.0:
                    merged[-1] = (merged[-1][0], max(merged[-1][1], end))
                else:
                    merged.append((start, end))
            intervals = merged

        return intervals

    # ==================== 多线程分段并行处理 ====================

    def _run_parallel(self, directory, scale, num_workers):
        """多线程分段并行：将视频分为 num_workers 段，每段独立线程检测，最后合并"""
        t_start = time.perf_counter()
        try:
            # ─── Phase 1: 扫描 ───
            self._report("扫描", 0, "查找视频文件...")
            video_files, file_type = find_video_files(directory)
            if not video_files:
                self._report("错误", 0, f"未找到视频文件（seg_*.ts / *.mp4）: {directory}")
                return
            file_label = "TS分片" if file_type == 'ts' else "MP4"
            self._report("扫描", 100, f"找到 {len(video_files)} 个{file_label}文件")

            if self._cancel.is_set():
                return

            # ─── Phase 2: 准备 ───
            self._report("准备", 0, "启动 FFmpeg 并行管道...")
            ffmpeg_path = find_ffmpeg()
            tmpdir = tempfile.mkdtemp(prefix="cel_kazumi_")
            # TS 分片跳过首段（通常只有头信息无完整关键帧），MP4 不跳过
            if file_type == 'ts' and len(video_files) > 1:
                _start_idx = 1
                video_files = video_files[_start_idx:]
            else:
                _start_idx = 0
            ts_effective = video_files

            concat_file = os.path.join(tmpdir, "concat.txt")
            with open(concat_file, "w", encoding="utf-8") as f:
                for vf in video_files:
                    # FFmpeg concat 用正斜杠，避免 Windows 反斜杠被误解析为转义符
                    f.write(f"file '{vf.replace(chr(92), '/')}'\n")

            # 探测视频参数
            ffdir = os.path.dirname(ffmpeg_path)
            ffprobe = os.path.join(ffdir, os.path.basename(ffmpeg_path).replace("ffmpeg", "ffprobe"))
            probe = subprocess.run(
                [ffprobe, "-v", "quiet", "-select_streams", "v:0",
                 "-show_entries", "stream=width,height,r_frame_rate,avg_frame_rate",
                 "-of", "csv=p=0", ts_effective[0]],
                capture_output=True, text=True, timeout=10)
            first_line = probe.stdout.strip().split("\n")[0]
            info = first_line.split(",")
            orig_w, orig_h = int(info[0]), int(info[1])

            def _parse_fps(s):
                """安全解析帧率字符串（如 24000/1001 或 23.976）"""
                if not s or s == '0/0':
                    return 0.0
                if '/' in s:
                    a, b = s.split('/')
                    return float(a) / float(b) if float(b) != 0 else 0.0
                return float(s)

            fps_r = _parse_fps(info[2])
            fps_avg = _parse_fps(info[3]) if len(info) > 3 else 0.0
            # 取两者中在合理范围(10~60)且偏小的值；avg_frame_rate 通常更可靠
            candidates = [f for f in (fps_avg, fps_r) if 10.0 < f < 60.0]
            video_fps = candidates[0] if candidates else (fps_avg or fps_r or 24.0)
            CROP_W, CROP_H = 0.8, 0.75
            out_w = int(orig_w * CROP_W * scale) // 2 * 2
            out_h = int(orig_h * CROP_H * scale) // 2 * 2
            frame_bytes = out_w * out_h

            # 精确时长
            try:
                dur_probe = subprocess.run(
                    [ffprobe, "-v", "quiet", "-f", "concat", "-safe", "0",
                     "-i", concat_file, "-show_entries", "format=duration",
                     "-of", "csv=p=0"],
                    capture_output=True, text=True, timeout=15)
                est_duration = float(dur_probe.stdout.strip())
            except Exception:
                est_duration = 0
            if est_duration <= 0:
                total_size = sum(os.path.getsize(f) for f in ts_effective)
                est_total_frames = int(total_size * 8 / (orig_w * orig_h * 0.15))
                est_duration = est_total_frames / video_fps if video_fps > 0 else len(ts_effective) * 6
            est_total_frames = int(est_duration * video_fps)

            self._report("准备", 40,
                         f"管道就绪 | {out_w}x{out_h} | 预估{est_total_frames}帧~{est_duration/60:.1f}分 | "
                         f"{num_workers}路并行")

            # ─── Phase 3: 划分时间段 ───
            seg_dur = est_duration / num_workers
            boundaries = []
            for i in range(num_workers):
                seg_start = i * seg_dur
                seg_end = min((i + 1) * seg_dur, est_duration)
                if seg_end > seg_start + 1:  # 至少 1 秒
                    boundaries.append((seg_start, seg_end))
            actual_workers = len(boundaries)
            self._report("准备", 60,
                         f"分为 {actual_workers} 段: " +
                         ", ".join(f"[{s:.0f}-{e:.0f}s]" for s, e in boundaries))

            # ─── Phase 4: 并行检测 ───
            seg_results = [None] * actual_workers
            seg_hashes = [None] * actual_workers  # 每段的哈希列表
            seg_progress = [0.0] * actual_workers
            progress_lock = threading.Lock()
            done_count = [0]

            def seg_progress_cb(seg_id, seg_pct, msg):
                with progress_lock:
                    seg_progress[seg_id] = seg_pct
                    total_pct = sum(seg_progress) / actual_workers
                elapsed = time.perf_counter() - t_start
                self._report("检测", min(total_pct, 99.9),
                             f"[P{seg_id+1}/{actual_workers}] {msg} | 总耗时{elapsed:.0f}s")

            def seg_done_cb(seg_id):
                with progress_lock:
                    done_count[0] += 1
                seg_progress_cb(seg_id, 100, f"段{seg_id+1}完成")

            def worker(seg_id, seg_start, seg_end):
                """单个分段的检测线程"""
                try:
                    res, hashes = self._process_segment(
                        seg_id, seg_start, seg_end,
                        concat_file, out_w, out_h, video_fps,
                        frame_bytes, ffmpeg_path, ffprobe,
                        CROP_W, CROP_H, scale,
                        lambda pct, msg: seg_progress_cb(seg_id, pct, msg)
                    )
                    seg_results[seg_id] = res
                    seg_hashes[seg_id] = hashes
                    seg_done_cb(seg_id)
                except Exception as e:
                    import traceback
                    seg_results[seg_id] = {"error": str(e), "traceback": traceback.format_exc()}
                    seg_hashes[seg_id] = []
                    seg_done_cb(seg_id)

            threads = []
            for i, (seg_start, seg_end) in enumerate(boundaries):
                t = threading.Thread(target=worker, args=(i, seg_start, seg_end), daemon=True)
                threads.append(t)
                t.start()

            for t in threads:
                while t.is_alive() and not self._cancel.is_set():
                    t.join(timeout=0.2)
                if self._cancel.is_set():
                    self._report("检测", sum(seg_progress) / actual_workers, "已取消")
                    return

            # 检查错误
            for i, r in enumerate(seg_results):
                if r and "error" in r:
                    self._report("错误", 0, f"段{i+1} 失败: {r['error']}")
                    return

            # ─── Phase 5: 合并结果 ───
            self._report("合并", 0, f"合并 {actual_workers} 段结果...")
            merged = _merge_segments(seg_results)
            all_hashes_global = []
            for hashes in seg_hashes:
                if hashes:
                    all_hashes_global.extend(hashes)
            all_hashes_global.sort(key=lambda x: x[1])  # 按时间戳排序
            self._report("合并", 50,
                         f"总帧{merged['frame_count']} | 过滤后{merged['total_cels']}张 | "
                         f"哈希样本{len(all_hashes_global)}个")

            # ─── Phase 6: OP/ED 批量检测（跨段合并后的哈希）───
            # 以 ffprobe 探测的合并时长为准（避免 r_frame_rate 异常偏高导致时长偏短）
            actual_duration = est_duration if est_duration > 0 else (
                merged['frame_count'] / video_fps if video_fps > 0 else 0)
            self._report("OP/ED", 0, f"遍历 {len(all_hashes_global)} 个哈希样本检测 OP/ED...")
            oped_intervals = self._detect_oped_batch(all_hashes_global)
            oped_cels_deducted = 0
            oped_time_deducted = 0.0
            if oped_intervals:
                avg_cps = merged['total_cels'] / actual_duration if actual_duration > 0 else 0
                for op_start, op_end in oped_intervals:
                    dur = op_end - op_start
                    oped_time_deducted += dur
                    oped_cels_deducted += int(dur * avg_cps)
                self._report("OP/ED", 100,
                             f"检测到 {len(oped_intervals)} 个 OP/ED 段，扣除 ~{oped_cels_deducted} 张 / {oped_time_deducted:.1f}s")

            # ─── 清理 ───
            shutil.rmtree(tmpdir, ignore_errors=True)

            # ─── 生成报告 ───
            elapsed = time.perf_counter() - t_start
            net_cels = max(0, merged['total_cels'] - oped_cels_deducted)
            net_time = actual_duration - oped_time_deducted
            avg_cels = net_cels / net_time if net_time > 0 else 0

            report = {
                "directory": directory,
                "segments": len(ts_effective),
                "file_type": file_type,
                "duration_sec": round(actual_duration, 1),
                "duration_min": round(actual_duration / 60, 1),
                "total_frames": merged['frame_count'],
                "video_fps": round(video_fps, 1),
                "total_raw": merged['total_raw'],
                "total_cels": merged['total_cels'],
                "net_cels": net_cels,
                "oped_deducted_cels": oped_cels_deducted,
                "oped_deducted_time": round(oped_time_deducted, 1),
                "oped_intervals": len(oped_intervals),
                "avg_cels_per_sec": round(avg_cels, 2),
                "elapsed_sec": round(elapsed, 1),
                "speedup": round(actual_duration / elapsed, 1) if elapsed > 0 else 0,
                "parallel_workers": actual_workers,
                "filtering": {
                    "translation": merged['filtering']['translation'],
                    "optical_flow": merged['filtering']['optical_flow'],
                    "hash": merged['filtering']['hash'],
                    "still": merged['filtering']['still'],
                    "local": merged['filtering']['local'],
                    "zoom": merged['filtering']['zoom'],
                    "other": merged['filtering']['other'],
                },
                "scale": scale,
            }
            self._report("完成", 100,
                         f"{net_cels} 张 (扣OP/ED {oped_cels_deducted}) | 平均 {avg_cels:.1f} 张/秒 | "
                         f"耗时 {elapsed:.0f}s ({actual_workers}路并行)")
            if self.cb_done:
                self.cb_done(report)

        except Exception as e:
            import traceback
            self._report("错误", 0, f"处理异常: {e}\n{traceback.format_exc()}")

    def _process_segment(self, seg_id, seg_start, seg_end,
                         concat_file, out_w, out_h, video_fps,
                         frame_bytes, ffmpeg_path, ffprobe_path,
                         CROP_W, CROP_H, scale, progress_cb):
        """处理单个时间段：[seg_start, seg_end)，返回 (result_dict, hash_list)"""
        t0 = time.perf_counter()

        # ─── 启动 FFmpeg（-ss 分段起始，-t 持续时长）───
        seg_duration = seg_end - seg_start
        pipe_cmd = [
            ffmpeg_path, "-ss", str(seg_start), "-t", str(seg_duration),
            "-f", "concat", "-safe", "0", "-i", concat_file,
            "-vf", f"crop=iw*{CROP_W}:ih*{CROP_H}:iw*0.1:ih*0.1,scale={out_w}:{out_h},format=gray",
            "-f", "rawvideo", "-pix_fmt", "gray", "-vsync", "0", "-an", "-threads", "1",
            "pipe:1"
        ]
        proc = subprocess.Popen(pipe_cmd, stdin=subprocess.DEVNULL,
                                 stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

        # ─── 初始化 GPU（每线程独立上下文；逐项自检通过才启用）───
        use_gpu = False
        gpu_ctx = None
        lk_cuda = None
        info = gpu_probe()
        if info.flow_ready and not CONFIG.get("GPU_FORCE_FALLBACK", False):
            try:
                gpu_ctx = GPUContext(info)
                lk_cuda = gpu_ctx.lk
                use_gpu = True
            except Exception:  # noqa: BLE001
                gpu_ctx = None
                lk_cuda = None

        # ─── 帧读取线程 ───
        frame_queue = queue.Queue(maxsize=120)
        reader_stop = threading.Event()

        def reader():
            buf = bytearray(frame_bytes)
            idx = 0
            try:
                while not reader_stop.is_set() and not self._cancel.is_set():
                    n = proc.stdout.readinto(buf)
                    if n < frame_bytes:
                        break
                    # 全局时间戳 = 段起始 + 帧内偏移
                    ts = seg_start + idx / video_fps if video_fps > 0 else seg_start
                    gray = np.frombuffer(buf, dtype=np.uint8).reshape(out_h, out_w).copy()
                    try:
                        hash_256, oped_hash = compute_all_hashes(gray)
                    except Exception:
                        hash_256, oped_hash = None, None
                    frame_queue.put((idx, ts, gray, hash_256, oped_hash), timeout=1.0)
                    idx += 1
            except Exception:
                pass
            finally:
                frame_queue.put(None)

        reader_thread = threading.Thread(target=reader, daemon=True)
        reader_thread.start()

        # ─── 检测循环 ───
        last_frame = None
        frame_buffer = deque(maxlen=CONFIG["FRAME_BUFFER_SIZE"])
        raw_timestamps = deque()
        full_filter_active = False
        full_filter_active_until = 0.0

        total_cels = 0; total_raw = 0
        total_translation = 0; total_flow = 0; total_hash = 0
        total_still = 0; total_local = 0; total_zoom = 0; total_other = 0

        recent_cels = deque()
        recent_sum_cels = 0

        frame_count = 0
        seg_hashes = []       # 本段的 OP/ED 哈希采样
        oped_sample_interval = CONFIG.get("OPED_SAMPLE_INTERVAL_SEC", 1.0)
        last_oped_sample_ts = -999
        last_report_time = seg_start
        seg_est_frames = int(seg_duration * video_fps)

        while not self._cancel.is_set():
            try:
                item = frame_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if item is None:
                break
            frame_idx, ts, now_frame, hash_256, oped_hash = item
            frame_count = frame_idx + 1

            # 采样哈希
            if oped_hash is not None and ts - last_oped_sample_ts >= oped_sample_interval:
                seg_hashes.append((frame_idx, ts, oped_hash))
                last_oped_sample_ts = ts

            if last_frame is None or last_frame.shape != now_frame.shape:
                last_frame = now_frame
                continue

            # 差分（统一走 CPU；GPU 只负责大帧的角点+光流）
            _, _, pre_ratio = compute_diff_mask(last_frame, now_frame)

            raw_new = 1 if pre_ratio >= CONFIG["MIN_CHANGE_RATIO"] else 0
            total_raw += raw_new

            basic_detected, move_type = basic_is_new_cel(last_frame, now_frame, precomputed_ratio=pre_ratio)
            if basic_detected:
                raw_timestamps.append(ts)

            window_sec = CONFIG["FILTER_TRIGGER_WINDOW_MS"] / 1000.0
            while raw_timestamps and raw_timestamps[0] < ts - window_sec:
                raw_timestamps.popleft()

            if len(raw_timestamps) >= CONFIG["FILTER_TRIGGER_COUNT"]:
                if not full_filter_active: full_filter_active = True
                full_filter_active_until = ts + CONFIG["FULL_FILTER_HOLD_SEC"]
            elif full_filter_active and ts > full_filter_active_until:
                full_filter_active = False

            duplicate = False
            if hash_256 is not None:
                for h in frame_buffer:
                    if np.sum(hash_256 != h) <= CONFIG["HASH_THRESHOLD"]:
                        duplicate = True
                        break
                frame_buffer.append(hash_256)

            if full_filter_active:
                uploaded = False
                if (use_gpu and gpu_ctx is not None
                        and gpu_ctx.usable_for_flow(now_frame.shape)):
                    try:
                        gpu_ctx.upload_pair(last_frame, now_frame)
                        uploaded = True
                    except Exception:  # noqa: BLE001
                        uploaded = False
                final_detected, move_type = full_is_new_cel(
                    last_frame, now_frame, use_optical_flow=True,
                    use_gpu=use_gpu, lk_cuda=lk_cuda,
                    precomputed_ratio=pre_ratio,
                    gpu_ctx=gpu_ctx if uploaded else None,
                    frames_uploaded=uploaded)
            else:
                final_detected = basic_detected

            new_cel = 1 if (final_detected and not duplicate) else 0
            if raw_new == 1 and new_cel == 0:
                if duplicate and final_detected:
                    total_hash += 1
                elif move_type == "全局平移": total_translation += 1
                elif move_type == "图层分离运镜": total_flow += 1
                elif move_type == "缩放运镜": total_zoom += 1
                elif move_type in ("极慢平移/静止", "静止"): total_still += 1
                elif move_type == "局部变化(过滤)": total_local += 1
                else: total_other += 1

            total_cels += new_cel

            recent_cels.append((ts, new_cel)); recent_sum_cels += new_cel
            while recent_cels and ts - recent_cels[0][0] > 1.0:
                recent_sum_cels -= recent_cels.popleft()[1]

            last_frame = now_frame

            # 进度回调
            if ts - last_report_time >= 0.5:
                seg_pct = min(frame_count / seg_est_frames * 100, 99.9) if seg_est_frames > 0 else 0
                span = ts - recent_cels[0][0] if recent_cels else 0.001
                cps = recent_sum_cels / max(span, 0.001) if recent_cels else 0.0
                seg_elapsed = time.perf_counter() - t0
                progress_cb(seg_pct,
                            f"帧{frame_count} | {cps:.1f}张/秒 | 累计{total_cels}张 | {seg_elapsed:.0f}s")
                last_report_time = ts

        # 清理
        reader_stop.set()
        reader_thread.join(timeout=3)
        try: proc.kill()
        except: pass
        if gpu_ctx:
            gpu_ctx.release()

        seg_elapsed = time.perf_counter() - t0
        result = {
            'frame_count': frame_count,
            'total_cels': total_cels,
            'total_raw': total_raw,
            'elapsed': seg_elapsed,
            'filtering': {
                'translation': total_translation,
                'optical_flow': total_flow,
                'hash': total_hash,
                'still': total_still,
                'local': total_local,
                'zoom': total_zoom,
                'other': total_other,
            },
        }
        return result, seg_hashes


def _merge_segments(seg_results):
    """合并多个分段的检测结果"""
    merged = {
        'frame_count': 0,
        'total_cels': 0,
        'total_raw': 0,
        'filtering': {
            'translation': 0, 'optical_flow': 0, 'hash': 0,
            'still': 0, 'local': 0, 'zoom': 0, 'other': 0,
        },
    }
    for r in seg_results:
        if r is None or 'error' in r:
            continue
        merged['frame_count'] += r['frame_count']
        merged['total_cels'] += r['total_cels']
        merged['total_raw'] += r['total_raw']
        for k in merged['filtering']:
            merged['filtering'][k] += r['filtering'].get(k, 0)
    return merged
