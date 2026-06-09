# video_source.py
"""
统一帧源抽象层：
- 实时模式：封装 dxcam
- 视频模式：封装 cv2.VideoCapture，可选 GPU 上传
提供 next_frame() 接口返回 (timestamp, gray_small_frame, raw_bgr_frame)
"""
import time
import cv2
import numpy as np

try:
    import dxcam
    DX_AVAILABLE = True
except ImportError:
    DX_AVAILABLE = False


class FrameSource:
    def __init__(self, mode='realtime', video_path=None, scale_factor=0.5, region=None,
                 use_gpu=False, gpu_mat_pool=None):
        """
        mode: 'realtime' 或 'video'
        video_path: 视频文件路径（mode='video' 时必需）
        scale_factor: 截图缩放比例
        region: 实时模式下的截图区域 (left, top, right, bottom)
        use_gpu: 是否尝试使用 GPU 上传帧
        gpu_mat_pool: 可选的 GPU 内存池（预留）
        """
        self.mode = mode
        self.scale_factor = scale_factor
        self.region = region
        self.use_gpu = use_gpu and self._check_gpu_support()
        self.cap = None
        self.frame_shape = None          # 缩放后的 (h, w)
        self.video_fps = 0
        self.video_total_frames = 0
        self.video_duration = 0
        self._start_time = None
        self._last_grab_time = 0
        self._gpu_upload_enabled = self.use_gpu

        if mode == 'realtime':
            if not DX_AVAILABLE:
                raise ImportError("实时模式需要 dxcam，请安装: pip install dxcam")
            self.camera = dxcam.create(output_color="BGR")
            self.camera.start(target_fps=0, video_mode=False)
            # 获取一帧来初始化 frame_shape
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
            self.cap = cv2.VideoCapture(video_path)
            if not self.cap.isOpened():
                raise IOError(f"无法打开视频文件: {video_path}")
            self.video_fps = self.cap.get(cv2.CAP_PROP_FPS)
            self.video_total_frames = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT))
            self.video_duration = self.video_total_frames / self.video_fps if self.video_fps > 0 else 0
            # 读取第一帧确定缩放后尺寸
            ret, frame = self.cap.read()
            if not ret:
                raise RuntimeError("视频读取失败")
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)  # 复位
            h, w = frame.shape[:2]
            self.frame_shape = (int(h * scale_factor), int(w * scale_factor))
        else:
            raise ValueError("mode 必须是 'realtime' 或 'video'")

    @staticmethod
    def _check_gpu_support():
        """检查 OpenCV CUDA 是否可用"""
        try:
            _ = cv2.cuda.getCudaEnabledDeviceCount()
            return True
        except Exception:
            return False

    def next_frame(self):
        """
        返回 (timestamp, gray_small, bgr_raw) 或 (None, None, None) 表示结束/错误。
        timestamp: 视频模式下为视频时间（秒），实时模式下为真实时间戳。
        gray_small: 缩放且灰度化后的 numpy 数组（CPU内存），shape=self.frame_shape。
        bgr_raw: 原始 BGR 帧（CPU内存），仅用于预览，可能为 None。
        """
        if self.mode == 'realtime':
            return self._next_realtime()
        else:
            return self._next_video()

    def _next_realtime(self):
        frame = self.camera.get_latest_frame()
        if frame is None:
            return None, None, None
        l, t, r, b = self.region
        frame = frame[t:b, l:r]
        bgr_raw = frame.copy()
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        small = cv2.resize(gray, (0, 0), fx=self.scale_factor, fy=self.scale_factor)
        return time.time(), small, bgr_raw

    def _next_video(self):
        if self.cap is None:
            return None, None, None
        ret, frame = self.cap.read()
        if not ret:
            return None, None, None
        bgr_raw = frame.copy()
        # 获取当前视频时间（秒）
        ts = self.cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0  # 毫秒转秒
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        small = cv2.resize(gray, (0, 0), fx=self.scale_factor, fy=self.scale_factor)
        # 可选 GPU 上传（暂时只用 CPU，后续若需要可在此处将 small 转换为 GpuMat 并返回）
        return ts, small, bgr_raw

    def seek(self, pos_sec):
        """视频模式下跳转到指定时间（秒）"""
        if self.mode != 'video' or self.cap is None:
            return
        self.cap.set(cv2.CAP_PROP_POS_MSEC, pos_sec * 1000.0)

    def get_progress(self):
        """返回当前进度 (当前时间, 总时长, 当前帧号, 总帧数)"""
        if self.mode != 'video':
            return 0, 0, 0, 0
        current_msec = self.cap.get(cv2.CAP_PROP_POS_MSEC)
        current_frame = int(self.cap.get(cv2.CAP_PROP_POS_FRAMES))
        return (current_msec / 1000.0, self.video_duration,
                current_frame, self.video_total_frames)

    def close(self):
        if self.mode == 'realtime' and hasattr(self, 'camera'):
            try:
                self.camera.release()
            except:
                pass
        elif self.mode == 'video' and self.cap is not None:
            self.cap.release()
            self.cap = None