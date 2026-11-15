# ====== Module: video_source.py ======
# video_source.py
"""
统一帧源抽象层：
- 实时模式：封装 dxcam
- 视频模式：封装 cv2.VideoCapture，可选 GPU 上传
提供 next_frame() 接口返回 (timestamp, gray_small_frame, raw_bgr_frame, hash_256, oped_hash)

关于「硬件加速」的说明（之前的实现名不符实）：
OpenCV VideoCapture 的 CAP_PROP_HW_ACCELERATION 只是**请求**，是否真的
走 NVDEC/D3D11 取决于构建和视频编码格式。这里在打开后用
CAP_PROP_HW_ACCELERATION 读回实际值，记录到 decode_backend，
不再无条件宣称"已硬解"。
"""

import time
import os
import cv2
import sys
import shutil
import tempfile
import numpy as np
import threading
import queue
try:
    import dxcam
    DX_AVAILABLE = True
except ImportError:
    DX_AVAILABLE = False
from detection import compute_all_hashes   # 确保 detection.py 已在路径中


# OpenCV 的硬解枚举值与字符串映射
_HW_ACCEL_NAMES = {
    0: "software",       # VIDEO_ACCELERATION_NONE
    1: "d3d11",          # VIDEO_ACCELERATION_D3D11
    2: "vaapi",          # VIDEO_ACCELERATION_VAAPI
    3: "mfx",            # VIDEO_ACCELERATION_MFX
}


def normalize_video_path(path):
    """
    兼容 B 站缓存 m4s：文件开头有 9 字节 ASCII "000000000"，
    剥掉后才是标准 fMP4，FFmpeg / OpenCV 才能打开。

    返回可直接播放的路径（原文件或 TEMP 下的修复副本）。
    修复副本按文件大小缓存，重复运行不会反复复制。
    """
    try:
        with open(path, "rb") as f:
            head = f.read(9)
        if head != b"000000000":
            return path
        size = os.path.getsize(path)
        dst = os.path.join(tempfile.gettempdir(),
                           "zsjc_fix_" + os.path.basename(path) + ".mp4")
        if (not os.path.isfile(dst)) or os.path.getsize(dst) != size - 9:
            with open(path, "rb") as src, open(dst, "wb") as out:
                src.seek(9)
                shutil.copyfileobj(src, out, 1 << 20)
        return dst
    except OSError:
        return path


