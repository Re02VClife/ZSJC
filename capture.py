<<<<<<< HEAD
import time
import numpy as np
import cv2                      # ← 必须导入，_get_screen_dxcam 里需要
import dxcam
from config import CONFIG


class CaptureMixin:
    """屏幕捕获与主处理循环"""

    def _init_camera(self):
        self.camera = dxcam.create(output_color="BGR")
        self.camera.start(target_fps=0, video_mode=False)

    def _get_screen_dxcam(self):
        try:
            frame = self.camera.get_latest_frame()
            if frame is None:
                # 没有新帧时返回上一帧
                with self.lock:
                    return self.last_frame.copy() if self.last_frame is not None else np.zeros(self.frame_shape,
                                                                                               dtype=np.uint8)
            # 裁剪区域
            l, t, r, b = self.region
            frame = frame[t:b, l:r]
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            small = cv2.resize(gray, (0, 0), fx=CONFIG["SCALE_FACTOR"], fy=CONFIG["SCALE_FACTOR"])
            with self.lock:
                if self.last_frame is not None and self.last_frame.shape != small.shape:
                    self.last_frame = cv2.resize(self.last_frame, (small.shape[1], small.shape[0]))
            return small
        except Exception:
            with self.lock:
                return self.last_frame.copy() if self.last_frame is not None else np.zeros(self.frame_shape,
                                                                                           dtype=np.uint8)

    def _capture_loop(self):
        target_fps = 48
        target_interval = 1.0 / target_fps
        next_frame_time = time.perf_counter()

        while not self._stop_event.is_set():
            self._run_event.wait()
            if self._stop_event.is_set():
                break

            # 定时等待
            now = time.perf_counter()
            if now < next_frame_time:
                sleep_time = next_frame_time - now
                if sleep_time > 0.002:
                    time.sleep(sleep_time * 0.8)
                while time.perf_counter() < next_frame_time:
                    # 短暂自旋
                    pass

            # 抓取当前帧（仅一次）
            now_frame = self._get_screen_dxcam()
            now_ts = time.time()

            # 周期管理
            if time.perf_counter() > next_frame_time + target_interval:
                next_frame_time = time.perf_counter() + target_interval
            else:
                next_frame_time += target_interval

            # 取上一帧
            with self.lock:
                last = self.last_frame.copy() if self.last_frame is not None else None

            new_cel = 0
            raw_new_cel = 0

            if last is not None:
                # 原始变化检测
                raw_detected = self._is_raw_change(last, now_frame)
                raw_new_cel = 1 if raw_detected else 0

                # 基础检测
                basic_detected = self._basic_is_new_cel(last, now_frame)
                if basic_detected:
                    self.raw_detection_timestamps.append(now_ts)

                window_sec = CONFIG["FILTER_TRIGGER_WINDOW_MS"] / 1000.0
                while self.raw_detection_timestamps and self.raw_detection_timestamps[0] < now_ts - window_sec:
                    self.raw_detection_timestamps.popleft()

                # 动态过滤触发
                if len(self.raw_detection_timestamps) >= CONFIG["FILTER_TRIGGER_COUNT"]:
                    if not self.full_filter_active:
                        self.full_filter_active = True
                    self.full_filter_active_until = now_ts + CONFIG["FULL_FILTER_HOLD_SEC"]
                elif self.full_filter_active and now_ts > self.full_filter_active_until:
                    self.full_filter_active = False

                # 最终检测
                if self.full_filter_active:
                    final_detected = self._full_is_new_cel(last, now_frame)
                else:
                    final_detected = basic_detected

                # 哈希去重
                curr_hash = None
                if self.use_hash_filter:
                    curr_hash = self._compute_hash(now_frame)

                duplicate = False
                if self.use_hash_filter and curr_hash is not None:
                    with self.lock:
                        # 缓冲区最后一个元素是刚刚存入的上一帧哈希，跳过它
                        buf = list(self.frame_buffer)  # 转为列表方便切片
                        for h in buf[:-1]:  # 不包含最后一个
                            if np.sum(curr_hash != h) <= self.hash_threshold:
                                duplicate = True
                                break
                    if not duplicate and final_detected:
                        new_cel = 1
                else:
                    new_cel = 1 if final_detected else 0

                # 过滤原因统计
                if raw_new_cel == 1 and new_cel == 0:
                    # 只有 final_detected 为真（即本身可以算作新张）却被哈希否决时才算哈希过滤
                    if duplicate and final_detected:
                        self.total_hash_filtered += 1
                    else:
                        mt = self.last_move_type
                        if mt == "全局平移":
                            self.total_translation_filtered += 1
                        elif mt == "图层分离运镜":
                            self.total_optical_flow_filtered += 1
                        elif mt == "极慢平移/静止":
                            self.total_still_filtered += 1
                        elif mt == "局部变化(过滤)":
                            self.total_local_filtered += 1
                        else:
                            self.total_other_unknown_filtered += 1

                if self.use_hash_filter:
                    with self.lock:
                        self.frame_buffer.append(curr_hash)
            else:
                # 第一帧，初始化哈希
                if self.use_hash_filter:
                    with self.lock:
                        self.frame_buffer.append(self._compute_hash(now_frame))

            # 更新总张数
            if new_cel:
                with self.lock:
                    self.total_cels_count += 1
                if not self._has_first_frame:
                    self._has_first_frame = True
            if raw_new_cel:
                with self.lock:
                    self.total_raw_cels_count += 1

            # 更新上一帧
            with self.lock:
                self.last_frame = now_frame
                if not self._has_first_frame:
                    self._has_first_frame = True

            # 更新预览缓存
            if self.preview_active:
                with self.preview_lock:
                    self.preview_cache = (last, now_frame.copy())

            # 更新实时速率
            self.recent_frames.append((now_ts, new_cel))
            self.recent_sum += new_cel
            while self.recent_frames and now_ts - self.recent_frames[0][0] > 1.0:
                old = self.recent_frames.popleft()
                self.recent_sum -= old[1]

            self.recent_frames_raw.append((now_ts, raw_new_cel))
            self.recent_sum_raw += raw_new_cel
            while self.recent_frames_raw and now_ts - self.recent_frames_raw[0][0] > 1.0:
                old = self.recent_frames_raw.popleft()
                self.recent_sum_raw -= old[1]

            span = now_ts - self.recent_frames[0][0] if self.recent_frames else 0.001
            cels = self.recent_sum / max(span, 0.001) if self.recent_frames else 0.0
            span_raw = now_ts - self.recent_frames_raw[0][0] if self.recent_frames_raw else 0.001
            cels_raw = self.recent_sum_raw / max(span_raw, 0.001) if self.recent_frames_raw else 0.0

            with self.lock:
                self.current_cels = cels
