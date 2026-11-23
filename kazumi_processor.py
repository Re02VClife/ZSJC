"""
番剧缓存处理器：合并 TS 分片 → 离线检测 → 生成报告。
GUI 线程安全：处理在后台线程执行，通过回调更新进度。
"""
import os, sys, time, threading, subprocess, tempfile, json, shutil, queue, re
from pathlib import Path
from collections import deque, defaultdict

import numpy as np
from config import CONFIG
from detection import (
    compute_diff_mask,
    compute_all_hashes,
    basic_is_new_cel, full_is_new_cel, hash_distance,
    GPUContext, gpu_probe,
)
from video_source import FrameSource


# 打包成 windowed exe 后，父进程没有控制台，子进程（ffmpeg/ffprobe.exe）
# 会各自分配一个新的控制台窗口弹在桌面上——4 路并行就是 4 个黑框。
# 统一加 CREATE_NO_WINDOW 把它们藏掉。
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def _queue_put_blocking(q, item, stop_events=(), timeout=0.2, max_wait=None):
    """往有界队列放数据：满了继续等，不让生产者静默退出。

    旧代码用 ``put(..., timeout=1.0)``：4 路并行时检测线程被 GIL 和光流拖慢，
    队列满 1 秒就抛 ``queue.Full``，异常被 ``except: pass`` 吞掉 → 读取线程结束 →
    这一段只跑了一部分（现象就是报告里的「实际检测」比「视频时长」少 20~30 分钟）。
    返回 False 表示收到停止信号、放弃这一条。
    """
    waited = 0.0
    while True:
        for ev in stop_events:
            if ev is not None and ev.is_set():
                return False
        try:
            q.put(item, timeout=timeout)
            return True
        except queue.Full:
            waited += timeout
            if max_wait is not None and waited >= max_wait:
                return False
            continue


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
            sp.check_call([c, "-version"], stdout=sp.DEVNULL, stderr=sp.DEVNULL,
                          creationflags=_NO_WINDOW)
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
    for subdir in sorted(groups.keys(), key=natural_key):
        result.extend(sorted(groups[subdir], key=natural_key))
    return result


def natural_key(path):
    """自然排序键：1,2,3,…,10 而不是 1,10,11,12,2

    kazumi 的集数目录名是纯数字，字符串排序会把 10 排到 2 前面，
    合并后的时间轴就和真实集序对不上（OP/ED 的集号会乱）。
    """
    parts = re.split(r"(\d+)", str(path).lower().replace("\\", "/"))
    return [(0, int(p)) if p.isdigit() else (1, p) for p in parts]


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
        for subdir in sorted(groups.keys(), key=natural_key):
            result.extend(sorted(groups[subdir], key=natural_key))
        return result, 'ts'

    # 回退查找 MP4 文件
    mp4_files = sorted(Path(directory).rglob("*.mp4"), key=natural_key)
    if mp4_files:
        return [str(f) for f in mp4_files], 'mp4'

    return [], 'none'


# ==================== 时长探测 ====================
# 注意：ffprobe 对 "-f concat" 输入的 format=duration 恒为 N/A
# （format_long_name = "Virtual concatenation script"），
# 不能再依赖 concat 探测总时长，否则会退化成"按文件大小估算"，
# 时长严重偏短（实测 2.6GB / 13 集只算出 47 分钟，真实 310 分钟），
# 并行分段还会按这个错误时长把后面的集数整段丢掉。


def probe_file_duration(ffprobe, path, timeout=20):
    """探测单个媒体「视频流」的时长（秒），失败返回 None。

    必须用 stream=duration，不能用 format=duration：
    mp4 的 format 时长是整条容器（含音频轨），音频比视频长时
    （下载中断、音画不同步、拼接残留）会严重高估。
    实测某集容器 1431 秒、视频流只有 625 秒，报告里的「视频时长」
    因此比真正能解码出来的多出 20~30 分钟。
    """
    for entries, sel in (("stream=duration", ["-select_streams", "v:0"]),
                         ("format=duration", [])):
        try:
            r = subprocess.run(
                [ffprobe, "-v", "error"] + sel + ["-show_entries", entries,
                 "-of", "csv=p=0", path],
                capture_output=True, text=True, timeout=timeout,
                encoding="utf-8", errors="ignore", creationflags=_NO_WINDOW)
            s = (r.stdout or "").strip().splitlines()
            if not s:
                continue
            v = float(s[0].strip())
            if v > 0:
                return v
        except Exception:
            continue
    return None