class FrameSource:
    def __init__(self, mode='realtime', video_path=None, scale_factor=0.5, region=None,
                 use_gpu=False, gpu_mat_pool=None, hw_accel=True, hash_enabled=True):

        self.mode = mode
        self.scale_factor = scale_factor
        self.region = region
        self.use_gpu = use_gpu and self._check_gpu_support()
        self.cap = None
        self.frame_shape = None
        self.video_fps = 0
        self.video_total_frames = 0
        self.video_duration = 0
        self._start_time = None
        self._last_grab_time = 0
        self._gpu_upload_enabled = self.use_gpu
        self.lock = threading.Lock()
        self._stop_decode = False
        self._pause_decode = threading.Event()
        self._pause_decode.set()
        self.frame_queue = None
        self.decode_thread = None
        self._cached_time = 0.0
        self._cached_frame = 0
        self._cached_duration = self.video_duration
        self.hash_enabled = hash_enabled
        # 实际使用的解码后端（software / d3d11 / mfx ...），由打开后的读回值决定
        self.decode_backend = "n/a"

        if mode == 'realtime':
            if not DX_AVAILABLE:
                raise ImportError("实时模式需要 dxcam，请安装: pip install dxcam")
            self.camera = dxcam.create(output_color="BGR")
            self.camera.start(target_fps=0, video_mode=False)
            sample = self.camera.get_latest_frame()
            if sample is not None:
                h, w = sample.shape[:2]
                if region is None:
                    self.region = (0, 0, w, h)
                else:
                    self.region = region
                l, t, r, b = self.region
                keep_h = b - t
                keep_w = r - l
                self.frame_shape = (int(keep_h * scale_factor), int(keep_w * scale_factor))
            else:
                raise RuntimeError("dxcam 无法获取初始帧")


        elif mode == 'video':

            if video_path is None:
                raise ValueError("视频模式必须提供 video_path")

            # B 站 m4s 需要先剥掉 9 字节前缀，否则 FFmpeg/OpenCV 都打不开
            playback_path = normalize_video_path(video_path)

            # ========= 硬件加速尝试（NVDEC → D3D11 → 软解）=========
            cap = None

            if hw_accel:
                # 1) FFmpeg + NVDEC（NVIDIA GPU 硬解）
                if sys.platform == "win32":
                    try:
                        # 设置 FFmpeg 环境变量启用 CUDA 硬解
                        old_opts = os.environ.get("OPENCV_FFMPEG_CAPTURE_OPTIONS", "")
                        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "hwaccel;cuda|hwaccel_output_format;cuda"
                        cap = cv2.VideoCapture(playback_path, cv2.CAP_FFMPEG)
                        cap.set(cv2.CAP_PROP_HW_ACCELERATION, cv2.VIDEO_ACCELERATION_ANY)
                        if not cap.isOpened():
                            cap.release()
                            cap = None
                        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = old_opts
                    except Exception:
                        pass

                # 2) MSMF + D3D11（Windows 原生硬解）
                if cap is None and sys.platform == "win32":
                    try:
                        cap = cv2.VideoCapture(playback_path, cv2.CAP_MSMF)
                        cap.set(cv2.CAP_PROP_HW_ACCELERATION, cv2.VIDEO_ACCELERATION_D3D11)
                        if not cap.isOpened():
                            cap.release()
                            cap = None
                    except Exception:
                        pass

                # 3) FFmpeg 通用硬解
                if cap is None:
                    try:
                        cap = cv2.VideoCapture(playback_path, cv2.CAP_FFMPEG)
                        cap.set(cv2.CAP_PROP_HW_ACCELERATION, cv2.VIDEO_ACCELERATION_ANY)
                        if not cap.isOpened():
                            cap.release()
                            cap = None
                    except Exception:
                        pass

            # 回退：纯软件解码
            if cap is None:
                cap = cv2.VideoCapture(playback_path)

            self.cap = cap
            self._frame_counter = 0
            if not self.cap.isOpened():
                raise IOError(f"无法打开视频文件: {video_path}")
            # 读回实际的硬件加速状态：请求 != 生效
            self.decode_backend = self._detect_decode_backend()
            self.video_fps = self.cap.get(cv2.CAP_PROP_FPS)
            self.video_total_frames = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT))
            self.video_duration = self.video_total_frames / self.video_fps if self.video_fps > 0 else 0
            # 读取第一帧确定尺寸
            with self.lock:
                ret, frame = self.cap.read()
            if not ret:
                raise RuntimeError("视频读取失败")
            # 裁剪
            h, w = frame.shape[:2]
            top_crop = int(h * 0.10)
            bottom_crop = int(h * 0.15)
            left_crop = int(w * 0.10)
            right_crop = int(w * 0.10)
            frame = frame[top_crop : h - bottom_crop, left_crop : w - right_crop]
            h, w = frame.shape[:2]
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            self.frame_shape = (int(h * scale_factor), int(w * scale_factor))

            # ===== 创建帧队列并启动解码线程 =====
            self.frame_queue = queue.Queue(maxsize=120)
            self._stop_decode = False
            self.decode_thread = threading.Thread(target=self._decode_loop, daemon=True)
            self.decode_thread.start()

    @staticmethod
    def _check_gpu_support():
        try:
            _ = cv2.cuda.getCudaEnabledDeviceCount()
            return True
        except Exception:
            return False

    def _detect_decode_backend(self):
        """读回 VideoCapture 实际采用的硬件加速方式（失败则视为软解）。"""
        try:
            raw = int(self.cap.get(cv2.CAP_PROP_HW_ACCELERATION))
        except Exception:  # noqa: BLE001
            return "software"
        return _HW_ACCEL_NAMES.get(raw, f"hw:{raw}")

    def _process_frame(self, frame):
        """裁剪、缩放、灰度化，返回 (gray_small, bgr_raw)"""
        h, w = frame.shape[:2]
        top_crop = int(h * 0.10)
        bottom_crop = int(h * 0.15)
        left_crop = int(w * 0.10)
        right_crop = int(w * 0.10)
        frame = frame[top_crop: h - bottom_crop, left_crop: w - right_crop]
        bgr_raw = frame.copy()
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        small = cv2.resize(gray, (0, 0), fx=self.scale_factor, fy=self.scale_factor)
        return small, bgr_raw

    def _decode_loop(self):
        while not self._stop_decode:
            self._pause_decode.wait()
            if self._stop_decode:
                break
            with self.lock:
                ret, frame = self.cap.read()
                if not ret:
                    self.frame_queue.put(None)
                    break
                self._frame_counter += 1
                ts = self._frame_counter / self.video_fps if self.video_fps > 0 else 0.0
            small, bgr_raw = self._process_frame(frame)

            # 流水线：解码线程计算哈希（仅当 hash_enabled 时）
            hash_256 = oped_hash = None
            if self.hash_enabled:
                try:
                    hash_256, oped_hash = compute_all_hashes(small)
                except Exception:
                    pass

            self.frame_queue.put((ts, small, bgr_raw, hash_256, oped_hash))
            self._cached_time = ts
            self._cached_frame = self._frame_counter

    def next_frame(self):
        if self.mode == 'realtime':
            return self._next_realtime()  # (time, gray, bgr, None, None) 实时模式不提供哈希
        else:
            try:
                return self.frame_queue.get_nowait()  # (ts, small, bgr, hash_256, oped_hash)
            except queue.Empty:
                return None, None, None, None, None
    def _next_realtime(self):
        frame = self.camera.get_latest_frame()
        if frame is None:
            return None, None, None, None, None
        l, t, r, b = self.region
        frame = frame[t:b, l:r]
        bgr_raw = frame.copy()
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        small = cv2.resize(gray, (0, 0), fx=self.scale_factor, fy=self.scale_factor)
        # 实时模式不在此计算哈希，由主循环统一计算
        return time.time(), small, bgr_raw, None, None

    def seek(self, pos_sec):
        if self.mode != 'video' or self.cap is None:
            return
        # 暂停解码 -> 清空队列 -> seek -> 恢复解码
        self._pause_decode.clear()
        with self.lock:
            while not self.frame_queue.empty():
                try:
                    self.frame_queue.get_nowait()
                except queue.Empty:
                    break
            self.cap.set(cv2.CAP_PROP_POS_MSEC, pos_sec * 1000.0)
            self._frame_counter = int(pos_sec * self.video_fps)  # 同步内部计数器
        self._pause_decode.set()

    def pause(self):
        self._pause_decode.clear()

    def resume(self):
        self._pause_decode.set()

    def get_progress(self):
        if self.mode != 'video':
            return 0, 0, 0, 0
        # 使用解码线程定期更新的缓存值，避免频繁加锁访问 cap
        return (self._cached_time, self.video_duration,
                self._cached_frame, self.video_total_frames)

    def close(self):
        self._stop_decode = True
        # 清空队列，防止解码线程在 put 时被阻塞
        if self.frame_queue:
            while not self.frame_queue.empty():
                try:
                    self.frame_queue.get_nowait()
                except queue.Empty:
                    break
        self._pause_decode.set()
        if self.decode_thread and self.decode_thread.is_alive():
            self.decode_thread.join(timeout=1)
        if self.mode == 'realtime' and hasattr(self, 'camera'):
            try:
                self.camera.release()
            except:
                pass
        elif self.mode == 'video' and self.cap is not None:
            self.cap.release()
            self.cap = None


