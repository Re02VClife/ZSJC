# oped_detector.py
"""
OP/ED 实时检测器。
基于帧级哈希匹配，连续匹配/不匹配判定进入/退出 OP/ED 片段。
负责归类、扣除张数和时长统计。
"""
import time
from collections import deque
from config import CONFIG

class OPEDDetector:
    def __init__(self, counter):
        self.counter = counter                     # 主计数器实例，用于访问 total_history 等
        self.enabled = CONFIG["OPED_DETECTION_ENABLED"]
        # 历史采样
        self.history_time = deque()
        self.history_filtered = deque()
        self.history_raw = deque()
        # 哈希序列
        self.hash_seq = deque()                    # (elapsed_time, hash_int)
        self.last_sample_time = 0.0

        # 连续匹配状态（帧级实时匹配）
        self.match_streak = 0
        self.mismatch_streak = 0
        self.first_match_time = None
        self.match_active = False
        self.match_start = None
        self.pending_id = None
        self.collected_hashes = []

        # 片段模板
        self.templates = []                        # [{id, hash_sequence, color, wave_filtered, wave_raw}]
        self.color_palette = ['#FFD700', '#87CEEB', '#90EE90', '#FFB6C1', '#DDA0DD']
        self.next_color_idx = 0

        # 累计扣除数据
        self.deducted_cels = 0
        self.deducted_time = 0.0
        self.deducted_intervals = []               # [(start, end), ...]
        self.segment_occurrences = []              # (start_time, end_time, segment_id)

    def reset(self):
        """重置所有检测状态"""
        self.history_time.clear()
        self.history_filtered.clear()
        self.history_raw.clear()
        self.hash_seq.clear()
        self.last_sample_time = 0.0
        self.match_streak = 0
        self.mismatch_streak = 0
        self.first_match_time = None
        self.match_active = False
        self.match_start = None
        self.pending_id = None
        self.collected_hashes = []
        self.templates.clear()
        self.next_color_idx = 0
        self.deducted_cels = 0
        self.deducted_time = 0.0
        self.deducted_intervals.clear()
        self.segment_occurrences.clear()

    def sample(self, elapsed_time, filtered_fps, raw_fps, frame_hash):
        """每秒采样，添加历史记录并触发检测"""
        self.history_time.append(elapsed_time)
        self.history_filtered.append(filtered_fps)
        self.history_raw.append(raw_fps)
        if CONFIG.get("OPED_HASH_ENABLED", True) and frame_hash is not None:
            self.hash_seq.append((elapsed_time, frame_hash))

        max_points = int(CONFIG["OPED_HISTORY_HOURS"] * 3600 / CONFIG["OPED_SAMPLE_INTERVAL_SEC"])
        while len(self.history_time) > max_points:
            self.history_time.popleft()
            self.history_filtered.popleft()
            self.history_raw.popleft()
        while len(self.hash_seq) > max_points:
            self.hash_seq.popleft()

        self._detect_oped()

    def _detect_oped(self):
        """基于帧级哈希匹配的实时 OP/ED 检测。"""
        if not self.enabled or not CONFIG.get("OPED_HASH_ENABLED", True):
            return
        if len(self.hash_seq) < 1:
            return

        enter_dist = CONFIG.get("OPED_ENTER_HAMMING", 5)
        exit_dist = CONFIG.get("OPED_EXIT_HAMMING", 15)
        enter_consec = CONFIG.get("OPED_ENTER_CONSEC", 3)
        exit_consec = CONFIG.get("OPED_EXIT_CONSEC", 3)
        self_exclude = CONFIG.get("OPED_SELF_EXCLUDE_SEC", 90)
        min_duration = CONFIG.get("OPED_MIN_DURATION", 30)
        sample_int = CONFIG["OPED_SAMPLE_INTERVAL_SEC"]

        t_now, h_now = self.hash_seq[-1]

        matched = False
        for t_hist, h_hist in self.hash_seq:
            if t_hist >= t_now - self_exclude:
                break
            if bin(h_now ^ h_hist).count('1') <= enter_dist:
                matched = True
                break

        if matched:
            self.match_streak += 1
            self.mismatch_streak = 0
            if not self.match_active and self.match_streak == 1:
                self.first_match_time = t_now
        else:
            self.mismatch_streak += 1
            self.match_streak = 0
            if not self.match_active:
                self.first_match_time = None

        if self.match_active:
            self.collected_hashes.append(h_now)

        # 进入 OP/ED
        if not self.match_active and self.match_streak >= enter_consec:
            self.match_active = True
            self.match_start = self.first_match_time
            idx = len(self.hash_seq) - enter_consec
            self.collected_hashes = [h for _, h in list(self.hash_seq)[idx:]]
            self.first_match_time = None

        # 退出 OP/ED
        if self.match_active and self.mismatch_streak >= exit_consec:
            self.match_active = False
            last_matched_time = t_now - exit_consec * sample_int
            duration = last_matched_time - self.match_start
            if duration >= min_duration:
                pending_id = self._classify_hash_segment(self.collected_hashes)
                self.pending_id = pending_id
                self.segment_occurrences.append(
                    (self.match_start, last_matched_time, pending_id)
                )
                self._apply_deduction(self.match_start, last_matched_time)
            self.match_start = None
            self.pending_id = None
            self.collected_hashes = []
            self.match_streak = 0
            self.mismatch_streak = 0
            self.first_match_time = None

    def _classify_hash_segment(self, query_hashes):
        """根据哈希窗口相似度归类或创建模板，返回模板ID"""
        threshold = 0.75
        max_dist = CONFIG["OPED_HASH_MAX_DIST"]
        best_ratio = 0.0
        best_id = -1
        best_idx = -1

        for idx, tpl in enumerate(self.templates):
            tpl_hashes = tpl.get('hash_sequence', [])
            if len(tpl_hashes) != len(query_hashes):
                continue
            similar = 0
            for q, t in zip(query_hashes, tpl_hashes):
                if bin(q ^ t).count('1') <= max_dist:
                    similar += 1
            ratio = similar / len(query_hashes)
            if ratio > best_ratio:
                best_ratio = ratio
                best_id = tpl['id']
                best_idx = idx

        if best_ratio >= threshold:
            self.templates[best_idx]['hash_sequence'] = list(query_hashes)
            return best_id
        else:
            new_id = len(self.templates)
            color = self.color_palette[self.next_color_idx % len(self.color_palette)]
            self.next_color_idx += 1
            self.templates.append({
                'id': new_id,
                'hash_sequence': query_hashes.copy(),
                'color': color,
                'wave_filtered': [],
                'wave_raw': []
            })
            return new_id

    def _apply_deduction(self, start, end):
        """计算并扣除指定 OP/ED 区间的张数和时长"""
        # 去重
        for old_start, old_end in self.deducted_intervals:
            if abs(old_start - start) < 1.0 and abs(old_end - end) < 1.0:
                return
        self.deducted_intervals.append((start, end))

        history = list(self.counter.total_history)
        if not history:
            return

        start_cels = None
        end_cels = None
        for item in history:
            t, _, filtered_total, *_ = item
            if t <= start:
                start_cels = filtered_total
        if start_cels is None:
            return
        for item in reversed(history):
            t, _, filtered_total, *_ = item
            if t >= end:
                end_cels = filtered_total
            else:
                break
        if end_cels is None:
            end_cels = history[-1][2]

        cels_in_interval = end_cels - start_cels
        if cels_in_interval < 0:
            cels_in_interval = 0
        time_in_interval = end - start
        self.deducted_cels += cels_in_interval
        self.deducted_time += time_in_interval

    def get_deducted_cels(self):
        return self.deducted_cels

    def get_deducted_time(self):
        return self.deducted_time