def probe_file_durations(ffprobe, path, timeout=20, with_container=True):
    """返回 (视频流时长, 容器时长)，取不到的为 None。用于识别「视频流比容器短」的文件。"""
    video = None
    container = None
    try:
        r = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=duration", "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="ignore", creationflags=_NO_WINDOW)
        s = (r.stdout or "").strip().splitlines()
        if s:
            video = float(s[0].strip())
    except Exception:
        video = None
    if with_container:
        try:
            r = subprocess.run(
                [ffprobe, "-v", "error", "-show_entries", "format=duration",
                 "-of", "csv=p=0", path],
                capture_output=True, text=True, timeout=timeout,
                encoding="utf-8", errors="ignore", creationflags=_NO_WINDOW)
            s = (r.stdout or "").strip().splitlines()
            if s:
                container = float(s[0].strip())
        except Exception:
            container = None
    return video, container


def _as_ffprobe(cmd):
    """把 ffmpeg 路径换成同目录的 ffprobe（已经是 ffprobe 就原样返回）"""
    base = os.path.basename(cmd or "")
    if "ffmpeg" not in base.lower():
        return cmd
    probe = base.replace("ffmpeg", "ffprobe")
    d = os.path.dirname(cmd)
    return os.path.join(d, probe) if d else probe


def probe_total_duration(ffprobe, files, max_workers=8, progress_cb=None):
    """逐文件探测「视频流」时长并求和。

    返回 dict:
      total       : 总时长（秒），探测失败时为 0
      per_file    : 与 files 等长的时长列表（失败项为 0）
      ok          : 成功探测的文件数
      failed      : 失败的文件数
      source      : 'per-file' | 'none'
      incomplete  : [(path, video_sec, container_sec), ...] 视频流明显短于容器
    """
    from concurrent.futures import ThreadPoolExecutor

    ffprobe = _as_ffprobe(ffprobe)
    n = len(files)
    per_file = [0.0] * n
    incomplete = []
    if n == 0:
        return {"total": 0.0, "per_file": per_file, "ok": 0, "failed": 0,
                "source": "none", "incomplete": incomplete}

    # TS 分片动辄上千个，每个多跑一次 ffprobe 不划算，只对分集文件做容器对比
    with_container = n <= 200

    workers = max(1, min(max_workers, n))
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        future_map = {
            ex.submit(probe_file_durations, ffprobe, f, 20, with_container): i
            for i, f in enumerate(files)
        }
        for fut in future_map:
            i = future_map[fut]
            try:
                video, container = fut.result()
            except Exception:
                video, container = None, None
            if video and video > 0:
                per_file[i] = video
                if container and video > 0 and container > video * 1.05 + 5:
                    incomplete.append((files[i], round(video, 1), round(container, 1)))
            done += 1
            if progress_cb and (done % 50 == 0 or done == n):
                progress_cb(done, n)

    ok = sum(1 for d in per_file if d > 0)
    total = sum(per_file)
    return {
        "total": total if ok else 0.0,
        "per_file": per_file,
        "ok": ok,
        "failed": n - ok,
        "source": "per-file" if ok else "none",
        "incomplete": incomplete,
    }


def _file_label(path):
    """给文件起个短名字：<番剧>/<集>/video.mp4 → 集号；分片 → 文件名"""
    p = Path(path)
    if p.name.lower().startswith("video."):
        return p.parent.name
    return p.name


def resolve_duration(ffprobe, files, estimate_fn=None, progress_cb=None):
    """取可信总时长：逐文件求和为主，失败时用 estimate_fn 兜底。

    返回 (duration_sec, source, meta)，source ∈ {'per-file', 'estimate', 'none'}
    """
    meta = probe_total_duration(ffprobe, files, progress_cb=progress_cb)
    if meta["total"] > 0:
        return meta["total"], "per-file", meta
    if estimate_fn is not None:
        try:
            est = float(estimate_fn())
            if est > 0:
                return est, "estimate", meta
        except Exception:
            pass
    return 0.0, "none", meta


