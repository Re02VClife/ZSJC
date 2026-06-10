# ====== Module: video_source.py ======
# video_source.py
"""
统一帧源抽象层：
- 实时模式：封装 dxcam
- 视频模式：封装 cv2.VideoCapture，可选 GPU 上传
提供 next_frame() 接口返回 (timestamp, gray_small_frame, raw_bgr_frame, hash_256, oped_hash)
"""

import time
import cv2
import sys
import numpy as np
import threading
import queue
try:
    import dxcam
    DX_AVAILABLE = True
except ImportError:
    DX_AVAILABLE = False
from detection import compute_all_hashes   # 确保 detection.py 已在路径中

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

            # ========= 硬件加速尝试（优化版）=========

            cap = None

            if hw_accel and sys.platform == "win32":
                try:

                    cap = cv2.VideoCapture(video_path, cv2.CAP_MSMF)

                    cap.set(cv2.CAP_PROP_HW_ACCELERATION, cv2.VIDEO_ACCELERATION_D3D11)

                    if not cap.isOpened():
                        cap.release()
                        cap = None
                except:
                    pass
            if cap is None and hw_accel:
                try:
                    cap = cv2.VideoCapture(video_path, cv2.CAP_FFMPEG)

                    cap.set(cv2.CAP_PROP_HW_ACCELERATION, cv2.VIDEO_ACCELERATION_ANY)

                    if not cap.isOpened():
                        cap.release()

                        cap = None

                except:

                    pass

            if cap is None:
                # 回退：纯软件解码

                cap = cv2.VideoCapture(video_path)

            self.cap = cap
            self._frame_counter = 0
            if not self.cap.isOpened():
                raise IOError(f"无法打开视频文件: {video_path}")
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