# ======================== FFmpeg NVDEC 管道源（极速模式） ========================

class FFmpegNvDecSource:
    """
    FFmpeg NVDEC 管道帧源。

    真实情况（纠正旧注释）：只有**解码**走 NVDEC（-hwaccel cuda），
    crop / scale / format=gray 由 FFmpeg 的 CPU 滤镜完成，Python 拿到的是
    CPU 内存里的灰度裸帧。这样能吃到硬解的速度，又不用引入 scale_cuda
    的兼容性问题。

    启动后会等第一帧：拿不到就直接抛异常，让调用方回退到
    FrameSource(OpenCV)，不会像旧实现那样静默产出 0 帧。
    """

    def __init__(self, video_path, scale_factor=0.5, target_h=None, target_w=None,
                 hash_enabled=True):
        import subprocess
        import threading
        import queue

        self.video_path = normalize_video_path(video_path)
        self.scale_factor = scale_factor
        self.hash_enabled = hash_enabled
        self._stop = False
        self._lock = threading.Lock()

        # 找到 FFmpeg 路径
        self._ffmpeg_path = self._find_ffmpeg()
        # ffprobe：同目录优先，找不到就用 PATH 里的
        _ffdir = os.path.dirname(self._ffmpeg_path)
        _ffname = os.path.basename(self._ffmpeg_path).replace("ffmpeg", "ffprobe")
        _probe_candidate = os.path.join(_ffdir, _ffname) if _ffdir else ""
        self._ffprobe_path = (_probe_candidate
                              if _probe_candidate and os.path.isfile(_probe_candidate)
                              else "ffprobe")

        # 探测视频信息：宽高/帧率必须拿到，否则整帧字节数会算错，
        # 后续 reshape 会跨帧错位（旧实现失败时默认 1920x1080，
        # 导致 1280x720 的视频每帧错位约 0.4 帧，实测丢一半以上帧）。
        try:
            info = subprocess.check_output(
                [self._ffprobe_path, "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=width,height,avg_frame_rate",
                 "-of", "csv=p=0", "-i", self.video_path],
                text=True, stderr=subprocess.DEVNULL).strip().split(",")
            self.orig_w = int(info[0])
            self.orig_h = int(info[1])
            fps_str = info[2] if len(info) > 2 else "0/0"
            if "/" in fps_str:
                num, den = fps_str.split("/")
                self.video_fps = float(num) / float(den) if float(den) else 30.0
            else:
                self.video_fps = float(fps_str or 30)
            if self.orig_w <= 0 or self.orig_h <= 0 or self.video_fps <= 0:
                raise ValueError(f"无效的视频参数: {info}")
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                f"FFprobe 无法确定视频尺寸/帧率（{video_path}）：{exc}"
            ) from exc

        # 时长/总帧数用于进度显示，拿不到不算致命
        try:
            dur = subprocess.check_output(
                [self._ffprobe_path, "-v", "error",
                 "-show_entries", "format=duration",
                 "-of", "csv=p=0", "-i", self.video_path],
                text=True, stderr=subprocess.DEVNULL).strip()
            self.video_duration = float(dur) if dur else 0.0
        except Exception:  # noqa: BLE001
            self.video_duration = 0.0
        self.video_total_frames = (int(self.video_duration * self.video_fps)
                                   if self.video_duration > 0 else 0)

        # 输出尺寸：与 FrameSource 完全一致的「裁剪比例 + 缩放」
        # 这里刻意不用 FFmpeg 的 crop/scale/format=gray 滤镜出小帧：
        # 实测滤镜链（先缩放后转灰度）与 FrameSource（先灰度后缩放）
        # 的插值顺序/舍入不同，帧数据会差 1~2 个灰阶，在差分阈值上被放大，
        # 四月是你的谎言那段实测张数从 816 掉到 616（-24%）。
        # 因此只让 FFmpeg 负责解码，整帧 bgr24 交给 Python，按与
        # FrameSource 完全相同的步骤（crop → cvtColor → resize）处理。
        self.crop_w = int(self.orig_w * 0.8)   # 左右各 10%
        self.crop_h = int(self.orig_h * 0.75)  # 上 10% + 下 15%
        if target_w and target_h:
            self.out_w, self.out_h = int(target_w), int(target_h)
        else:
            self.out_w = int(round(self.crop_w * scale_factor))
            self.out_h = int(round(self.crop_h * scale_factor))
        self.frame_shape = (self.out_h, self.out_w)
        self.frame_bytes = self.orig_w * self.orig_h * 3

        # FFmpeg 管道命令：NVDEC 硬解 → 整帧 bgr24
        self._cmd = [
            "ffmpeg",
            "-hwaccel", "cuda",
            "-i", self.video_path,
            "-f", "rawvideo",
            "-pix_fmt", "bgr24",
            "-vsync", "0",
            "-an",
            "-threads", "1",
            "pipe:1"
        ]

        self._proc = None
        # 队列只存 small（不需要原始 BGR），120 帧足够缓冲，避免几百 MB 堆积
        self.frame_queue = queue.Queue(maxsize=120)
        self._frame_counter = 0
        self._cached_time = 0.0
        self._cached_frame = 0
        self._pause = False
        self._first_frame = None

        self._start_process()
        # 启动自检：必须真的拿到第一帧，否则让调用方回退 OpenCV 帧源
        try:
            first = self.frame_queue.get(timeout=5.0)
        except queue.Empty:
            self.close()
            raise RuntimeError("FFmpeg NVDEC 管道启动超时（5s 内未产出帧）")
        if first is None:
            self.close()
            raise RuntimeError("FFmpeg NVDEC 管道未产出帧（可能不支持该编码/硬件）")
        self._first_frame = first

    @classmethod
    def _find_ffmpeg(cls):
        """自动搜索 FFmpeg 可执行文件（含 winget 安装路径），结果缓存。"""
        if cls._ffmpeg_cache:
            return cls._ffmpeg_cache
        candidates = ["ffmpeg", "ffmpeg.exe"]
        # winget 安装路径（Gyan.FFmpeg.Shared）
        try:
            import pathlib
            base = pathlib.Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/WinGet/Packages"
            for d in base.glob("Gyan.FFmpeg.Shared_*"):
                for pattern in ["ffmpeg-*/bin/ffmpeg.exe", "*/bin/ffmpeg.exe"]:
                    for exe in d.glob(pattern):
                        candidates.insert(0, str(exe))
        except Exception:  # noqa: BLE001
            pass
        # 标准路径
        for p in [r"C:\Program Files\FFmpeg\bin\ffmpeg.exe",
                  r"C:\Program Files (x86)\FFmpeg\bin\ffmpeg.exe"]:
            if os.path.isfile(p):
                candidates.insert(1, p)
        import subprocess as _sp
        for c in candidates:
            try:
                _sp.check_call([c, "-version"], stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)
                cls._ffmpeg_cache = c
                return c
            except Exception:  # noqa: BLE001
                continue
        cls._ffmpeg_cache = "ffmpeg"  # 兜底，让后续报错更明显
        return cls._ffmpeg_cache

    _ffmpeg_cache = None

    def _start_process(self):
        import subprocess
        self._cmd[0] = self._ffmpeg_path
        self._proc = subprocess.Popen(
            self._cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL
        )
        self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
        self._reader_thread.start()

    def _read_loop(self):
        """持续从 FFmpeg stdout 读取裸帧，包装为 numpy 放入队列。

        注意：readinto() 不保证一次读满整帧，管道边界处经常返回半帧。
        旧实现把 n < frame_size 直接当 EOF，导致大量丢帧
        （实测 58 秒视频只读到 776/1747 帧，219 秒视频只读到 623/6574 帧）。
        这里必须循环读满。
        """
        frame_size = self.frame_bytes
        buf = bytearray(frame_size)
        view = memoryview(buf)
        while not self._stop:
            # 暂停：不读 stdout，FFmpeg 会在管道写满后自然阻塞，不浪费 CPU
            while self._pause and not self._stop:
                time.sleep(0.02)
            got = 0
            while got < frame_size:
                n = self._proc.stdout.readinto(view[got:])
                if not n:
                    break
                got += n
            if got < frame_size:
                break  # 视频结束或解码出错
            ts = self._frame_counter / self.video_fps if self.video_fps > 0 else 0.0
            self._frame_counter += 1
            # 整帧 BGR → 与 FrameSource._process_frame 完全相同的裁剪/灰度/缩放
            full = np.frombuffer(buf, dtype=np.uint8).reshape(
                self.orig_h, self.orig_w, 3)
            top = int(self.orig_h * 0.10)
            bottom = int(self.orig_h * 0.15)
            left = int(self.orig_w * 0.10)
            right = int(self.orig_w * 0.10)
            frame = full[top:self.orig_h - bottom, left:self.orig_w - right]
            # bgr_raw 只给预览用，主流程/提取器都不用；管道源不复制整帧省内存
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            small = cv2.resize(gray, (self.out_w, self.out_h),
                               interpolation=cv2.INTER_LINEAR)
            # 计算哈希（可配置关闭）
            hash_256 = oped_hash = None
            if self.hash_enabled:
                try:
                    hash_256, oped_hash = compute_all_hashes(small)
                except Exception:  # noqa: BLE001
                    pass
            try:
                self.frame_queue.put((ts, small, None, hash_256, oped_hash), timeout=0.5)
            except Exception:  # noqa: BLE001
                break
            self._cached_time = ts
            self._cached_frame = self._frame_counter
        # 结束前自检：读到的帧数明显少于按时长估算的帧数，说明硬解不完整
        expected = self.video_total_frames
        if expected and not self._stop and self._frame_counter < expected * 0.9:
            print(f"[NVDEC] 警告：只读到 {self._frame_counter}/{expected} 帧，"
                  f"硬解可能不完整（{self.video_path}）", file=sys.stderr)
        # EOF
        try:
            self.frame_queue.put(None, timeout=1)
        except Exception:  # noqa: BLE001
            pass

    def next_frame(self):
        if self._first_frame is not None:
            item, self._first_frame = self._first_frame, None
            return item
        try:
            return self.frame_queue.get_nowait()
        except queue.Empty:
            return None, None, None, None, None

    def seek(self, pos_sec):
        """Seek 需要重建 FFmpeg 进程（管道模式无法随机访问）"""
        self._stop = True
        try:
            self._proc.kill()
            self._proc.wait(timeout=2)
        except Exception:  # noqa: BLE001
            pass
        while True:
            try:
                self.frame_queue.get_nowait()
            except queue.Empty:
                break
        self._frame_counter = int(pos_sec * self.video_fps)
        self._first_frame = None
        self._stop = False
        # 重建进程：-ss 必须作为输入选项放在 -i 之前，才是快速 seek
        cmd = self._cmd.copy()
        idx = cmd.index("-i")
        cmd[idx:idx] = ["-ss", str(pos_sec)]
        import subprocess
        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL
        )
        self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
        self._reader_thread.start()

    def pause(self):
        self._pause = True

    def resume(self):
        self._pause = False

    def get_progress(self):
        return (self._cached_time, self.video_duration,
                self._cached_frame, self.video_total_frames)

    def close(self):
        self._stop = True
        try:
            self._proc.kill()
            self._proc.wait(timeout=2)
        except Exception:
            pass
        while True:
            try:
                self.frame_queue.get_nowait()
            except Exception:
                break