def split_files_by_duration(files, durations, num_workers):
    """按「文件 + 时长」把文件切成 num_workers 组，让每组时长尽量均衡。

    返回 [(files_subset, duration_subset), ...]
    """
    n = len(files)
    if n == 0:
        return []
    if num_workers <= 1 or n == 1:
        return [(list(files), float(sum(durations)))]

    # 时长缺失时退化成均分文件数
    durs = [float(d) if d and d > 0 else 0.0 for d in durations]
    if sum(durs) <= 0:
        durs = [1.0] * n
    target = sum(durs) / num_workers

    groups = []
    cur_f, cur_d = [], 0.0
    for f, d in zip(files, durs):
        # 已经够一组了，且后面还留得下组数
        if cur_f and cur_d + d / 2 > target and len(groups) < num_workers - 1:
            groups.append((cur_f, cur_d))
            cur_f, cur_d = [], 0.0
        cur_f.append(f)
        cur_d += d
    if cur_f:
        groups.append((cur_f, cur_d))
    return groups


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
        self._busy = threading.Event()
        self._gpu_ctx = None
        self._lk_cuda = None
        self._reader_thread = None
        self._reader_stop = None

    @property
    def is_busy(self):
        """是否有任务在跑（用于 GUI 防止重复触发/重复弹窗）"""
        return self._busy.is_set()

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
        if self._busy.is_set():
            self._report("错误", 0, "已有任务在处理中，请等待完成或取消")
            return
        self._busy.set()
        self._cancel.clear()
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
                capture_output=True, text=True, timeout=10, creationflags=_NO_WINDOW)
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

            # ─── 时长探测（关键）───
            # ffprobe 对 "-f concat" 输入的 format=duration 恒为 N/A
            # （format_long_name = "Virtual concatenation script"），
            # 所以必须逐文件探测求和；只有全失败时才退化成按文件大小估算。
            self._report("准备", 40, f"探测 {len(ts_effective)} 个文件的时长...")

            def _estimate_by_size():
                total_size = sum(os.path.getsize(f) for f in ts_effective)
                frames = int(total_size * 8 / (orig_w * orig_h * 0.15))  # ~0.15 bit/pixel
                return frames / video_fps if video_fps > 0 else len(ts_effective) * 6

            est_duration, dur_source, dur_meta = resolve_duration(
                ffprobe, ts_effective, estimate_fn=_estimate_by_size,
                progress_cb=lambda d, n: self._report(
                    "准备", 40 + 20.0 * d / max(n, 1), f"探测时长 {d}/{n}..."))
            if est_duration <= 0:
                self._report("错误", 0, "无法探测视频总时长，已中止（避免只处理一小段）")
                return
            est_total_frames = int(est_duration * video_fps)
            self._report("准备", 60,
                         f"管道就绪 | {out_w}x{out_h} | 共{est_duration/60:.1f}分({est_total_frames}帧) "
                         f"| 时长来源: {dur_source}")

            # FFmpeg 管道命令
            pipe_cmd = [
                ffmpeg, "-f", "concat", "-safe", "0", "-i", concat_file,
                "-vf", f"crop=iw*{CROP_W}:ih*{CROP_H}:iw*0.1:ih*0.1,scale={out_w}:{out_h},format=gray",
                "-f", "rawvideo", "-pix_fmt", "gray", "-vsync", "0", "-an", "-threads", "1",
                "pipe:1"
            ]
            proc = subprocess.Popen(pipe_cmd, stdin=subprocess.DEVNULL,
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     creationflags=_NO_WINDOW)

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
                        if not _queue_put_blocking(
                                frame_queue, (idx, ts, gray, hash_256, oped_hash),
                                (self._reader_stop, self._cancel)):
                            break
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
            oped_sample_interval = CONFIG.get("OPED_BATCH_SAMPLE_SEC", 0.5)
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
                    all_hashes.append((frame_idx, ts, oped_hash, float(now_frame.mean())))
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
                        if hash_distance(hash_256, h) <= CONFIG["HASH_THRESHOLD"]:
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

            # 总时长用逐文件探测值；同时记录「实际解码到的时长」做交叉校验
            detected_duration = frame_count / video_fps if video_fps > 0 else 0.0
            actual_duration = est_duration if est_duration > 0 else detected_duration

            # ─── Phase 6: OP/ED 批量后处理（遍历哈希查找重复段）───
            # 串行路径暂不实现「按集固定扣头尾」，字段保持 0 以免报告缺键
            manual_cels = 0
            manual_time = 0.0
            manual_head = manual_tail = 0.0
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
            net_time = max(0.0, actual_duration - oped_time_deducted - manual_time)
            avg_cels = net_cels / net_time if net_time > 0 else 0

            report = {
                "directory": directory,
                "segments": len(ts_effective),
                "file_type": file_type,
                "duration_sec": round(actual_duration, 1),
                "duration_min": round(actual_duration / 60, 1),
                "detected_sec": round(detected_duration, 1),
                "detected_min": round(detected_duration / 60, 1),
                "duration_source": dur_source,
                "duration_files_ok": dur_meta.get("ok", 0),
                "duration_files_failed": dur_meta.get("failed", 0),
                "incomplete_files": [_file_label(p) for p, _v, _c in dur_meta.get("incomplete", [])],
                "incomplete_detail": dur_meta.get("incomplete", []),
                "total_frames": frame_count,
                "video_fps": round(video_fps, 3),
                "total_raw": total_raw,
                "total_cels": total_cels,
                "net_cels": net_cels,
                "oped_deducted_cels": oped_cels_deducted,
                "oped_deducted_time": round(oped_time_deducted, 1),
                "manual_deducted_cels": manual_cels,
                "manual_deducted_time": round(manual_time, 1),
                "manual_head_sec": manual_head,
                "manual_tail_sec": manual_tail,
                "oped_intervals": len(oped_intervals),
                "oped_interval_list": [[round(a, 1), round(b, 1)] for a, b in oped_intervals],
                "net_duration_sec": round(net_time, 1),
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
        finally:
            self._busy.clear()

    def _detect_oped_batch(self, all_hashes):
        """批量 OP/ED 检测：按「偏移分桶 + 匹配密度」找重复播放的片段。

        思路（容忍 OP/ED 内部有相当比例的画面不匹配）：
          1. 对每个采样点 i，在 [self_exclude, search_window] 的历史窗口里找
             汉明距离最小的 j，只保留距离 ≤ enter_hamming 的，记下偏移
             offset_i = ts_i - ts_j；
          2. 把偏移按 bucket 秒量化分桶 —— 同一段画面重复出现时，绝大多数匹配
             会落在同一个偏移上（即使中间有转场、字幕、快切导致部分帧匹配不上）；
          3. 每个桶内按时间聚簇（间隙 ≤ cluster_gap），簇的跨度 ≥ min_duration
             且**匹配密度**（匹配样本数 / 跨度秒数）≥ min_density 就算一段；
          4. 多桶结果合并去重。

        这样只要「哈希值大量匹配」就能检出，不需要整段逐帧一致。

        all_hashes: [(frame_idx, ts, hash_64), ...]  按时间排序的哈希采样
        返回: [(start_ts, end_ts), ...]  OP/ED 区间列表
        """
        if not all_hashes or not CONFIG.get("OPED_HASH_ENABLED", True):
            return []

        enter_dist = int(CONFIG.get("OPED_ENTER_HAMMING", 5))
        min_duration = float(CONFIG.get("OPED_MIN_DURATION", 30))
        self_exclude = float(CONFIG.get("OPED_SELF_EXCLUDE_SEC", 90))
        # 跨集找重复：OP/ED 每隔一集出现一次，间隔通常 20~25 分钟。
        # 旧版写死 self_exclude * 2 = 180 秒，永远够不到上一集，
        # 所以多集番剧从来检不到 OP/ED（报告里一直是「无」）。
        search_window = float(CONFIG.get("OPED_SEARCH_WINDOW_SEC", 1800))
        search_window = max(search_window, self_exclude + 1.0)
        bucket_sec = float(CONFIG.get("OPED_OFFSET_BUCKET_SEC", 3.0))
        max_gap = float(CONFIG.get("OPED_CLUSTER_GAP_SEC", 5.0))
        min_density = float(CONFIG.get("OPED_MATCH_MIN_DENSITY", 0.3))
        min_hits = int(CONFIG.get("OPED_MIN_MATCH_HITS", 8))
        win_sec = float(CONFIG.get("OPED_DENSITY_WINDOW_SEC", 15.0))

        n = len(all_hashes)

        # 位运算向量化：numpy >= 2.0 用 bitwise_count，老版本退化成查表
        hashes = np.fromiter((h[2] for h in all_hashes), dtype=np.uint64, count=n)
        times = np.fromiter((h[1] for h in all_hashes), dtype=np.float64, count=n)
        # 亮度（第 4 个字段，老数据没有就跳过该校验）
        if len(all_hashes[0]) >= 4:
            bright = np.fromiter((float(h[3]) for h in all_hashes), dtype=np.float32, count=n)
        else:
            bright = None
        bright_tol = float(CONFIG.get("OPED_BRIGHT_TOL", 22.0))

        _popcount = getattr(np, "bitwise_count", None)
        if _popcount is None:  # numpy < 2.0 兜底
            _LUT = np.array([bin(i).count("1") for i in range(256)], dtype=np.uint8)

            def _popcount(x):
                b = x.view(np.uint8).reshape(-1, 8)
                return _LUT[b].sum(axis=1, dtype=np.uint16)

        # ── 1) 收集「有时间偏移的匹配」──
        hits_t = []
        hits_off = []
        for i in range(n):
            lo = int(np.searchsorted(times, times[i] - search_window, side="left"))
            hi = int(np.searchsorted(times, times[i] - self_exclude, side="right"))
            if hi <= lo:
                continue
            d = _popcount(hashes[lo:hi] ^ hashes[i])
            if not d.size:
                continue
            if bright is not None:
                # 亮度差太大的直接判为不匹配（压掉暗场误配）
                d = np.where(np.abs(bright[lo:hi] - bright[i]) <= bright_tol, d, 9999)
            k = int(np.argmin(d))
            if int(d[k]) <= enter_dist:
                hits_t.append(float(times[i]))
                hits_off.append(float(times[i]) - float(times[lo + k]))

        if len(hits_t) < min_hits:
            return []

        ht = np.asarray(hits_t, dtype=np.float64)
        ho = np.asarray(hits_off, dtype=np.float64)

        # ── 2) 偏移分桶 → 桶内用滑窗密度找密集区 ──
        buckets = np.rint(ho / bucket_sec).astype(np.int64)
        intervals = []
        for b in np.unique(buckets):
            sel = buckets == b
            if int(sel.sum()) < min_hits:
                continue
            ts = np.sort(ht[sel])
            m = ts.size

            # 滑窗密度：每个匹配点前后 win_sec 秒里有多少个同偏移匹配
            dense = []
            j0 = 0
            j1 = 0
            for i in range(m):
                while j0 < m and ts[j0] < ts[i] - win_sec:
                    j0 += 1
                if j1 < i:
                    j1 = i
                while j1 < m and ts[j1] <= ts[i] + win_sec:
                    j1 += 1
                if (j1 - j0) / (2.0 * win_sec) >= min_density:
                    dense.append(float(ts[i]))
            if len(dense) < min_hits:
                continue

            # 密集点聚成段
            clusters = []
            cur = [dense[0]]
            for t in dense[1:]:
                if t - cur[-1] <= max_gap:
                    cur.append(t)
                else:
                    clusters.append(cur)
                    cur = [t]
            clusters.append(cur)
            for c in clusters:
                intervals.extend(self._cluster_to_interval(c, min_duration, min_density))

        if not intervals:
            return []

        # ── 3) 合并重叠/相邻区间 ──
        intervals.sort()
        merged = [intervals[0]]
        for start, end in intervals[1:]:
            if start - merged[-1][1] <= 2.0:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            else:
                merged.append((start, end))
        return merged

    @staticmethod
    def _cluster_to_interval(cluster, min_duration, min_density):
        """一簇匹配点 → 一个 OP/ED 区间（不够长/不够密就不算）"""
        if not cluster:
            return []
        t0, t1 = cluster[0], cluster[-1]
        span = t1 - t0
        if span < min_duration:
            return []
        density = len(cluster) / (span + 1.0)
        if density < min_density:
            return []
        return [(t0, t1)]

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
                capture_output=True, text=True, timeout=10, creationflags=_NO_WINDOW)
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

            # ─── Phase 2b: 时长探测（逐文件求和）───
            # ffprobe 对 "-f concat" 输入的 format=duration 恒为 N/A，
            # 旧代码因此退化成"按文件大小估算"，总时长短到真实值的 15%，
            # 并行分段又会按这个错误时长把后面的集数整段丢掉。
            self._report("准备", 20, f"探测 {len(ts_effective)} 个文件的时长...")

            def _estimate_by_size():
                total_size = sum(os.path.getsize(f) for f in ts_effective)
                frames = int(total_size * 8 / (orig_w * orig_h * 0.15))
                return frames / video_fps if video_fps > 0 else len(ts_effective) * 6

            est_duration, dur_source, dur_meta = resolve_duration(
                ffprobe, ts_effective, estimate_fn=_estimate_by_size,
                progress_cb=lambda d, n: self._report(
                    "准备", 20 + 20.0 * d / max(n, 1), f"探测时长 {d}/{n}..."))
            if est_duration <= 0:
                self._report("错误", 0, "无法探测视频总时长，已中止（避免只处理一小段）")
                return
            est_total_frames = int(est_duration * video_fps)
            self._report("准备", 45,
                         f"管道就绪 | {out_w}x{out_h} | 共{est_duration/60:.1f}分"
                         f"({est_total_frames}帧) | 时长来源:{dur_source} | {num_workers}路并行")

            # ─── Phase 3: 按「文件 + 时长」均分，每组写自己的 concat ───
            # 不再用 -ss 在整条 concat 上盲 seek：mp4 的 moov/PTS 各集不一致时，
            # 输入 seek 会丢帧或跳错位置；按文件切分天然对齐，每段也从关键帧开始。
            groups = split_files_by_duration(ts_effective, dur_meta["per_file"], num_workers)
            file_dur_map = {f: d for f, d in zip(ts_effective, dur_meta["per_file"])}
            seg_specs = []
            _acc = 0.0
            for gi, (gfiles, gdur) in enumerate(groups):
                seg_concat = os.path.join(tmpdir, f"concat_p{gi}.txt")
                with open(seg_concat, "w", encoding="utf-8") as f:
                    for vf in gfiles:
                        f.write(f"file '{vf.replace(chr(92), '/')}'\n")
                # 每集在全局时间轴上的 [起点, 时长]，供「按集固定扣头尾」使用
                edges = []
                _a = _acc
                for vf in gfiles:
                    _d = file_dur_map.get(vf, 0.0) or 0.0
                    edges.append((_a, _d))
                    _a += _d
                seg_specs.append((gi, seg_concat, _acc, gdur, edges))
                _acc += gdur
            actual_workers = len(seg_specs)
            self._report("准备", 60,
                         f"分为 {actual_workers} 段: " +
                         ", ".join(f"P{g+1}[{s/60:.0f}~{(s+d)/60:.0f}分]"
                                   for g, _, s, d, _e in seg_specs))

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

            def worker(seg_id, seg_concat, seg_start, seg_dur, edges):
                """单个分段的检测线程"""
                try:
                    res, hashes = self._process_segment(
                        seg_id, seg_start, seg_dur,
                        seg_concat, out_w, out_h, video_fps,
                        frame_bytes, ffmpeg_path, tmpdir,
                        CROP_W, CROP_H, scale,
                        lambda pct, msg: seg_progress_cb(seg_id, pct, msg),
                        edges=edges
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
            for i, (gid, seg_concat, seg_start, seg_dur, edges) in enumerate(seg_specs):
                t = threading.Thread(target=worker,
                                     args=(i, seg_concat, seg_start, seg_dur, edges), daemon=True)
                threads.append(t)
                t.start()

            for t in threads:
                while t.is_alive() and not self._cancel.is_set():
                    t.join(timeout=0.2)
                if self._cancel.is_set():
                    self._report("检测", sum(seg_progress) / actual_workers, "已取消")
                    return

            # 检查错误（带 FFmpeg stderr 尾部，别再静默失败）
            for i, r in enumerate(seg_results):
                if r and "error" in r:
                    tail = (r.get("stderr") or "").strip()
                    self._report("错误", 0,
                                 f"段{i+1} 失败: {r['error']}" + (f"\nFFmpeg: {tail[-400:]}" if tail else ""))
                    return

            # ─── Phase 5: 合并结果 ───
            # 段摘要：每段实际解码了多少帧（少帧说明解码中断，不是“op 被扣掉了”）
            for i, r in enumerate(seg_results):
                if not r or "error" in r:
                    continue
                exp = int(seg_specs[i][3] * video_fps)
                msg = f"P{i+1}: {r['frame_count']}/{exp} 帧, {r['elapsed']:.0f}s"
                if exp > 0 and r['frame_count'] < exp * 0.98:
                    tail = (r.get('stderr') or '').strip().splitlines()
                    msg += f"  ⚠ 少 {exp - r['frame_count']} 帧"
                    if tail:
                        msg += f" | {tail[-1][:110]}"
                self._report("合并", 0, msg)
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
            # 总时长 = 逐文件探测之和；detected = 实际解码到的时长（用于校验是否漏段）
            detected_duration = merged['frame_count'] / video_fps if video_fps > 0 else 0.0
            actual_duration = est_duration if est_duration > 0 else detected_duration
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
            # 按集固定扣头尾（片源每集 OP/ED 不同时的兜底）
            manual_head = float(CONFIG.get("OPED_MANUAL_HEAD_SEC", 0) or 0)
            manual_tail = float(CONFIG.get("OPED_MANUAL_TAIL_SEC", 0) or 0)
            manual_time = 0.0
            if (manual_head > 0 or manual_tail > 0) and ts_effective:
                avg_ep = est_duration / max(len(ts_effective), 1)
                manual_time = min(manual_head + manual_tail, avg_ep * 0.9) * len(ts_effective)
            manual_cels = merged.get('manual_cels', 0)
            if manual_time > 0:
                self._report("OP/ED", 100,
                             f"按集固定扣除 {manual_time:.0f}s / {manual_cels} 张 "
                             f"(每集头{manual_head:.0f}s 尾{manual_tail:.0f}s)")
            shutil.rmtree(tmpdir, ignore_errors=True)

            # ─── 生成报告 ───
            elapsed = time.perf_counter() - t_start
            net_cels = max(0, merged['total_cels'] - oped_cels_deducted)
            net_time = max(0.0, actual_duration - oped_time_deducted - manual_time)
            avg_cels = net_cels / net_time if net_time > 0 else 0

            report = {
                "directory": directory,
                "segments": len(ts_effective),
                "file_type": file_type,
                "duration_sec": round(actual_duration, 1),
                "duration_min": round(actual_duration / 60, 1),
                "detected_sec": round(detected_duration, 1),
                "detected_min": round(detected_duration / 60, 1),
                "duration_source": dur_source,
                "duration_files_ok": dur_meta.get("ok", 0),
                "duration_files_failed": dur_meta.get("failed", 0),
                "incomplete_files": [_file_label(p) for p, _v, _c in dur_meta.get("incomplete", [])],
                "incomplete_detail": dur_meta.get("incomplete", []),
                "total_frames": merged['frame_count'],
                "video_fps": round(video_fps, 3),
                "total_raw": merged['total_raw'],
                "total_cels": merged['total_cels'],
                "net_cels": net_cels,
                "oped_deducted_cels": oped_cels_deducted,
                "oped_deducted_time": round(oped_time_deducted, 1),
                "manual_deducted_cels": manual_cels,
                "manual_deducted_time": round(manual_time, 1),
                "manual_head_sec": manual_head,
                "manual_tail_sec": manual_tail,
                "oped_intervals": len(oped_intervals),
                "oped_interval_list": [[round(a, 1), round(b, 1)] for a, b in oped_intervals],
                "net_duration_sec": round(net_time, 1),
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
        finally:
            self._busy.clear()

    def _process_segment(self, seg_id, seg_start, seg_duration,
                         concat_file, out_w, out_h, video_fps,
                         frame_bytes, ffmpeg_path, tmpdir,
                         CROP_W, CROP_H, scale, progress_cb, edges=None):
        """处理单个分段（该段 concat 文件从头解码到尾），返回 (result_dict, hash_list)

        seg_start: 该段在全片中的起始时间（秒），用于生成全局时间戳
        edges:     [(该集全局起点, 该集时长), ...]，用于「按集固定扣头尾」
        """
        t0 = time.perf_counter()

        # ─── 启动 FFmpeg（段内从文件开头解码，不做输入 seek）───
        pipe_cmd = [
            ffmpeg_path,
            "-f", "concat", "-safe", "0", "-i", concat_file,
            "-vf", f"crop=iw*{CROP_W}:ih*{CROP_H}:iw*0.1:ih*0.1,scale={out_w}:{out_h},format=gray",
            "-f", "rawvideo", "-pix_fmt", "gray", "-vsync", "0", "-an", "-threads", "1",
            "pipe:1"
        ]
        err_path = os.path.join(tmpdir, f"ffmpeg_p{seg_id}.log")
        err_file = open(err_path, "wb")
        proc = subprocess.Popen(pipe_cmd, stdin=subprocess.DEVNULL,
                                 stdout=subprocess.PIPE, stderr=err_file,
                                 creationflags=_NO_WINDOW)

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
                    if not _queue_put_blocking(
                            frame_queue, (idx, ts, gray, hash_256, oped_hash),
                            (reader_stop, self._cancel)):
                        break
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
        oped_sample_interval = CONFIG.get("OPED_BATCH_SAMPLE_SEC", 0.5)
        last_oped_sample_ts = -999
        last_report_time = seg_start
        seg_est_frames = int(seg_duration * video_fps)

        # ─── 按集固定扣头尾（片源每集 OP/ED 不同时用）───
        manual_head = float(CONFIG.get("OPED_MANUAL_HEAD_SEC", 0) or 0)
        manual_tail = float(CONFIG.get("OPED_MANUAL_TAIL_SEC", 0) or 0)
        manual_on = bool(edges) and (manual_head > 0 or manual_tail > 0)
        manual_edge_i = 0
        manual_cels = 0
        manual_frames = 0

        def _in_manual_oped(t):
            """这一帧是否落在某集的开头/结尾固定扣除区"""
            nonlocal manual_edge_i
            if not manual_on:
                return False
            while (manual_edge_i + 1 < len(edges)
                   and t >= edges[manual_edge_i][0] + edges[manual_edge_i][1]):
                manual_edge_i += 1
            fs, fd = edges[manual_edge_i]
            return (t < fs + manual_head) or (fd > 0 and t >= fs + fd - manual_tail)

        while not self._cancel.is_set():
            try:
                item = frame_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if item is None:
                break
            frame_idx, ts, now_frame, hash_256, oped_hash = item
            frame_count = frame_idx + 1

            # 采样哈希（顺带记录亮度：动漫大量暗场 pHash 几乎相同，
            # 不加亮度校验会把「黑底字幕 / 夜景」误判成重复片段）
            if oped_hash is not None and ts - last_oped_sample_ts >= oped_sample_interval:
                seg_hashes.append((frame_idx, ts, oped_hash, float(now_frame.mean())))
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
                    if hash_distance(hash_256, h) <= CONFIG["HASH_THRESHOLD"]:
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

            if new_cel and manual_on and _in_manual_oped(ts):
                manual_cels += 1
                manual_frames += 1
                new_cel = 0

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
        try:
            proc.wait(timeout=5)
        except Exception:
            try: proc.kill()
            except Exception: pass
        try: err_file.close()
        except Exception: pass
        stderr_tail = ""
        try:
            with open(err_path, "r", encoding="utf-8", errors="ignore") as f:
                stderr_tail = f.read()[-800:]
        except Exception:
            pass
        if gpu_ctx:
            gpu_ctx.release()

        seg_elapsed = time.perf_counter() - t0
        result = {
            'frame_count': frame_count,
            'total_cels': total_cels,
            'total_raw': total_raw,
            'manual_cels': manual_cels,
            'manual_frames': manual_frames,
            'elapsed': seg_elapsed,
            'stderr': stderr_tail,
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
        'manual_cels': 0,
        'manual_frames': 0,
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
        merged['manual_cels'] += r.get('manual_cels', 0)
        merged['manual_frames'] += r.get('manual_frames', 0)
        for k in merged['filtering']:
            merged['filtering'][k] += r['filtering'].get(k, 0)
    return merged


# ==================== 后台 / 命令行模式 ====================

def run_headless(directory, scale=0.5, num_workers=None, json_path=None,
                 log_path=None, log_cb=None):
    """无窗口跑完整检测（后台模式）。

    进度写日志/回调，报告返回 dict（也写 json_path）。
    返回 report 或 None。
    """
    done = threading.Event()
    holder = {}

    def _log(msg):
        line = f"[{time.strftime('%H:%M:%S')}] {msg}"
        print(line, flush=True)
        if log_path:
            try:
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except Exception:
                pass
        if log_cb:
            try:
                log_cb(line)
            except Exception:
                pass

    def on_progress(phase, pct, msg):
        _log(f"[{phase}] {pct:5.1f}% {msg}")

    def on_done(report):
        holder["report"] = report
        done.set()

    proc = KazumiProcessor(callback_progress=on_progress, callback_done=on_done)
    proc.process(directory, scale=scale, num_workers=num_workers)
    while not done.wait(0.5):
        if not proc.is_busy:
            break

    report = holder.get("report")
    if report:
        _log(f"完成: {report.get('net_cels')} 张 / 平均 {report.get('avg_cels_per_sec')} 张每秒")
        if json_path:
            try:
                with open(json_path, "w", encoding="utf-8") as f:
                    json.dump(report, f, ensure_ascii=False, indent=2)
                _log(f"报告已写入 {json_path}")
            except Exception as e:  # noqa: BLE001
                _log(f"写报告失败: {e}")
    else:
        _log("处理失败或被取消")
    return report


def main_cli(argv=None):
    """python kazumi_processor.py <目录> [--scale 0.5] [--workers 4] [--json out.json] [--log run.log]"""
    import argparse

    ap = argparse.ArgumentParser(
        prog="kazumi_processor",
        description="番剧缓存离线检测（无窗口/后台模式）")
    ap.add_argument("directory", help="番剧缓存目录（含 seg_*.ts 或 *.mp4）")
    ap.add_argument("--scale", type=float, default=0.5, help="检测缩放比例，默认 0.5")
    ap.add_argument("--workers", type=int, default=None,
                    help="并行分段数（1=串行，默认读配置 KAZUMI_PARALLEL_WORKERS）")
    ap.add_argument("--json", dest="json_path", default=None, help="报告输出路径（.json）")
    ap.add_argument("--log", dest="log_path", default=None, help="进度日志追加写入的文件")
    args = ap.parse_args(argv)

    if not os.path.isdir(args.directory):
        print(f"目录不存在: {args.directory}", file=sys.stderr)
        return 2

    try:
        report = run_headless(args.directory, scale=args.scale,
                              num_workers=args.workers,
                              json_path=args.json_path, log_path=args.log_path)
    except KeyboardInterrupt:
        print("\n已中断", file=sys.stderr)
        return 130
    return 0 if report else 1


if __name__ == "__main__":
    sys.exit(main_cli())
