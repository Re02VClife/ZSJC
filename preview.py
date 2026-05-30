import time
import threading
import numpy as np
import cv2
import tkinter as tk
from PIL import Image, ImageTk

from config import CONFIG, PIL_AVAILABLE


class PreviewMixin:
    """实时预览功能"""

    def _on_preview_toggle(self):
        if self.preview_toggle_var.get():
            self._start_preview()
        else:
            self._stop_preview()

    def _update_preview_interval(self):
        try:
            val = float(self.preview_interval_var.get())
            if val <= 0:
                raise ValueError
            self.preview_update_interval = val
        except ValueError:
            pass

    def _start_preview(self):
        if not PIL_AVAILABLE:
            import tkinter.messagebox as messagebox
            messagebox.showwarning("预览不可用", "请安装 Pillow 库: pip install Pillow")
            self.preview_toggle_var.set(False)
            return
        if self.preview_active:
            return
        self.preview_active = True
        self.preview_thread = threading.Thread(target=self._preview_loop, daemon=True)
        self.preview_thread.start()

    def _stop_preview(self):
        self.preview_active = False
        if self.preview_thread is not None:
            self.preview_thread.join(timeout=0.5)
            self.preview_thread = None

    def _preview_loop(self):
        while self.preview_active:
            with self.preview_lock:
                last, curr = self.preview_cache
            if curr is not None:
                combined = self._make_preview_image(last, curr)
                with self._preview_control_lock:
                    self._latest_preview_img = combined
                    if not self._preview_pending:
                        self._preview_pending = True
                        if self._preview_after_id is not None:
                            try:
                                self.root.after_cancel(self._preview_after_id)
                            except Exception:
                                pass
                        self._preview_after_id = self.root.after(1, self._do_preview_update)
            time.sleep(self.preview_update_interval)

    def _do_preview_update(self):
        with self._preview_control_lock:
            img = self._latest_preview_img
            self._preview_pending = False
            self._preview_after_id = None
        if img is not None and self._preview_label and self._preview_label.winfo_exists():
            self._update_preview_image(img)

    def _make_preview_image(self, last, curr):
        show_dense = getattr(self, 'show_dense_flow_var', tk.BooleanVar(value=True)).get()
        show_sparse = getattr(self, 'show_sparse_flow_var', tk.BooleanVar(value=True)).get()
        show_curr = getattr(self, 'show_curr_var', tk.BooleanVar(value=True)).get()
        show_diff = getattr(self, 'show_diff_var', tk.BooleanVar(value=True)).get()

        if show_curr:
            base = cv2.cvtColor(curr, cv2.COLOR_GRAY2BGR)
        else:
            base = np.zeros((curr.shape[0], curr.shape[1], 3), dtype=np.uint8)

        if show_dense and last is not None and last.shape == curr.shape:
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

        if show_diff and last is not None and last.shape == curr.shape:
            diff = cv2.absdiff(last, curr)
            thresh = self._adaptive_threshold(curr)
            _, diff_mask = cv2.threshold(diff, thresh, 255, cv2.THRESH_BINARY)

            if CONFIG["SUBTITLE_BOTTOM_RATIO"] > 0:
                sub_mask = self._get_bottom_subtitle_mask(curr)
                diff_mask = cv2.bitwise_and(diff_mask, sub_mask)

            diff_filtered = np.where(diff_mask > 0, diff, 0).astype(np.float32)

            if diff_filtered.max() > 0:
                diff_norm = diff_filtered / diff_filtered.max()
            else:
                diff_norm = np.zeros_like(diff_filtered)

            decay_factor = CONFIG["PREVIEW_DIFF_DECAY"]
            if self._diff_decay is None or self._diff_decay.shape != diff_norm.shape:
                self._diff_decay = np.zeros_like(diff_norm)
            combined = np.maximum(diff_norm, self._diff_decay * decay_factor)
            self._diff_decay = combined.copy()

            combined_u8 = (combined * 255).astype(np.uint8)
            heat = cv2.applyColorMap(combined_u8, cv2.COLORMAP_HOT)
            base = cv2.addWeighted(base, 0.6, heat, 0.4, 0)
        else:
            if self._diff_decay is not None:
                self._diff_decay *= 0.7

        if show_sparse and last is not None and last.shape == curr.shape:
            try:
                feature_mask = None
                subtitle_ratio = CONFIG.get("SUBTITLE_BOTTOM_RATIO", 0)
                if subtitle_ratio > 0 and CONFIG.get("SUBTITLE_CONTRAST_FILTER", True):
                    sub_mask = self._get_subtitle_mask(last, subtitle_ratio)
                    full_allowed = np.ones(last.shape, dtype=np.uint8) * 255
                    feature_mask = cv2.bitwise_and(full_allowed, cv2.bitwise_not(sub_mask))
                elif subtitle_ratio > 0:
                    feature_mask = np.ones(last.shape, dtype=np.uint8) * 255
                    h, w = feature_mask.shape
                    cut_line = int(h * (1 - subtitle_ratio))
                    feature_mask[cut_line:, :] = 0

                corners = cv2.goodFeaturesToTrack(
                    last,
                    maxCorners=CONFIG["FLOW_FEATURE_COUNT"],
                    qualityLevel=CONFIG["FLOW_QUALITY_LEVEL"],
                    minDistance=CONFIG["FLOW_MIN_DISTANCE"],
                    mask=feature_mask
                )
                if corners is not None:
                    p1 = np.float32(corners).reshape(-1, 2)
                    p2, status, _ = cv2.calcOpticalFlowPyrLK(last, curr, p1, None)
                    if p2 is not None:
                        valid = status.flatten() == 1
                        h, w = base.shape[:2]
                        if self._motion_history is None or self._motion_history.shape != (h, w, 3):
                            self._motion_history = np.zeros((h, w, 3), dtype=np.float32)

                        decay = CONFIG["PREVIEW_MOTION_DECAY"]
                        self._motion_history *= decay
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
                            color_bgr = cv2.cvtColor(np.array([[[hue, 255, 255]]], dtype=np.uint8), cv2.COLOR_HSV2BGR)[0, 0]
                            cv2.line(self._motion_history, pt1, pt2, color_bgr.astype(np.float32).tolist(), 2, cv2.LINE_AA)

                        hist_disp = np.clip(self._motion_history, 0, 255).astype(np.uint8)
                        base = cv2.addWeighted(base, 0.7, hist_disp, 0.3, 0)
            except Exception:
                pass

        return base

    def _update_preview_image(self, combined):
        if self._preview_label is None or not self._preview_label.winfo_exists():
            return
        rgb = cv2.cvtColor(combined, cv2.COLOR_BGR2RGB)
        pil_img = Image.fromarray(rgb)
        label_w = self._preview_label.winfo_width()
        label_h = self._preview_label.winfo_height()
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
        self._preview_label.configure(image=tk_img)
        self._preview_label.image = tk_img