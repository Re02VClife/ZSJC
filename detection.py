import cv2
import numpy as np
from config import CONFIG


class DetectionMixin:
    """所有图像检测方法"""

    def _compute_hash(self, gray_img):
        resized = cv2.resize(gray_img, (16, 16), interpolation=cv2.INTER_AREA)
        avg = resized.mean()
        return (resized > avg).flatten()

    def _adaptive_threshold(self, frame):
        mean_val = np.mean(frame)
        t = CONFIG["MIN_DIFF_THRESHOLD"] + (mean_val / 255.0) * (CONFIG["MAX_DIFF_THRESHOLD"] - CONFIG["MIN_DIFF_THRESHOLD"])
        return max(CONFIG["MIN_DIFF_THRESHOLD"], min(CONFIG["MAX_DIFF_THRESHOLD"], t))

    def align_background(self, prev, curr):
        diff = cv2.absdiff(prev, curr)
        if np.mean(diff) < 2.0:
            return curr, False
        try:
            dx, dy = cv2.phaseCorrelate(np.float32(prev), np.float32(curr))[0]
            if abs(dx) < 0.5 and abs(dy) < 0.5:
                return curr, False
            h, w = curr.shape
            M = np.float32([[1, 0, -dx], [0, 1, -dy]])
            aligned = cv2.warpAffine(curr, M, (w, h), borderMode=cv2.BORDER_REPLICATE)
            return aligned, True
        except:
            return curr, False

    def _has_local_motion(self, binary_mask):
        num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(binary_mask, connectivity=8)
        if num_labels <= 1:
            return False
        areas = stats[1:, cv2.CC_STAT_AREA]
        if len(areas) == 0:
            return False
        max_area = np.max(areas)
        total_pixels = binary_mask.size
        if max_area / total_pixels < CONFIG["LOCAL_AREA_THRESH"]:
            return False
        ys, xs = np.where(binary_mask > 0)
        if len(xs) < 20:
            return False
        x_range = np.max(xs) - np.min(xs)
        y_range = np.max(ys) - np.min(ys)
        bbox_area_ratio = (x_range * y_range) / total_pixels
        if bbox_area_ratio > CONFIG["LOCAL_BBOX_RATIO_MAX"]:
            return False
        max_label = np.argmax(areas) + 1
        max_mask = (labels == max_label).astype(np.uint8)
        contours, _ = cv2.findContours(max_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if contours:
            cnt = max(contours, key=cv2.contourArea)
            x, y, w, h = cv2.boundingRect(cnt)
            aspect_ratio = max(w, h) / (min(w, h) + 1)
            if aspect_ratio > CONFIG["LOCAL_ASPECT_RATIO_MAX"]:
                return False
        return True

    def _get_subtitle_mask(self, gray_img, bottom_ratio):
        h, w = gray_img.shape
        cut_line = int(h * (1 - bottom_ratio))
        roi = gray_img[cut_line:, :]
        grad_x = cv2.Sobel(roi, cv2.CV_64F, 1, 0, ksize=3)
        grad_y = cv2.Sobel(roi, cv2.CV_64F, 0, 1, ksize=3)
        grad_mag = np.sqrt(grad_x ** 2 + grad_y ** 2).astype(np.uint8)
        _, high_contrast = cv2.threshold(grad_mag, CONFIG["SUBTITLE_GRADIENT_THRESH"], 255, cv2.THRESH_BINARY)
        kernel_close = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
        high_contrast = cv2.morphologyEx(high_contrast, cv2.MORPH_CLOSE, kernel_close)
        window_size = 21
        kernel_density = np.ones((window_size, window_size), np.float32) / (window_size ** 2)
        density_map = cv2.filter2D(high_contrast.astype(np.float32) / 255, -1, kernel_density)
        high_density = (density_map > CONFIG["SUBTITLE_DENSITY_THRESH"]).astype(np.uint8) * 255
        full_mask = np.zeros((h, w), dtype=np.uint8)
        full_mask[cut_line:, :] = high_density
        return full_mask

    def _get_bottom_subtitle_mask(self, curr_gray):
        h, w = curr_gray.shape
        subtitle_ratio = CONFIG.get("SUBTITLE_BOTTOM_RATIO", 0)
        if subtitle_ratio <= 0:
            return np.full((h, w), 255, dtype=np.uint8)

        mask = np.ones((h, w), dtype=np.uint8) * 255
        bottom_cut = int(h * (1 - subtitle_ratio))

        if CONFIG.get("SUBTITLE_CONTRAST_FILTER", True):
            sub_mask = self._get_subtitle_mask(curr_gray, subtitle_ratio)
            mask[bottom_cut:, :] = sub_mask[bottom_cut:, :]
        else:
            mask[bottom_cut:, :] = 0
        return mask

    def _is_raw_change(self, prev, curr):
        diff = cv2.absdiff(prev, curr)
        thresh = self._adaptive_threshold(curr)
        _, mask = cv2.threshold(diff, thresh, 255, cv2.THRESH_BINARY)

        if CONFIG["SUBTITLE_BOTTOM_RATIO"] > 0:
            sub_mask = self._get_bottom_subtitle_mask(curr)
            mask = cv2.bitwise_and(mask, sub_mask)

        kernel = np.ones((3, 3), np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
        ratio = np.count_nonzero(mask) / mask.size
        return ratio >= CONFIG["MIN_CHANGE_RATIO"]

    def _basic_is_new_cel(self, prev, curr):
        corr = cv2.matchTemplate(prev, curr, cv2.TM_CCOEFF_NORMED)[0][0]
        sub_mask = None
        if CONFIG["SUBTITLE_BOTTOM_RATIO"] > 0:
            sub_mask = self._get_bottom_subtitle_mask(curr)

        if corr > CONFIG["BASIC_CORR_THRESHOLD"]:
            raw_diff = cv2.absdiff(prev, curr)
            raw_thresh = self._adaptive_threshold(curr)
            _, raw_mask = cv2.threshold(raw_diff, raw_thresh, 255, cv2.THRESH_BINARY)
            if sub_mask is not None:
                raw_mask = cv2.bitwise_and(raw_mask, sub_mask)
            raw_ratio = np.count_nonzero(raw_mask) / raw_mask.size
            if raw_ratio < CONFIG["BASIC_MIN_RAW_RATIO_STILL"]:
                self.last_move_type = "极慢平移/静止"
                return False
            if raw_ratio >= CONFIG["SIGNIFICANT_CHANGE_RATIO"]:
                self.last_move_type = "新作画(基础)"
                return True
            else:
                self.last_move_type = "新作画(基础)"
                return True

        diff_thresh = self._adaptive_threshold(curr)
        raw_diff = cv2.absdiff(prev, curr)
        _, raw_mask = cv2.threshold(raw_diff, diff_thresh, 255, cv2.THRESH_BINARY)
        if sub_mask is not None:
            raw_mask = cv2.bitwise_and(raw_mask, sub_mask)
        kernel = np.ones((3, 3), np.uint8)
        raw_mask = cv2.morphologyEx(raw_mask, cv2.MORPH_CLOSE, kernel)
        raw_ratio = np.count_nonzero(raw_mask) / raw_mask.size

        if raw_ratio < CONFIG["MIN_CHANGE_RATIO"]:
            self.last_move_type = "静止"
            return False
        if raw_ratio >= CONFIG["SIGNIFICANT_CHANGE_RATIO"]:
            self.last_move_type = "新作画(基础)"
            return True

        self.last_move_type = "新作画(基础)"
        return True

    def _full_is_new_cel(self, prev, curr):
        sub_mask = None
        if CONFIG["SUBTITLE_BOTTOM_RATIO"] > 0:
            sub_mask = self._get_bottom_subtitle_mask(curr)

        corr = cv2.matchTemplate(prev, curr, cv2.TM_CCOEFF_NORMED)[0][0]
        if corr > CONFIG["FULL_CORR_THRESHOLD"]:
            raw_diff = cv2.absdiff(prev, curr)
            raw_thresh = self._adaptive_threshold(curr)
            _, raw_mask = cv2.threshold(raw_diff, raw_thresh, 255, cv2.THRESH_BINARY)
            if sub_mask is not None:
                raw_mask = cv2.bitwise_and(raw_mask, sub_mask)
            raw_ratio = np.count_nonzero(raw_mask) / raw_mask.size
            if raw_ratio < CONFIG["FULL_STILL_RATIO"]:
                self.last_move_type = "极慢平移/静止"
                return False

        diff_thresh = self._adaptive_threshold(curr)
        raw_diff = cv2.absdiff(prev, curr)
        _, raw_mask = cv2.threshold(raw_diff, diff_thresh, 255, cv2.THRESH_BINARY)
        if sub_mask is not None:
            raw_mask = cv2.bitwise_and(raw_mask, sub_mask)
        raw_ratio = np.count_nonzero(raw_mask) / raw_mask.size

        curr_aligned, has_shift = self.align_background(prev, curr)
        diff = cv2.absdiff(prev, curr_aligned)
        _, mask = cv2.threshold(diff, diff_thresh, 255, cv2.THRESH_BINARY)
        if sub_mask is not None:
            mask = cv2.bitwise_and(mask, sub_mask)
        kernel = np.ones((3, 3), np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
        change_ratio = np.count_nonzero(mask) / mask.size

        if raw_ratio >= CONFIG["MIN_CHANGE_RATIO"] and change_ratio < CONFIG["ALIGNED_CHANGE_THRESHOLD"] and has_shift:
            self.last_move_type = "全局平移"
            return False

        if change_ratio >= CONFIG["ALIGNED_CHANGE_THRESHOLD"]:
            if self._has_local_motion(mask):
                self.last_move_type = "新作画"
                return True

            feature_mask = mask.copy()
            if sub_mask is not None:
                feature_mask = cv2.bitwise_and(feature_mask, sub_mask)
            if self.use_optical_flow and self._is_layer_camera_move_v2(prev, curr_aligned, feature_mask):
                self.last_move_type = "图层分离运镜"
                return False

            if change_ratio >= CONFIG["SIGNIFICANT_CHANGE_RATIO"]:
                self.last_move_type = "新作画"
                return True

        if change_ratio < CONFIG["SIGNIFICANT_CHANGE_RATIO"]:
            mask_inv = cv2.bitwise_not(mask)
            score = cv2.matchTemplate(prev, curr_aligned, cv2.TM_CCOEFF_NORMED, mask=mask_inv)[0][0]
            if score >= CONFIG["SSIM_THRESHOLD"]:
                self.last_move_type = "局部变化(过滤)"
                return False

        self.last_move_type = "新作画"
        return True

    def _is_layer_camera_move_v2(self, prev, curr_aligned, mask):
        corners = cv2.goodFeaturesToTrack(
            prev,
            maxCorners=CONFIG["FLOW_FEATURE_COUNT"],
            qualityLevel=CONFIG["FLOW_QUALITY_LEVEL"],
            minDistance=CONFIG["FLOW_MIN_DISTANCE"],
            mask=mask
        )
        if corners is None:
            return False
        p1 = np.float32(corners).reshape(-1, 2)
        p2, status, _ = cv2.calcOpticalFlowPyrLK(prev, curr_aligned, p1, None)
        if p2 is None:
            return False
        valid = status.flatten() == 1
        if np.sum(valid) < CONFIG["LAYER_MIN_VALID_POINTS"]:
            return False
        vecs = p2[valid] - p1[valid]
        norms = np.linalg.norm(vecs, axis=1)
        moving = norms > CONFIG["FLOW_LAYER_STATIC_THRESH"]
        n_moving = np.sum(moving)
        if n_moving < CONFIG["LAYER_MIN_MOVING_POINTS"]:
            return False
        moving_vecs = vecs[moving]
        mean_vec = np.mean(moving_vecs, axis=0)
        if np.linalg.norm(mean_vec) < CONFIG["LAYER_MEAN_VEC_MIN"]:
            return False
        cos_sim = np.dot(moving_vecs, mean_vec) / (
                    np.linalg.norm(moving_vecs, axis=1) * np.linalg.norm(mean_vec) + 1e-8)
        consistency = np.mean(cos_sim > CONFIG["LAYER_COS_SIM_THRESH"])
        if consistency > CONFIG["LAYER_DIRECTION_CONSISTENCY"]:
            return True
        else:
            return False