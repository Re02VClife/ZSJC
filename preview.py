# ====== Module: preview.py ======
# preview.py
"""
实时预览模块：生成叠加了稠密光流、差分热力图、稀疏光流的预览图像。
"""

import cv2
import numpy as np
import time
import threading
from config import CONFIG
from detection import adaptive_threshold    # 仅需差分阈值计算

try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False


class PreviewManager:
    """管理预览状态与图像生成"""

    def __init__(self):
        self.active = False
        self.thread = None
        self.lock = threading.Lock()
        self.preview_cache = (None, None)
        self.update_interval = 0.05
        self._label = None
        self.diff_decay = None
        self.motion_history = None

        self.show_dense = False
        self.show_sparse = True
        self.show_curr = True
        self.show_diff = True

        self._get_cache_cb = None
        self._update_image_cb = None
        self._root = None

    def set_callbacks(self, get_cache_cb, update_image_cb, root):
        """设置预览所需的数据源和更新方法"""
        self._get_cache_cb = get_cache_cb
        self._update_image_cb = update_image_cb
        self._root = root

    def start(self):
        """启动预览线程"""
        if not PIL_AVAILABLE:
            import tkinter.messagebox as messagebox
            messagebox.showwarning("预览不可用", "请安装 Pillow 库: pip install Pillow")
            return
        if self.active:
            return
        self.active = True
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def stop(self):
        """停止预览线程，并等待线程结束，避免在已销毁的 root 上调度 after"""
        self.active = False
        if self.thread is not None:
            self.thread.join(timeout=1.0)
            self.thread = None

    def _loop(self):
        """预览主循环，定时获取最新帧并生成预览图"""
        while self.active:
            if self._get_cache_cb:
                last, curr = self._get_cache_cb()
                if curr is not None:
                    combined = self.make_preview_image(last, curr)
                    if self._update_image_cb and self._root:
                        # 确保 root 还存在再调度
                        try:
                            self._root.after(1, lambda img=combined: self._update_image_cb(img))
                        except Exception:
                            break  # 主窗口已销毁，退出循环
            time.sleep(self.update_interval)

    def make_preview_image(self, last, curr):
        """根据当前帧和上一帧生成叠加预览图像"""
        if self.show_curr:
            base = cv2.cvtColor(curr, cv2.COLOR_GRAY2BGR)
        else:
            base = np.zeros((curr.shape[0], curr.shape[1], 3), dtype=np.uint8)

        # ---------- 稠密光流 ----------
        if self.show_dense and last is not None and last.shape == curr.shape:
            try:
                flow = cv2.calcOpticalFlowFarneback(last, curr, None, 0.5, 3, 15, 3, 5, 1.2, 0)
                mag, ang = cv2.cartToPolar(flow[..., 0], flow[..., 1])
                hsv = np.zeros((curr.shape[0], curr.shape[1], 3), dtype=np.uint8)
                hsv[..., 0] = ang * 180 / np.pi / 2
                hsv[..., 1] = 255
                hsv[..., 2] = cv2.normalize(mag, None, 0, 255, cv2.NORM_MINMAX)
                flow_vis = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
                alpha = CONFIG["PREVIEW_DENSE_ALPHA"]
                base = cv2.addWeighted(base, 1 - alpha, flow_vis, alpha, 0)
            except Exception:
                pass

        # ---------- 差分热力图（带衰减）----------
        if self.show_diff and last is not None and last.shape == curr.shape:
            diff = cv2.absdiff(last, curr)
            thresh = adaptive_threshold(curr)
            _, diff_mask = cv2.threshold(diff, thresh, 255, cv2.THRESH_BINARY)
            diff_filtered = np.where(diff_mask > 0, diff, 0).astype(np.float32)
            if diff_filtered.max() > 0:
                diff_norm = diff_filtered / diff_filtered.max()
            else:
                diff_norm = np.zeros_like(diff_filtered)
            decay_factor = CONFIG["PREVIEW_DIFF_DECAY"]
            if self.diff_decay is None or self.diff_decay.shape != diff_norm.shape:
                self.diff_decay = np.zeros_like(diff_norm)
            combined = np.maximum(diff_norm, self.diff_decay * decay_factor)
            self.diff_decay = combined.copy()
            combined_u8 = (combined * 255).astype(np.uint8)
            heat = cv2.applyColorMap(combined_u8, cv2.COLORMAP_HOT)
            base = cv2.addWeighted(base, 0.6, heat, 0.4, 0)
        else:
            if self.diff_decay is not None:
                self.diff_decay *= 0.7

        # ---------- 稀疏光流拖影 ----------
        if self.show_sparse and last is not None and last.shape == curr.shape:
            try:
                corners = cv2.goodFeaturesToTrack(
                    last,
                    maxCorners=CONFIG["FLOW_FEATURE_COUNT"],
                    qualityLevel=CONFIG["FLOW_QUALITY_LEVEL"],
                    minDistance=CONFIG["FLOW_MIN_DISTANCE"],
                    mask=None
                )
                if corners is not None:
                    p1 = np.float32(corners).reshape(-1, 2)
                    p2, status, _ = cv2.calcOpticalFlowPyrLK(last, curr, p1, None)
                    if p2 is not None:
                        valid = status.flatten() == 1
                        h, w = base.shape[:2]
                        if self.motion_history is None or self.motion_history.shape != (h, w, 3):
                            self.motion_history = np.zeros((h, w, 3), dtype=np.float32)
                        decay = CONFIG["PREVIEW_MOTION_DECAY"]
                        self.motion_history *= decay
                        max_speed = CONFIG["PREVIEW_MOTION_MAX_SPEED"]
                        for i in range(len(p1)):
                            if not valid[i]:
                                continue
                            pt1 = tuple(p1[i].astype(int))
                            pt2 = tuple(p2[i].astype(int))
                            dx = pt2[0] - pt1[0]
                            dy = pt2[1] - pt1[1]
                            speed = np.sqrt(dx * dx + dy * dy)
                            hue = int(240 * (1 - min(speed, max_speed) / max_speed))
                            color_bgr = cv2.cvtColor(
                                np.array([[[hue, 255, 255]]], dtype=np.uint8),
                                cv2.COLOR_HSV2BGR
                            )[0, 0]
                            cv2.line(self.motion_history, pt1, pt2,
                                     color_bgr.astype(np.float32).tolist(),
                                     2, cv2.LINE_AA)
                        hist_disp = np.clip(self.motion_history, 0, 255).astype(np.uint8)
                        base = cv2.addWeighted(base, 0.7, hist_disp, 0.3, 0)
            except Exception:
                pass
        return base

    def update_image_on_label(self, combined, label):
        """将图像更新到 tkinter Label 上（需在主线程调用）"""
        if not label.winfo_exists():
            return
        rgb = cv2.cvtColor(combined, cv2.COLOR_BGR2RGB)
        pil_img = Image.fromarray(rgb)
        label_w = label.winfo_width()
        label_h = label.winfo_height()
        if label_w > 1 and label_h > 1:
            img_w, img_h = pil_img.size
            scale = min(label_w / img_w, label_h / img_h)
            new_w = int(img_w * scale)
            new_h = int(img_h * scale)
            resized = pil_img.resize((new_w, new_h), Image.Resampling.LANCZOS)
            background = Image.new('RGB', (label_w, label_h), (0, 0, 0))
            x_offset = (label_w - new_w) // 2
            y_offset = (label_h - new_h) // 2
            background.paste(resized, (x_offset, y_offset))
            tk_img = ImageTk.PhotoImage(background)
        else:
            tk_img = ImageTk.PhotoImage(pil_img)
        label.configure(image=tk_img)
        label.image = tk_img