=======
import time
import numpy as np
import cv2                      # ← 必须导入，_get_screen_dxcam 里需要
import dxcam
from config import CONFIG


class CaptureMixin:
    """屏幕捕获与主处理循环"""

    def _init_camera(self):
        self.camera = dxcam.create(output_color="BGR")
        self.camera.start(target_fps=0, video_mode=False)

    def _get_screen_dxcam(self):
        try:
            frame = self.camera.get_latest_frame()
            if frame is None:
                # 没有新帧时返回上一帧
                with self.lock:
                    return self.last_frame.copy() if self.last_frame is not None else np.zeros(self.frame_shape,
                                                                                               dtype=np.uint8)
            # 裁剪区域
            l, t, r, b = self.region
            frame = frame[t:b, l:r]
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            small = cv2.resize(gray, (0, 0), fx=CONFIG["SCALE_FACTOR"], fy=CONFIG["SCALE_FACTOR"])
            with self.lock:
                if self.last_frame is not None and self.last_frame.shape != small.shape:
                    self.last_frame = cv2.resize(self.last_frame, (small.shape[1], small.shape[0]))
            return small
        except Exception:
            with self.lock:
                return self.last_frame.copy() if self.last_frame is not None else np.zeros(self.frame_shape,
                                                                                           dtype=np.uint8)

    def _capture_loop(self):
        target_fps = 48
        target_interval = 1.0 / target_fps
        next_frame_time = time.perf_counter()

        while not self._stop_event.is_set():
            self._run_event.wait()
            if self._stop_event.is_set():
                break

            # 定时等待
            now = time.perf_counter()
            if now < next_frame_time:
                sleep_time = next_frame_time - now
                if sleep_time > 0.002:
                    time.sleep(sleep_time * 0.8)
                while time.perf_counter() < next_frame_time:
                    # 短暂自旋
                    pass

            # 抓取当前帧（仅一次）
            now_frame = self._get_screen_dxcam()
            now_ts = time.time()

            # 周期管理
            if time.perf_counter() > next_frame_time + target_interval:
                next_frame_time = time.perf_counter() + target_interval
            else:
                next_frame_time += target_interval

            # 取上一帧
            with self.lock:
                last = self.last_frame.copy() if self.last_frame is not None else None

            new_cel = 0
            raw_new_cel = 0

            if last is not None:
                # 原始变化检测
                raw_detected = self._is_raw_change(last, now_frame)
                raw_new_cel = 1 if raw_detected else 0

                # 基础检测
                basic_detected = self._basic_is_new_cel(last, now_frame)
                if basic_detected:
                    self.raw_detection_timestamps.append(now_ts)

                window_sec = CONFIG["FILTER_TRIGGER_WINDOW_MS"] / 1000.0
                while self.raw_detection_timestamps and self.raw_detection_timestamps[0] < now_ts - window_sec:
                    self.raw_detection_timestamps.popleft()

                # 动态过滤触发
                if len(self.raw_detection_timestamps) >= CONFIG["FILTER_TRIGGER_COUNT"]:
                    if not self.full_filter_active:
                        self.full_filter_active = True
                    self.full_filter_active_until = now_ts + CONFIG["FULL_FILTER_HOLD_SEC"]
                elif self.full_filter_active and now_ts > self.full_filter_active_until:
                    self.full_filter_active = False

                # 最终检测
                if self.full_filter_active:
                    final_detected = self._full_is_new_cel(last, now_frame)
                else:
                    final_detected = basic_detected

                # 哈希去重
                curr_hash = None
                if self.use_hash_filter:
                    curr_hash = self._compute_hash(now_frame)

                duplicate = False
                if self.use_hash_filter and curr_hash is not None:
                    with self.lock:
                        # 缓冲区最后一个元素是刚刚存入的上一帧哈希，跳过它
                        buf = list(self.frame_buffer)  # 转为列表方便切片
                        for h in buf[:-1]:  # 不包含最后一个
                            if np.sum(curr_hash != h) <= self.hash_threshold:
                                duplicate = True
                                break
                    if not duplicate and final_detected:
                        new_cel = 1
                else:
                    new_cel = 1 if final_detected else 0

                # 过滤原因统计
                if raw_new_cel == 1 and new_cel == 0:
                    # 只有 final_detected 为真（即本身可以算作新张）却被哈希否决时才算哈希过滤
                    if duplicate and final_detected:
                        self.total_hash_filtered += 1
                    else:
                        mt = self.last_move_type
                        if mt == "全局平移":
                            self.total_translation_filtered += 1
                        elif mt == "图层分离运镜":
                            self.total_optical_flow_filtered += 1
                        elif mt == "极慢平移/静止":
                            self.total_still_filtered += 1
                        elif mt == "局部变化(过滤)":
                            self.total_local_filtered += 1
                        else:
                            self.total_other_unknown_filtered += 1

                if self.use_hash_filter:
                    with self.lock:
                        self.frame_buffer.append(curr_hash)
            else:
                # 第一帧，初始化哈希
                if self.use_hash_filter:
                    with self.lock:
                        self.frame_buffer.append(self._compute_hash(now_frame))

            # 更新总张数
            if new_cel:
                with self.lock:
                    self.total_cels_count += 1
                if not self._has_first_frame:
                    self._has_first_frame = True
            if raw_new_cel:
                with self.lock:
                    self.total_raw_cels_count += 1

            # 更新上一帧
            with self.lock:
                self.last_frame = now_frame
                if not self._has_first_frame:
                    self._has_first_frame = True

            # 更新预览缓存
            if self.preview_active:
                with self.preview_lock:
                    self.preview_cache = (last, now_frame.copy())

            # 更新实时速率
            self.recent_frames.append((now_ts, new_cel))
            self.recent_sum += new_cel
            while self.recent_frames and now_ts - self.recent_frames[0][0] > 1.0:
                old = self.recent_frames.popleft()
                self.recent_sum -= old[1]

            self.recent_frames_raw.append((now_ts, raw_new_cel))
            self.recent_sum_raw += raw_new_cel
            while self.recent_frames_raw and now_ts - self.recent_frames_raw[0][0] > 1.0:
                old = self.recent_frames_raw.popleft()
                self.recent_sum_raw -= old[1]

            span = now_ts - self.recent_frames[0][0] if self.recent_frames else 0.001
            cels = self.recent_sum / max(span, 0.001) if self.recent_frames else 0.0
            span_raw = now_ts - self.recent_frames_raw[0][0] if self.recent_frames_raw else 0.001
            cels_raw = self.recent_sum_raw / max(span_raw, 0.001) if self.recent_frames_raw else 0.0

            with self.lock:
                self.current_cels = cels
>>>>>>> 9fc274e7ae6af0036ee6d18f8b684bd055c603aa
                self.current_raw_cels = cels_raw