# main.py
import copy
import json
import os
import sys
import threading
import time
import tkinter as tk
from collections import deque

import cv2
import numpy as np

from config import CONFIG, CONFIG_DEFAULT, color_manager
from detection import (
    compute_hash, is_raw_change, basic_is_new_cel, full_is_new_cel, compute_oped_hash
)
from preview import PreviewManager
from ui import build_main_ui, create_settings_window, create_crop_window

# dxcam 检查
try:
    import dxcam
    DX_AVAILABLE = True
except ImportError:
    DX_AVAILABLE = False

if not DX_AVAILABLE:
    raise ImportError("请安装 dxcam 库: pip install dxcam")

class AnimeCelCounter:
    """动画作画张数计数器主类，负责屏幕捕获、变化检测、OP/ED识别与统计"""

    def __init__(self):
        # ---------- 基础路径与拖拽数据 ----------
        if getattr(sys, 'frozen', False):
            base_dir = sys._MEIPASS
        else:
            base_dir = os.path.dirname(os.path.abspath(__file__))
        self.readme_path = os.path.join(base_dir, "README.txt")
        self._drag_data = {'start_x': 0, 'start_y': 0, 'dragging': False}
        self._settings_win = None
        self.lock = threading.Lock()
        self._stop_event = threading.Event()
        self._run_event = threading.Event()
        self._run_event.set()
        self.time_lock = threading.Lock()  # 新增
        self.skip_until_elapsed = 0.0  # 新增，替代原 skip_until_abs_time
        # ---------- 实时统计相关 ----------
        self.recent_frames = deque()
        self.recent_sum = 0
        self.current_cels = 0.0
        self.recent_frames_raw = deque()
        self.recent_sum_raw = 0
        self.current_raw_cels = 0.0
        self.total_raw_cels_count = 0

        self.total_sum = 0.0
        self.total_count = 0
        self.last_frame = None
        self.is_running = True
        self.last_refresh = time.time()
        self.idle_start_time = None

        self.total_cels_count = 0
        self.elapsed_time = 0.0
        self.last_loop_time = time.time()
        self._has_first_frame = False

        self.preview_cache = (None, None)
        self._prev_cels_count = -1
        self._last_disp_cels = -1
        self.last_move_type = "静止"

        self.total_history = deque(maxlen=6000)
        self._last_recorded_time = -1

        # ---------- 动态过滤状态 ----------
        self.full_filter_active = False
        self.full_filter_active_until = 0
        self.raw_detection_timestamps = deque()

        # 各种过滤类型的计数
        self.total_translation_filtered = 0
        self.total_optical_flow_filtered = 0
        self.total_hash_filtered = 0
        self.total_still_filtered = 0
        self.total_local_filtered = 0
        self.total_other_unknown_filtered = 0
        self.total_zoom_filtered = 0
        self.color_vars = {}
        self.load_settings()
        self.instant_fps_history = deque(maxlen=3600)  # 保存最近1小时的每秒张数
        # ---------- OP/ED 检测 ----------
        self.oped_enabled = CONFIG["OPED_DETECTION_ENABLED"]
        self.oped_history_time = deque()
        self.oped_history_filtered = deque()  # 每秒过滤后张数
        self.oped_history_raw = deque()       # 每秒过滤前张数
        # ---------- OP/ED 哈希匹配 ----------
        self.oped_hash_seq = deque()       # 存储 (elapsed_time, hash_int) ，最大长度与历史一致
        self.oped_last_sample_time = 0.0

        # 连续匹配状态
        self.consecutive_match_start = None

        # 连续匹配判定
        self.oped_match_count = 0  # 连续匹配次数
        self.oped_mismatch_count = 0  # 连续未匹配次数
        self.oped_match_active = False  # 当前是否处于匹配状态
        self.oped_match_start = None  # 当前匹配区间起点（elapsed_time）
        self.oped_pending_id = None  # 当前匹配区间临时分配的类别ID

        # 片段特征模板
        self.oped_templates = []  # 每条模板：[wave, color, id]
        self.oped_color_palette = ['#FFD700', '#87CEEB', '#90EE90', '#FFB6C1', '#DDA0DD']  # 淡金、淡蓝、淡绿、淡粉、淡紫
        self.oped_next_color_idx = 0

        # 匹配进入/退出阈值
        self.OPED_ENTER_COUNT = 3  # 连续匹配a次确认为OP/ED区间开始  设置1直接退出
        self.OPED_EXIT_COUNT = 3  # 同上，结束区间
        # OP/ED 累计扣除数据
        self.oped_deducted_cels = 0
        self.oped_deducted_time = 0.0
        self.deducted_intervals = []  # 已扣除的区间 [(start, end), ...]
        # 已知 OP/ED 片段模板
        self.known_segments = []              # 每个元素为 dict
        self.segment_occurrences = []         # (start_time, end_time, segment_id)
        self.current_skip_until = 0.0         # 暂停统计直到该时间戳（已废弃，保留兼容）
        self._pending_oped_start = None       # 匹配成功时记录的片段开始时间

        # OP/ED 累计统计（不重复计数）
        self.oped_unique_cels = 0             # 不重复 OP/ED 总张数
        self.oped_unique_time = 0.0           # 不重复 OP/ED 总时长
        self.oped_unique_count = 0            # 不重复片段个数
        self.oped_active_segment = None       # 当前正在跳过的 OP/ED 片段引用
        self._refine_lock = threading.Lock()
        self._refining = False
        self.start_real_time = time.time()
        self.start_elapsed_time = 0.0  # 实际上 elapsed_time 从 0 开始
        # ---------- 哈希缓冲区 ----------
        self.frame_buffer = deque(maxlen=CONFIG["FRAME_BUFFER_SIZE"])
        self.hash_threshold = CONFIG["HASH_THRESHOLD"]

        # ---------- 波形数据 ----------
        max_wave_points = int(CONFIG["WAVE_HISTORY_SEC"] * (1000 / CONFIG["WAVE_REFRESH_MS"]))
        self.wave_data = deque(maxlen=max_wave_points)
        self.wave_raw_data = deque(maxlen=max_wave_points)

        max_wave2_points = int(CONFIG["WAVE2_HISTORY_SEC"] * (1000 / CONFIG["WAVE2_REFRESH_MS"]))
        self.wave2_data = deque(maxlen=max_wave2_points)
        self.wave2_raw_data = deque(maxlen=max_wave2_points)

        self.smooth_cels = 0.0
        self.smooth_raw_cels = 0.0

        # ---------- 屏幕捕获 ----------
        self.camera = dxcam.create(output_color="BGR")
        self.camera.start(target_fps=0, video_mode=False)

        monitor_sample = self.camera.get_latest_frame()
        if monitor_sample is not None:
            h, w = monitor_sample.shape[:2]
            if CONFIG.get("CROP_REGION"):
                base_rect = CONFIG["CROP_REGION"]
            else:
                base_rect = (0, 0, w, h)
            self.region = self._compute_region_from_base(base_rect, CONFIG["CROP_RATIO"], w, h)
            keep_h = self.region[3] - self.region[1]
            keep_w = self.region[2] - self.region[0]
            self.frame_shape = (int(keep_h * CONFIG["SCALE_FACTOR"]),
                                int(keep_w * CONFIG["SCALE_FACTOR"]))
        else:
            self.region = (384, 216, 1536, 864)
            self.frame_shape = (100, 100)

        # ---------- 预览管理器 ----------
        self.preview_manager = PreviewManager()

        # ---------- 创建主窗口 ----------
        self.root = tk.Tk()
        self.root.title("动画作画张数")
        self.root.geometry("1200x420")
        self.root.attributes("-topmost", True)
        TRANSPARENT = "#000000"
        self.root.attributes("-transparentcolor", TRANSPARENT)
        self.root.configure(bg=TRANSPARENT)
        self.root.overrideredirect(True)

        # 构建主界面
        ui_elems = build_main_ui(self.root, self)
        self.lb_rt = ui_elems['lb_rt']
        self.lb_total_time = ui_elems['lb_total_time']
        self.lb_total_cels = ui_elems['lb_total_cels']
        self.lb_avg = ui_elems['lb_avg']
        self.lb_st = ui_elems['lb_st']
        self.canvas = ui_elems['canvas1']
        self.canvas2 = ui_elems['canvas2']
        self.btn_canvas = ui_elems['btn_canvas']

        self.wave_margin_left = 30
        self.wave_margin_right = 10
        self.wave_margin_top = 10
        self.wave_margin_bottom = 10

        self.make_draggable()
        self._draw_buttons()

        # 启动捕获线程和UI循环
        self.capture_thread = threading.Thread(target=self._capture_loop, daemon=True)
        self.capture_thread.start()

        self.root.after(30, self.loop)
        self.root.after(CONFIG["WAVE_REFRESH_MS"], self.update_wave)
        self.root.after(CONFIG["WAVE2_REFRESH_MS"], self.update_wave2)
        self.root.mainloop()

    # ---------- 辅助方法 ----------
    def _compute_region_from_base(self, base_rect, ratio, screen_w, screen_h):
        """根据基准矩形和缩放比例计算实际截图区域"""
        left, top, right, bottom = base_rect
        cx = (left + right) / 2
        cy = (top + bottom) / 2
        orig_w = right - left
        orig_h = bottom - top
        new_w = orig_w * ratio
        new_h = orig_h * ratio
        new_left = cx - new_w / 2
        new_top = cy - new_h / 2
        new_right = cx + new_w / 2
        new_bottom = cy + new_h / 2
        new_left = max(0, new_left)
        new_top = max(0, new_top)
        new_right = min(screen_w, new_right)
        new_bottom = min(screen_h, new_bottom)
        return (int(new_left), int(new_top), int(new_right), int(new_bottom))

    def _get_settings_path(self):
        from config import get_settings_path
        return get_settings_path()

    def load_settings(self):
        """从 JSON 加载设置"""
        try:
            settings_path = self._get_settings_path()
            with open(settings_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            CONFIG.update(data.get("config", {}))
            self.use_optical_flow = data.get("use_optical_flow", True)
            self.use_hash_filter = data.get("use_hash_filter", True)
            self.hash_threshold = CONFIG["HASH_THRESHOLD"]
            self.frame_buffer = deque(maxlen=CONFIG["FRAME_BUFFER_SIZE"])
            self.layouts = data.get("layouts", {})
            self.active_layout = data.get("active_layout", None)
            if "USE_CUSTOM_COLORS" in data:
                CONFIG["USE_CUSTOM_COLORS"] = data["USE_CUSTOM_COLORS"]
            color_manager._config = CONFIG["COLORS"]
        except (FileNotFoundError, json.JSONDecodeError):
            self.use_optical_flow = True
            self.use_hash_filter = True
            self.layouts = {}
            self.active_layout = None

    def _save_all_settings(self):
        """保存所有设置到 JSON 文件"""
        settings = {
            "config": CONFIG,
            "use_optical_flow": self.use_optical_flow,
            "use_hash_filter": self.use_hash_filter,
            "layouts": self.layouts,
            "active_layout": self.active_layout
        }
        path = self._get_settings_path()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2, ensure_ascii=False)

    def make_draggable(self):
        """使主窗口可拖动（无边框窗口）"""
        def start(e):
            self._drag_data['start_x'] = e.x_root
            self._drag_data['start_y'] = e.y_root
            self._drag_data['dragging'] = False

        def move(e):
            if not hasattr(self, '_drag_data'):
                return
            dx = e.x_root - self._drag_data['start_x']
            dy = e.y_root - self._drag_data['start_y']
            if abs(dx) > 5 or abs(dy) > 5:
                self._drag_data['dragging'] = True
                self.root.geometry(f"+{self.root.winfo_x() + dx}+{self.root.winfo_y() + dy}")
                self._drag_data['start_x'] = e.x_root
                self._drag_data['start_y'] = e.y_root

        def release(e):
            self._drag_data['dragging'] = False

        self.root.bind("<Button-1>", start)
        self.root.bind("<B1-Motion>", move)
        self.root.bind("<ButtonRelease-1>", release)

    def _draw_buttons(self):
        """绘制主界面的功能按钮（设置/暂停/重置/关闭）"""
        canvas = self.btn_canvas
        canvas.delete("all")
        btn_w = 100
        btn_h = 22
        gap = 4
        x0 = 5
        x1 = x0 + btn_w
        pause_text = "暂停/继续" if self.is_running else "继续"
        buttons_info = [
            ("设置", self.open_settings),
            (pause_text, self.pause),
            ("重置统计", self.reset_stats),
            ("关闭", self._on_close)
        ]
        accent = color_manager.get_color('accent')
        for i, (text, cmd) in enumerate(buttons_info):
            y0 = 5 + i * (btn_h + gap)
            y1 = y0 + btn_h
            rect_id = canvas.create_rectangle(x0, y0, x1, y1,
                                              outline="", fill="",
                                              activeoutline="", width=0)
            text_id = canvas.create_text(x0 + btn_w // 2, y0 + btn_h // 2,
                                         text=text, fill=accent,
                                         font=("Arial", 9, "bold"),
                                         activefill="#FFD700")

            def safe_cmd(cmd):
                if not self._drag_data['dragging']:
                    cmd()

            canvas.tag_bind(rect_id, "<ButtonRelease-1>", lambda e, c=cmd: safe_cmd(c))
            canvas.tag_bind(text_id, "<ButtonRelease-1>", lambda e, c=cmd: safe_cmd(c))
            canvas.tag_bind(rect_id, "<Enter>",
                            lambda e, r=rect_id: canvas.itemconfig(r, fill="#111111"))
            canvas.tag_bind(rect_id, "<Leave>",
                            lambda e, r=rect_id: canvas.itemconfig(r, fill=""))
            canvas.tag_bind(text_id, "<Enter>",
                            lambda e, r=rect_id: canvas.itemconfig(r, fill="#A12F2F"))
            canvas.tag_bind(text_id, "<Leave>",
                            lambda e, r=rect_id: canvas.itemconfig(r, fill=""))

    def open_settings(self):
        """打开设置窗口"""
        if self._settings_win is not None and self._settings_win.winfo_exists():
            self._settings_win.lift()
            return
        win = create_settings_window(self.root, self)
        self._settings_win = win

    def open_readme(self):
        """打开 README 说明文件"""
        import subprocess
        path = self.readme_path
        if not os.path.exists(path):
            import tkinter.messagebox as messagebox
            messagebox.showinfo("说明文件", "未找到 README.txt，请确保文件存在。")
            return
        try:
            if sys.platform == "win32":
                os.startfile(path)
            elif sys.platform == "darwin":
                subprocess.run(["open", path], check=True)
            else:
                subprocess.run(["xdg-open", path], check=True)
        except Exception as e:
            import tkinter.messagebox as messagebox
            messagebox.showinfo("说明文件", f"请手动打开文件：\n{path}")

    def show_crop_region(self):
        """显示截图范围调整窗口"""
        create_crop_window(self)

    def _apply_layout(self, win, panels):
        """应用已保存的窗口布局"""
        def set_panels(attempt=0):
            if win.winfo_width() < 100:
                if attempt < 5:
                    win.after(200, lambda: set_panels(attempt + 1))
                return
            layout_to_apply = None
            if self.active_layout and self.active_layout in self.layouts:
                layout_to_apply = self.layouts[self.active_layout]
            else:
                try:
                    with open(self._get_settings_path(), "r") as f:
                        data = json.load(f)
                    layout_to_apply = data.get('layout', None)
                except:
                    pass
            if layout_to_apply:
                if 'window_geometry' in layout_to_apply:
                    try:
                        win.geometry(layout_to_apply['window_geometry'])
                    except:
                        pass
                for name, geom in layout_to_apply.get('panels', {}).items():
                    if name in panels:
                        panels[name].apply_geometry(geom)
        win.after(200, set_panels)

    # ---------- 设置窗口内容构建 ----------
    def _build_params_content(self, parent, win, panels):
        """
        构建设置窗口中的参数内容（可滚动区域）。
        返回包含所有配置项输入变量的字典。
        """
        canvas = tk.Canvas(parent, borderwidth=0, highlightthickness=0,
                           bg=color_manager.get_color('bg'))
        scrollbar = tk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        scroll_frame = tk.Frame(canvas, bg=color_manager.get_color('bg'))
        scroll_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=scroll_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.settings_canvas = canvas

        accent = color_manager.get_color('accent')
        bg = color_manager.get_color('bg')
        btn_bg = color_manager.get_color('btn_bg')

        # 参数中文显示名
        param_names = {
            "REFRESH_INTERVAL": "UI刷新间隔",
            "ALPHA": "背景透明度",
            "SCALE_FACTOR": "截图缩放比例",
            "SSIM_THRESHOLD": "背景相似度阈值",
            "WAVE_HISTORY_SEC": "波形1时长(s)",
            "WAVE_REFRESH_MS": "波形1刷新(ms)",
            "WAVE2_HISTORY_SEC": "波形2时长(s)",
            "WAVE2_REFRESH_MS": "波形2刷新间隔(ms)",
            "JingzhiShiJian": "静止自动暂停(s)",
            "WAVE_MAX_Y": "波形Y轴最大值",
            "CROP_RATIO": "截图区域比例",
            "MIN_DIFF_THRESHOLD": "最小变化阈值",
            "MAX_DIFF_THRESHOLD": "最大变化阈值",
            "MIN_CHANGE_RATIO": "最小变化占比",
            "ALIGNED_CHANGE_THRESHOLD": "平移对齐残差",
            "SIGNIFICANT_CHANGE_RATIO": "显著变化占比",
            "FLOW_FEATURE_COUNT": "光流特征点数",
            "FRAME_BUFFER_SIZE": "哈希缓冲区大小",
            "HASH_THRESHOLD": "哈希距离阈值",
            "TOTAL_WAVE_REFRESH_SEC": "总张数波形刷新(s)",
            "FILTER_TRIGGER_WINDOW_MS": "触发窗口(ms)",
            "FILTER_TRIGGER_COUNT": "触发张数阈值",
            "FULL_FILTER_HOLD_SEC": "完整过滤保持(s)",
            "BASIC_CORR_THRESHOLD": "相似度阈值",
            "BASIC_MIN_RAW_RATIO_STILL": "基础静止变化面积",
            "FULL_CORR_THRESHOLD": "相似度阈值",
            "FULL_STILL_RATIO": "静止变化面积",
            "LOCAL_AREA_THRESH": "局部面积占比下限",
            "LOCAL_BBOX_RATIO_MAX": "局部包围盒上限",
            "LOCAL_ASPECT_RATIO_MAX": "局部宽高比上限",
            "LAYER_MIN_VALID_POINTS": "光流最小有效点数",
            "LAYER_MIN_MOVING_POINTS": "光流最小移动点数",
            "LAYER_DIRECTION_CONSISTENCY": "光流方向一致性",
            "LAYER_MEAN_VEC_MIN": "主方向最小模长",
            "LAYER_COS_SIM_THRESH": "夹角阈值",
            "FLOW_LAYER_STATIC_THRESH": "残余移动阈值",
            "FLOW_QUALITY_LEVEL": "角点质量阈值",
            "FLOW_MIN_DISTANCE": "角点最小间距",
            "PREVIEW_DENSE_ALPHA": "稠密光流透明度",
            "PREVIEW_DIFF_DECAY": "差分衰减",
            "PREVIEW_MOTION_DECAY": "运动衰减",
            "PREVIEW_MOTION_MAX_SPEED": "预览最大速度",
            "ZOOM_DIRECTION_CONSISTENCY": "缩放径向一致性",
            "ZOOM_RADIAL_CORRELATION": "缩放距离相关度",
            "OPED_DETECTION_ENABLED": "启用OP/ED检测",
            "OPED_WINDOW_SEC": "匹配窗口秒数",
            "OPED_MATCH_THRESHOLD": "匹配相关系数",
            "OPED_HISTORY_HOURS": "历史时长(h)",
            "OPED_SAMPLE_INTERVAL_SEC": "采样间隔(s)",
        }

        # 参数分组
        groups = [
            ("基本", ["REFRESH_INTERVAL", "SCALE_FACTOR", "SSIM_THRESHOLD", "JingzhiShiJian", "CROP_RATIO", "ALPHA"]),
            ("波形1", ["WAVE_HISTORY_SEC", "WAVE_REFRESH_MS"]),
            ("波形2", ["WAVE2_HISTORY_SEC", "WAVE2_REFRESH_MS", "WAVE_MAX_Y"]),
            ("变化检测", ["MIN_DIFF_THRESHOLD", "MAX_DIFF_THRESHOLD", "MIN_CHANGE_RATIO",
                          "ALIGNED_CHANGE_THRESHOLD", "SIGNIFICANT_CHANGE_RATIO"]),
            ("光流法", ["FLOW_FEATURE_COUNT", "FLOW_QUALITY_LEVEL", "FLOW_MIN_DISTANCE",
                        "FLOW_LAYER_STATIC_THRESH"]),
            ("重复帧过滤", ["FRAME_BUFFER_SIZE", "HASH_THRESHOLD"]),
            ("总张数波形", ["TOTAL_WAVE_REFRESH_SEC"]),
            ("动态过滤触发", ["FILTER_TRIGGER_WINDOW_MS", "FILTER_TRIGGER_COUNT", "FULL_FILTER_HOLD_SEC"]),
            ("基础检测常量", ["BASIC_CORR_THRESHOLD", "BASIC_MIN_RAW_RATIO_STILL"]),
            ("完整检测常量", ["FULL_CORR_THRESHOLD", "FULL_STILL_RATIO"]),
            ("局部运动判断", ["LOCAL_AREA_THRESH", "LOCAL_BBOX_RATIO_MAX", "LOCAL_ASPECT_RATIO_MAX"]),
            ("光流图层分离", ["LAYER_MIN_VALID_POINTS", "LAYER_MIN_MOVING_POINTS",
                              "LAYER_DIRECTION_CONSISTENCY", "LAYER_MEAN_VEC_MIN", "LAYER_COS_SIM_THRESH"]),
            ("缩放运镜", ["ZOOM_DIRECTION_CONSISTENCY", "ZOOM_RADIAL_CORRELATION"]),
            ("预览参数", ["PREVIEW_DENSE_ALPHA", "PREVIEW_DIFF_DECAY", "PREVIEW_MOTION_DECAY", "PREVIEW_MOTION_MAX_SPEED"]),
            ("OP/ED检测", ["OPED_DETECTION_ENABLED", "OPED_WINDOW_SEC", "OPED_MATCH_THRESHOLD", "OPED_HISTORY_HOURS",
                           "OPED_SAMPLE_INTERVAL_SEC"]),
        ]

        # 截图区域比例标签
        self.crop_ratio_label_var = tk.StringVar()
        if CONFIG.get("CROP_REGION"):
            self.crop_ratio_label_var.set("手动区域缩放比例:")
        else:
            self.crop_ratio_label_var.set("截图区域比例:")

        entries = {}
        left_frame = tk.Frame(scroll_frame, bg=bg)
        right_frame = tk.Frame(scroll_frame, bg=bg)
        left_frame.grid(row=0, column=0, sticky="nw", padx=(0, 5))
        right_frame.grid(row=0, column=1, sticky="nw")

        # 生成参数输入框
        for idx, (group_name, keys) in enumerate(groups):
            target = left_frame if idx % 2 == 0 else right_frame
            row = target.grid_size()[1]
            tk.Label(target, text=group_name, font=("Arial", 10, "bold"),
                     fg=accent, bg=bg).grid(row=row, column=0, columnspan=2, sticky="w", pady=(10, 2))
            row += 1
            for key in keys:
                if key == "CROP_RATIO":
                    tk.Label(target, textvariable=self.crop_ratio_label_var, fg=accent, bg=bg,
                             font=("Arial", 9)).grid(row=row, column=0, sticky="w", padx=5)
                else:
                    label_text = param_names.get(key, key) + ":"
                    tk.Label(target, text=label_text, fg=accent, bg=bg,
                             font=("Arial", 9)).grid(row=row, column=0, sticky="w", padx=(0, 2))
                var = tk.StringVar(value=str(CONFIG[key]))
                ent = tk.Entry(target, textvariable=var, width=5,
                               font=("Arial", 9), bg=btn_bg, fg=accent,
                               insertbackground=accent)
                ent.grid(row=row, column=1, sticky="w", padx=(2, 0))
                entries[key] = var
                row += 1

        # 功能开关
        row_left = left_frame.grid_size()[1]
        tk.Label(left_frame, text="功能开关", font=("Arial", 10, "bold"),
                 fg=accent, bg=bg).grid(row=row_left, column=0, columnspan=2, sticky="w", pady=(10, 2))
        row_left += 1
        flow_var = tk.BooleanVar(value=self.use_optical_flow)
        tk.Checkbutton(left_frame, text="启用光流法图层分离", variable=flow_var,
                       fg=accent, bg=bg, selectcolor=btn_bg).grid(row=row_left, column=0, columnspan=2, sticky="w",
                                                                  padx=5)
        row_left += 1
        hash_var = tk.BooleanVar(value=self.use_hash_filter)
        tk.Checkbutton(left_frame, text="启用重复帧过滤", variable=hash_var,
                       fg=accent, bg=bg, selectcolor=btn_bg).grid(row=row_left, column=0, columnspan=2, sticky="w",
                                                                  padx=5)
        row_left += 1

        # 全局调色板
        tk.Label(left_frame, text="全局调色板", font=("Arial", 10, "bold"),
                 fg=accent, bg=bg).grid(row=row_left, column=0, columnspan=2, sticky="w", pady=(10, 2))
        row_left += 1
        self.custom_color_var = tk.BooleanVar(value=CONFIG["USE_CUSTOM_COLORS"])

        def toggle_custom_colors():
            CONFIG["USE_CUSTOM_COLORS"] = self.custom_color_var.get()
            if CONFIG["USE_CUSTOM_COLORS"]:
                color_frame.grid()
                for child in color_frame.winfo_children():
                    if isinstance(child, tk.Entry) or isinstance(child, tk.Button):
                        child.configure(state="normal")
            else:
                color_frame.grid_remove()
            color_manager.apply_theme()
            self._redraw_all()

        tk.Checkbutton(left_frame, text="启用自定义调色板", variable=self.custom_color_var,
                       command=toggle_custom_colors,
                       fg=accent, bg=bg, selectcolor=btn_bg).grid(row=row_left, column=0, columnspan=2, sticky="w",
                                                                  padx=5)
        row_left += 1

        color_frame = tk.Frame(left_frame, bg=bg)
        color_frame.grid(row=row_left, column=0, columnspan=2, sticky="we", padx=5)
        if not CONFIG["USE_CUSTOM_COLORS"]:
            color_frame.grid_remove()
        row_left += 1

        color_keys = [
            ("accent", "主色调"), ("bg", "背景"), ("secondary", "次要"),
            ("btn_bg", "按钮背景"), ("title_bg", "标题栏背景"), ("canvas_bg", "画布背景"),
            ("wave_line_filtered", "波形1过滤后"), ("wave_line_raw", "波形1过滤前"),
            ("filter_translation", "平移过滤"), ("filter_optical_flow", "光流过滤"),
            ("filter_hash", "哈希过滤"), ("filter_still", "静止过滤"),
            ("filter_local", "局部过滤"), ("filter_zoom", "缩放过滤"),
            ("filter_other", "其他过滤"),
            ("filter_raw_total", "过滤前总张数"), ("filter_filtered_total", "过滤后总张数")
        ]
        self.color_vars = {}
        row_color = 0
        for key, name in color_keys:
            tk.Label(color_frame, text=name + ":", fg=accent, bg=bg).grid(row=row_color, column=0, sticky="w")
            var = tk.StringVar(value=CONFIG["COLORS"].get(key, "#FFFFFF"))
            ent = tk.Entry(color_frame, textvariable=var, width=8, bg=btn_bg, fg=accent,
                           state="normal" if CONFIG["USE_CUSTOM_COLORS"] else "disabled")
            ent.grid(row=row_color, column=1, sticky="w")

            def make_color_callback(k, v):
                return lambda: self._pick_color(k, v)

            btn = tk.Button(color_frame, text="  ", bg=var.get(), command=make_color_callback(key, var),
                            state="normal" if CONFIG["USE_CUSTOM_COLORS"] else "disabled")
            btn.grid(row=row_color, column=2, padx=2)
            self.color_vars[key] = (var, btn)
            row_color += 1

        def restore_default_colors():
            for key, (var, btn) in self.color_vars.items():
                default_color = CONFIG_DEFAULT["COLORS"].get(key, "#FFFFFF")
                var.set(default_color)
                btn.configure(bg=default_color)
            if CONFIG["USE_CUSTOM_COLORS"]:
                self._apply_custom_colors()

        tk.Button(color_frame, text="恢复默认颜色", command=restore_default_colors,
                  bg=btn_bg, fg=accent).grid(row=row_color, column=0, columnspan=3, pady=5)

        # 布局管理（右侧）
        row_right = right_frame.grid_size()[1]
        tk.Label(right_frame, text="布局管理", font=("Arial", 10, "bold"),
                 fg=accent, bg=bg).grid(row=row_right, column=0, columnspan=2, sticky="w", pady=(10, 2))
        row_right += 1
        layout_listbox = tk.Listbox(right_frame, bg=btn_bg, fg=accent, height=5, selectmode=tk.SINGLE)
        layout_listbox.grid(row=row_right, column=0, columnspan=2, sticky="we", padx=5)
        row_right += 1

        def update_layout_list():
            layout_listbox.delete(0, tk.END)
            for name in self.layouts.keys():
                display = f"* {name}" if name == self.active_layout else name
                layout_listbox.insert(tk.END, display)

        update_layout_list()

        btn_frame_layout = tk.Frame(right_frame, bg=bg)
        btn_frame_layout.grid(row=row_right, column=0, columnspan=2, pady=5)
        row_right += 1

        def save_named_layout():
            import tkinter.simpledialog as simpledialog
            name = simpledialog.askstring("保存布局", "输入布局名称:", parent=win)
            if name:
                self.layouts[name] = {
                    'window_geometry': win.winfo_geometry(),
                    'panels': {n: p.get_geometry() for n, p in panels.items()}
                }
                update_layout_list()
                self._save_all_settings()

        def load_selected_layout():
            sel = layout_listbox.curselection()
            if sel:
                name = layout_listbox.get(sel[0]).replace('* ', '')
                if name in self.layouts:
                    layout_dict = self.layouts[name]
                    try:
                        win.geometry(layout_dict['window_geometry'])
                    except:
                        pass
                    for pname, geom in layout_dict.get('panels', {}).items():
                        if pname in panels:
                            panels[pname].apply_geometry(geom)

        def delete_layout():
            sel = layout_listbox.curselection()
            if sel:
                name = layout_listbox.get(sel[0]).replace('* ', '')
                if name in self.layouts:
                    del self.layouts[name]
                    if self.active_layout == name:
                        self.active_layout = None
                    update_layout_list()
                    self._save_all_settings()

        def set_active_layout():
            sel = layout_listbox.curselection()
            if sel:
                name = layout_listbox.get(sel[0]).replace('* ', '')
                self.active_layout = name
                update_layout_list()
                self._save_all_settings()

        tk.Button(btn_frame_layout, text="保存布局", command=save_named_layout,
                  bg=btn_bg, fg=accent).pack(side="left", padx=2)
        tk.Button(btn_frame_layout, text="加载", command=load_selected_layout,
                  bg=btn_bg, fg=accent).pack(side="left", padx=2)
        tk.Button(btn_frame_layout, text="删除", command=delete_layout,
                  bg=btn_bg, fg=accent).pack(side="left", padx=2)
        tk.Button(btn_frame_layout, text="设为当前", command=set_active_layout,
                  bg=btn_bg, fg=accent).pack(side="left", padx=2)
        # ---------- 运行记录管理 ----------
        # 添加在布局管理下方
        tk.Label(right_frame, text="运行记录管理", font=("Arial", 10, "bold"),
                 fg=accent, bg=bg).grid(row=row_right, column=0, columnspan=2, sticky="w", pady=(10, 2))
        row_right += 1

        record_listbox = tk.Listbox(right_frame, bg=btn_bg, fg=accent, height=6, selectmode=tk.SINGLE)
        record_listbox.grid(row=row_right, column=0, columnspan=2, sticky="we", padx=5)
        row_right += 1

        btn_frame_records = tk.Frame(right_frame, bg=bg)
        btn_frame_records.grid(row=row_right, column=0, columnspan=2, pady=5)
        row_right += 1

        def load_record_list():
            record_listbox.delete(0, tk.END)
            records = self.load_records()
            for i, rec in enumerate(records):
                display = f"{rec['name']}  |  {rec.get('record_time', '')}"
                record_listbox.insert(tk.END, display)

        def view_record():
            sel = record_listbox.curselection()
            if not sel:
                return
            records = self.load_records()
            idx = sel[0]
            if idx >= len(records):
                return
            rec = records[idx]
            avg_fps_val = rec.get('avg_fps', 'N/A')
            if isinstance(avg_fps_val, (int, float)):
                avg_fps_str = f"{avg_fps_val:.2f}"
            else:
                avg_fps_str = str(avg_fps_val)

            info = (f"名称: {rec['name']}\n"
                    f"总张数: {rec.get('total_cels', 'N/A')}\n"
                    f"总时长: {rec.get('total_time', 'N/A')} 秒\n"
                    f"平均帧率: {avg_fps_str}\n"
                    f"记录时间: {rec.get('record_time', 'N/A')}")
            import tkinter.messagebox as messagebox
            messagebox.showinfo("记录详情", info)

        def edit_record():
            sel = record_listbox.curselection()
            if not sel:
                return
            records = self.load_records()
            idx = sel[0]
            if idx >= len(records):
                return
            rec = records[idx]
            # 编辑对话框
            edit_win = tk.Toplevel(win)
            edit_win.title("编辑记录")
            edit_win.configure(bg=bg)
            tk.Label(edit_win, text="名称:", fg=accent, bg=bg).grid(row=0, column=0, padx=5, pady=5)
            name_var = tk.StringVar(value=rec['name'])
            tk.Entry(edit_win, textvariable=name_var, bg=btn_bg, fg=accent).grid(row=0, column=1, padx=5, pady=5)

            tk.Label(edit_win, text="总张数:", fg=accent, bg=bg).grid(row=1, column=0, padx=5, pady=5)
            cels_var = tk.StringVar(value=str(rec.get('total_cels', 0)))
            tk.Entry(edit_win, textvariable=cels_var, bg=btn_bg, fg=accent).grid(row=1, column=1, padx=5, pady=5)

            tk.Label(edit_win, text="总时长(s):", fg=accent, bg=bg).grid(row=2, column=0, padx=5, pady=5)
            time_var = tk.StringVar(value=str(rec.get('total_time', 0)))
            tk.Entry(edit_win, textvariable=time_var, bg=btn_bg, fg=accent).grid(row=2, column=1, padx=5, pady=5)

            def save_changes():
                try:
                    new_name = name_var.get().strip()
                    new_cels = int(float(cels_var.get()))
                    new_time = float(time_var.get())
                    if not new_name:
                        return
                    records[idx]['name'] = new_name
                    records[idx]['total_cels'] = new_cels
                    records[idx]['total_time'] = new_time
                    records[idx]['avg_fps'] = new_cels / new_time if new_time > 0 else 0
                    self.save_records(records)
                    load_record_list()
                    edit_win.destroy()
                except ValueError:
                    import tkinter.messagebox as messagebox
                    messagebox.showerror("输入错误", "请输入有效的数字")

            tk.Button(edit_win, text="保存", command=save_changes,
                      bg=btn_bg, fg=accent).grid(row=3, column=0, columnspan=2, pady=10)
            edit_win.transient(win)
            edit_win.grab_set()

        def delete_record():
            sel = record_listbox.curselection()
            if not sel:
                return
            records = self.load_records()
            idx = sel[0]
            if idx >= len(records):
                return
            import tkinter.messagebox as messagebox
            if messagebox.askyesno("确认删除", "确定删除该记录吗？"):
                self.delete_record(idx)
                load_record_list()

        tk.Button(btn_frame_records, text="查看", command=view_record,
                  bg=btn_bg, fg=accent).pack(side="left", padx=2)
        tk.Button(btn_frame_records, text="编辑", command=edit_record,
                  bg=btn_bg, fg=accent).pack(side="left", padx=2)
        tk.Button(btn_frame_records, text="删除", command=delete_record,
                  bg=btn_bg, fg=accent).pack(side="left", padx=2)
        tk.Button(btn_frame_records, text="刷新", command=load_record_list,
                  bg=btn_bg, fg=accent).pack(side="left", padx=2)

        load_record_list()  # 初始加载
        # ---------- 保存 / 恢复默认设置函数 ----------
        def save_settings():
            """保存当前参数到 CONFIG 并应用"""
            for key, var in entries.items():
                try:
                    orig = CONFIG[key]
                    if isinstance(orig, int):
                        CONFIG[key] = int(float(var.get()))
                    else:
                        CONFIG[key] = float(var.get())
                except ValueError:
                    pass
            self.use_optical_flow = flow_var.get()
            self.use_hash_filter = hash_var.get()
            self.hash_threshold = CONFIG["HASH_THRESHOLD"]
            with self.lock:
                new_size = CONFIG["FRAME_BUFFER_SIZE"]
                if self.use_hash_filter and new_size != self.frame_buffer.maxlen:
                    self.frame_buffer = deque(self.frame_buffer, maxlen=new_size)
                elif not self.use_hash_filter:
                    self.frame_buffer.clear()

            if CONFIG["USE_CUSTOM_COLORS"]:
                for key, (var, _) in self.color_vars.items():
                    CONFIG["COLORS"][key] = var.get()
            self._apply_custom_colors()

            monitor_sample = self.camera.get_latest_frame()
            if monitor_sample is not None:
                h, w = monitor_sample.shape[:2]
                if CONFIG.get("CROP_REGION"):
                    base_rect = CONFIG["CROP_REGION"]
                else:
                    base_rect = (0, 0, w, h)
                self.region = self._compute_region_from_base(base_rect, CONFIG["CROP_RATIO"], w, h)
                keep_h = self.region[3] - self.region[1]
                keep_w = self.region[2] - self.region[0]
                self.frame_shape = (int(keep_h * CONFIG["SCALE_FACTOR"]),
                                    int(keep_w * CONFIG["SCALE_FACTOR"]))

            with self.lock:
                self.last_frame = None
                if self.use_hash_filter:
                    self.frame_buffer.clear()

            self._save_all_settings()

        def reset_to_default():
            """恢复所有参数为默认值"""
            CONFIG.clear()
            CONFIG.update(copy.deepcopy(CONFIG_DEFAULT))
            for key, var in entries.items():
                var.set(str(CONFIG[key]))
            flow_var.set(True)
            hash_var.set(False)
            self.use_optical_flow = True
            self.use_hash_filter = False
            self.hash_threshold = CONFIG["HASH_THRESHOLD"]
            with self.lock:
                self.frame_buffer = deque(maxlen=CONFIG["FRAME_BUFFER_SIZE"])
            self.custom_color_var.set(CONFIG_DEFAULT["USE_CUSTOM_COLORS"])
            for key, (var, btn) in self.color_vars.items():
                var.set(CONFIG_DEFAULT["COLORS"].get(key, "#FFFFFF"))
                btn.configure(bg=CONFIG_DEFAULT["COLORS"].get(key, "#FFFFFF"))
            self._apply_custom_colors()
            try:
                os.remove(self._get_settings_path())
            except FileNotFoundError:
                pass


        # 鼠标滚轮绑定
        def on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        canvas.bind_all("<MouseWheel>", on_mousewheel)
        # ========== 顶部固定按钮栏（创建于所有函数定义之后） ==========
        top_btn_frame = tk.Frame(parent, bg=bg)
        # 使用 before 参数，让这个 frame 显示在 canvas 之前（即上方）
        top_btn_frame.pack(side="top", fill="x", before=canvas, pady=(5, 0), padx=5)

        row1 = tk.Frame(top_btn_frame, bg=bg)
        row1.pack(fill="x", pady=(0, 2))
        tk.Button(row1, text="保存", command=save_settings,
                  bg=btn_bg, fg=accent).pack(side="left", padx=3)
        tk.Button(row1, text="恢复默认", command=reset_to_default,
                  bg=btn_bg, fg=accent).pack(side="left", padx=3)
        tk.Button(row1, text="说明", command=self.open_readme,
                  bg=btn_bg, fg=accent).pack(side="left", padx=3)

        row2 = tk.Frame(top_btn_frame, bg=bg)
        row2.pack(fill="x", pady=(2, 5))
        tk.Button(row2, text="显示截图范围", command=self.show_crop_region,
                  bg=btn_bg, fg=accent).pack(side="left", padx=3)
        tk.Button(row2, text="保存运行记录", command=self._save_record,
                  bg=btn_bg, fg=accent).pack(side="left", padx=3)


        return entries

    # ---------- 运行记录管理方法 ----------
    def _get_records_path(self):
        settings_dir = os.path.dirname(self._get_settings_path())
        return os.path.join(settings_dir, "cel_counter_records.json")

    def load_records(self):
        path = self._get_records_path()
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    def save_records(self, records):
        path = self._get_records_path()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(records, f, indent=2, ensure_ascii=False)

    def delete_record(self, index):
        records = self.load_records()
        if 0 <= index < len(records):
            del records[index]
            self.save_records(records)

    def _pick_color(self, key, var):
        """颜色选择器回调"""
        from tkinter import colorchooser
        color = colorchooser.askcolor(color=var.get(), title=f"选择{key}颜色")
        if color[1]:
            var.set(color[1])
            self.color_vars[key][1].configure(bg=color[1])

    def _apply_custom_colors(self):
        """应用自定义调色板"""
        if CONFIG["USE_CUSTOM_COLORS"]:
            for key, (var, _) in self.color_vars.items():
                CONFIG["COLORS"][key] = var.get()
        color_manager._config = CONFIG["COLORS"]
        color_manager.apply_theme()
        self._redraw_all()

    def _redraw_all(self):
        """重绘所有波形和按钮"""
        self.draw_wave()
        self.draw_wave2()
        if hasattr(self, 'settings_wave_canvas') and self.settings_wave_canvas:
            self._draw_settings_wave()
        self._draw_buttons()

    def _on_preview_toggle(self, state):
        """预览开关回调"""
        if state:
            self.preview_manager.start()
        else:
            self.preview_manager.stop()

    # ---------- 屏幕捕获 ----------
    def _get_screen_dxcam(self):
        """使用 dxcam 获取当前帧的灰度缩小图像"""
        try:

            frame = self.camera.get_latest_frame()
            if frame is None:
                with self.lock:
                    return self.last_frame.copy() if self.last_frame is not None else np.zeros(self.frame_shape, dtype=np.uint8)
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
                return self.last_frame.copy() if self.last_frame is not None else np.zeros(self.frame_shape, dtype=np.uint8)

    # ---------- 主捕获与检测循环 ----------
    def _capture_loop(self):
        """
        主截图与检测循环，运行在独立线程中。
        核心流程：截帧 -> 灰度/缩放 -> 原始变化检测 -> 基础/完整检测 -> 哈希过滤 -> 计数
        同时维护 OP/ED 历史波形、每秒采样，并周期性执行 OP/ED 匹配。
        当匹配到已知 OP/ED 片段时，暂停全局张数与时间统计，仅记录首次出现的片段数据。
        """
        target_fps = 48
        target_interval = 1.0 / target_fps
        next_frame_time = time.perf_counter()

        # OP/ED 相关局部变量
        last_oped_detect_time = 0.0          # 上次执行 OP/ED 检测的时间戳
        oped_sample_interval = CONFIG["OPED_SAMPLE_INTERVAL_SEC"]
        oped_detect_interval = 2.0           # 每2秒执行一次全量检测，避免频繁滑动窗口

        while not self._stop_event.is_set():
            self._run_event.wait()           # 暂停时阻塞
            if self._stop_event.is_set():
                break

            # ---------- 帧率控制 ----------
            now = time.perf_counter()
            if now < next_frame_time:
                sleep_time = next_frame_time - now
                if sleep_time > 0.002:
                    time.sleep(sleep_time * 0.8)
                while time.perf_counter() < next_frame_time:
                    pass

            # 获取当前帧
            now_frame = self._get_screen_dxcam()
            now_ts = time.time()

            # 更新帧率目标
            if time.perf_counter() > next_frame_time + target_interval:
                next_frame_time = time.perf_counter() + target_interval
            else:
                next_frame_time += target_interval

            # 获取上一帧（线程安全）
            with self.lock:
                last = self.last_frame.copy() if self.last_frame is not None else None

            new_cel = 0
            raw_new_cel = 0
            move_type = "静止"

            if last is not None:
                # 原始变化检测
                raw_detected = is_raw_change(last, now_frame)
                raw_new_cel = 1 if raw_detected else 0

                # 基础检测（始终执行）
                basic_detected, move_type = basic_is_new_cel(last, now_frame)
                if basic_detected:
                    self.raw_detection_timestamps.append(now_ts)

                # 动态过滤窗口维护
                window_sec = CONFIG["FILTER_TRIGGER_WINDOW_MS"] / 1000.0
                while self.raw_detection_timestamps and self.raw_detection_timestamps[0] < now_ts - window_sec:
                    self.raw_detection_timestamps.popleft()

                # 决定是否启用完整检测
                if len(self.raw_detection_timestamps) >= CONFIG["FILTER_TRIGGER_COUNT"]:
                    if not self.full_filter_active:
                        self.full_filter_active = True
                    self.full_filter_active_until = now_ts + CONFIG["FULL_FILTER_HOLD_SEC"]
                elif self.full_filter_active and now_ts > self.full_filter_active_until:
                    self.full_filter_active = False

                # 最终检测：基础或完整
                if self.full_filter_active:
                    final_detected, move_type = full_is_new_cel(last, now_frame, self.use_optical_flow)
                else:
                    final_detected = basic_detected

                # 哈希重复帧过滤
                curr_hash = None
                if self.use_hash_filter:
                    curr_hash = compute_hash(now_frame)

                duplicate = False
                if self.use_hash_filter and curr_hash is not None:
                    with self.lock:
                        buf = list(self.frame_buffer)
                        for h in buf[:-1]:
                            if np.sum(curr_hash != h) <= self.hash_threshold:
                                duplicate = True
                                break
                    if not duplicate and final_detected:
                        new_cel = 1
                else:
                    new_cel = 1 if final_detected else 0

                # 过滤类型统计（仅当原始检测到但最终未计数时）
                if raw_new_cel == 1 and new_cel == 0:
                    if duplicate and final_detected:
                        self.total_hash_filtered += 1
                    else:
                        if move_type == "全局平移":
                            self.total_translation_filtered += 1
                        elif move_type == "图层分离运镜":
                            self.total_optical_flow_filtered += 1
                        elif move_type == "缩放运镜":
                            self.total_zoom_filtered += 1
                        elif move_type == "极慢平移/静止":
                            self.total_still_filtered += 1
                        elif move_type == "局部变化(过滤)":
                            self.total_local_filtered += 1
                        else:
                            self.total_other_unknown_filtered += 1

                # 更新哈希缓冲区
                if self.use_hash_filter:
                    with self.lock:
                        self.frame_buffer.append(curr_hash)
            else:
                # 首帧：直接记录哈希
                if self.use_hash_filter:
                    with self.lock:
                        self.frame_buffer.append(compute_hash(now_frame))

            if new_cel:
                self.total_cels_count += 1
                if not self._has_first_frame:
                    self._has_first_frame = True

            if raw_new_cel:
                self.total_raw_cels_count += 1



            # 更新上一帧
            with self.lock:
                self.last_frame = now_frame
                if not self._has_first_frame:
                    self._has_first_frame = True

            # 更新预览缓存
            self.preview_cache = (last, now_frame.copy() if now_frame is not None else None)

            # 实时张数滑动窗口更新
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
                self.current_raw_cels = cels_raw
                self.last_move_type = move_type if last is not None else "静止"

            # OP/ED 历史采样（每秒一次）
            if self.oped_enabled and now_ts - self.oped_last_sample_time >= oped_sample_interval:
                with self.lock:
                    fc = self.current_cels
                    rc = self.current_raw_cels
                self.oped_history_time.append(self.elapsed_time)
                self.oped_history_filtered.append(fc)
                self.oped_history_raw.append(rc)

                # 图像哈希采样
                if CONFIG.get("OPED_HASH_ENABLED", True):
                    h = compute_oped_hash(now_frame)
                    self.oped_hash_seq.append((self.elapsed_time, h))

                self.instant_fps_history.append((self.elapsed_time, self.current_cels))

                max_points = int(CONFIG["OPED_HISTORY_HOURS"] * 3600 / oped_sample_interval)
                while len(self.oped_history_time) > max_points:
                    self.oped_history_time.popleft()
                    self.oped_history_filtered.popleft()
                    self.oped_history_raw.popleft()
                while len(self.oped_hash_seq) > max_points:
                    self.oped_hash_seq.popleft()

                self.oped_last_sample_time = now_ts
            # ====== 新增：定期触发 OP/ED 检测 ======
            # 每 2 秒执行一次全量检测（与 last_oped_detect_time 配合）
            if self.oped_enabled:
                if not hasattr(self, '_last_oped_detect_time'):
                    self._last_oped_detect_time = 0.0
                if now_ts - self._last_oped_detect_time >= 2.0:
                    self._detect_oped()
                    self._last_oped_detect_time = now_ts
    # ---------- UI 更新与波形 ----------
    def loop(self):
        """主 UI 更新循环"""
        if self.is_running:
            now_real = time.time()

            self.elapsed_time += now_real - self.last_loop_time
            self.last_loop_time = now_real

            # 每秒记录一次历史数据
            if not hasattr(self, '_last_recorded_time'):
                self._last_recorded_time = -1
            if self.elapsed_time - self._last_recorded_time >= 1.0:
                with self.lock:
                    total_r = self.total_raw_cels_count
                    total_f = self.total_cels_count
                    trans_f = self.total_translation_filtered
                    flow_f = self.total_optical_flow_filtered
                    hash_f = self.total_hash_filtered
                    still_f = self.total_still_filtered
                    local_f = self.total_local_filtered
                    other_f = self.total_other_unknown_filtered
                    zoom_f = self.total_zoom_filtered
                self.total_history.append((self.elapsed_time, total_r, total_f,
                                           trans_f, flow_f, hash_f,
                                           still_f, local_f, other_f, zoom_f))
                self._last_recorded_time = self.elapsed_time

            with self.lock:
                current_total = self.total_cels_count
                has_frame = self._has_first_frame

            net_cels = current_total - self.oped_deducted_cels

            # 静止自动暂停检测
            if has_frame:
                if net_cels != self._prev_cels_count:
                    self.idle_start_time = None
                elif self.idle_start_time is None:
                    self.idle_start_time = self.elapsed_time
                elif self.elapsed_time - self.idle_start_time >= CONFIG["JingzhiShiJian"]:
                    self.pause()
            self._prev_cels_count = net_cels

            if net_cels != self._last_disp_cels:
                self.lb_total_cels.config(text=f"总张数：{net_cels}")
                self._last_disp_cels = net_cels

            # 刷新界面标签
            if now_real - self.last_refresh >= CONFIG["REFRESH_INTERVAL"]:
                with self.lock:
                    real = self.current_cels
                self.total_sum += real
                self.total_count += 1
                avg = self.total_sum / self.total_count if self.total_count > 0 else 0.0

                net_cels = current_total - self.oped_deducted_cels
                net_time = self.elapsed_time - self.oped_deducted_time
                avg_net = net_cels / net_time if net_time > 0 else 0.0

                accent = color_manager.get_color('accent')
                self.lb_rt.config(text=f"实时张数：{real:.1f} ", fg=accent)
                self.lb_total_time.config(text=f"运行时长：{net_time:.1f} s", fg=accent)
                self.lb_total_cels.config(text=f"总张数：{net_cels}", fg=accent)
                self.lb_avg.config(text=f"总平均：{avg_net:.1f}", fg=accent)
                status = self.get_status(real)
                self.lb_st.config(text=status, fg=accent)
                self.last_refresh = now_real
                self._last_disp_cels = current_total

        self.root.after(20, self.loop)

    def pause(self):
        """暂停/继续"""
        self.is_running = not self.is_running
        if self.is_running:
            self._run_event.set()
            self.last_loop_time = time.time()
            try:
                self.camera.release()
            except:
                pass
            self.camera = dxcam.create(output_color="BGR")
            self.camera.start(target_fps=0, video_mode=False)
        else:
            self._run_event.clear()
        self.idle_start_time = None
        self._draw_buttons()

    def reset_stats(self):
        """重置所有统计数据"""
        was_running = self.is_running
        if was_running:
            self._run_event.clear()
            time.sleep(0.05)

        with self.lock:
            self.recent_frames.clear()
            self.recent_sum = 0
            self.current_cels = 0.0
            self.recent_frames_raw.clear()
            self.recent_sum_raw = 0
            self.current_raw_cels = 0.0
            self.total_raw_cels_count = 0
            self.last_frame = None
            self.total_cels_count = 0
            self._has_first_frame = False
            self.frame_buffer.clear()

        self.total_sum = 0.0
        self.total_count = 0
        self.last_refresh = time.time()
        self.elapsed_time = 0.0
        self.last_loop_time = time.time()
        self.idle_start_time = None
        self._prev_cels_count = -1
        self._last_disp_cels = -1
        self.last_move_type = "静止"
        self.total_history.clear()
        self._last_recorded_time = -1

        self.wave_data.clear()
        self.wave2_data.clear()
        self.wave_raw_data.clear()
        self.wave2_raw_data.clear()
        self.smooth_cels = 0.0
        self.smooth_raw_cels = 0.0

        self.full_filter_active = False
        self.full_filter_active_until = 0
        self.raw_detection_timestamps.clear()

        self.total_translation_filtered = 0
        self.total_optical_flow_filtered = 0
        self.total_hash_filtered = 0
        self.total_still_filtered = 0
        self.total_local_filtered = 0
        self.total_other_unknown_filtered = 0
        self.total_zoom_filtered = 0

        # 清理 OP/ED 检测数据
        self.oped_history_time.clear()
        self.oped_history_filtered.clear()
        self.oped_history_raw.clear()
        self.oped_hash_seq.clear()
        self.oped_last_sample_time = 0.0
        self.known_segments.clear()
        self.segment_occurrences.clear()
        with self.time_lock:
            self.skip_until_elapsed = 0.0
        self.oped_active_segment = None
        self.oped_unique_cels = 0
        self.oped_unique_time = 0.0
        self.oped_unique_count = 0
        self.consecutive_match_start = None
        self.oped_deducted_cels = 0
        self.oped_deducted_time = 0.0
        self.deducted_intervals.clear()
        self.segment_occurrences.clear()
        self.known_segments.clear()
        accent = color_manager.get_color('accent')
        self.lb_rt.config(text="实时张数：0.0 ")
        self.lb_total_time.config(text="运行时长：0.0 s")
        self.lb_total_cels.config(text="总张数：0")
        self.lb_avg.config(text="总平均：0.0")
        self.lb_st.config(text="静止/纯运镜")
        self.draw_wave()
        self.draw_wave2()
        # 重置连续匹配状态
        self.oped_match_count = 0
        self.oped_mismatch_count = 0
        self.oped_match_active = False
        self.oped_match_start = None
        self.oped_pending_id = None
        self.oped_templates.clear()
        self.oped_next_color_idx = 0
        if was_running:
            self._run_event.set()
            try:
                self.camera.release()
            except:
                pass
            self.camera = dxcam.create(output_color="BGR")
            self.camera.start(target_fps=0, video_mode=False)

    def _on_close(self):
        """关闭程序"""
        self._stop_event.set()
        self._run_event.set()
        self.preview_manager.stop()
        self.root.destroy()

        self.end_real_time = time.time()
    def get_status(self, v):
        """根据当前张数返回状态描述"""
        if self.full_filter_active:
            base = "完整 "
        else:
            base = "基础 "
        mt = self.last_move_type
        if mt == "极慢平移/静止":
            return base + "静止"
        elif mt == "全局平移":
            return base + "平移运镜"
        elif mt == "图层分离运镜":
            return base + "多层平移"
        elif mt == "局部变化(过滤)":
            return base + "局部变化"
        elif mt == "静止":
            return base + "基本静止"
        elif mt == "新作画" or mt == "新作画(基础)":
            if v < 5.0:
                return base + "微动"
            elif v < 8.0:
                return base + "一拍三"
            elif v < 13.0:
                return base + "一拍二"
            elif v < 20.0:
                return base + "一拍一"
            else:
                return base + "全动画"
        else:
            if v < 0.3:
                return base + "极低变化"
            elif v < 1.0:
                return base + "极微变化"
            elif v < 5.0:
                return base + "微动"
            elif v < 8.0:
                return base + "一拍三"
            elif v < 13.0:
                return base + "一拍二"
            elif v < 20.0:
                return base + "高频作画"

    def update_wave(self):
        """更新波形1数据"""
        if self.is_running:
            with self.lock:
                cels = self.current_cels
                raw_cels = self.current_raw_cels
            self.wave_data.append((self.elapsed_time, cels))
            self.wave_raw_data.append((self.elapsed_time, raw_cels))
        self.draw_wave()
        self.root.after(CONFIG["WAVE_REFRESH_MS"], self.update_wave)

    def update_wave2(self):
        """更新波形2数据（长周期平滑）"""
        if self.is_running:
            with self.lock:
                raw = self.current_cels
                raw_raw = self.current_raw_cels
            self.smooth_cels = 0.2 * raw + 0.8 * self.smooth_cels
            self.smooth_raw_cels = 0.2 * raw_raw + 0.8 * self.smooth_raw_cels
            self.wave2_data.append((self.elapsed_time, self.smooth_cels))
            self.wave2_raw_data.append((self.elapsed_time, self.smooth_raw_cels))
        self.draw_wave2()
        self.root.after(CONFIG["WAVE2_REFRESH_MS"], self.update_wave2)

    def draw_wave(self):
        """绘制波形1（60秒）"""
        color_filtered = color_manager.get_color('wave_line_filtered')
        color_raw = color_manager.get_color('wave_line_raw')
        data_list = [
            (self.wave_raw_data, color_raw),
            (self.wave_data, color_filtered)
        ]
        self._draw_wave_multi(self.canvas, data_list,
                              CONFIG["WAVE_HISTORY_SEC"], CONFIG["WAVE_MAX_Y"], "60s")

    def draw_wave2(self):
        """绘制波形2（25分钟）"""
        color_filtered = color_manager.get_color('wave_line_filtered')
        color_raw = color_manager.get_color('wave_line_raw')
        data_list = [
            (self.wave2_raw_data, color_raw),
            (self.wave2_data, color_filtered)
        ]
        self._draw_wave_multi(self.canvas2, data_list,
                              CONFIG["WAVE2_HISTORY_SEC"], CONFIG["WAVE_MAX_Y"], "25min")

    def _draw_wave_multi(self, canvas, data_list, history_sec, y_max, title=""):
        """通用波形绘制，支持多条数据线及OP/ED高亮"""
        canvas.delete("all")
        width = canvas.winfo_width()
        height = canvas.winfo_height()
        if width < 50 or height < 50:
            canvas.after(100, lambda: self._draw_wave_multi(canvas, data_list, history_sec, y_max, title))
            return
        plot_w = width - self.wave_margin_left - self.wave_margin_right
        plot_h = height - self.wave_margin_top - self.wave_margin_bottom
        if plot_w <= 0 or plot_h <= 0:
            return
        now = self.elapsed_time
        start = now - history_sec
        accent = color_manager.get_color('accent')
        self._draw_axes_only(canvas, plot_w, plot_h, y_max, accent)
        if title:
            canvas.create_text(self.wave_margin_left, self.wave_margin_top - 5,
                               text=title, fill=accent, font=("Arial", 8), anchor="nw")

        # 绘制 OP/ED 背景高亮
        if self.oped_enabled:
            mark_color = "#FFD700"
            start_t = now - history_sec

            # 创建 seg_id → 颜色 的映射
            seg_colors = {t['id']: t['color'] for t in self.oped_templates}

            # 已结束的重复片段（来自 segment_occurrences 的记录）
            for s, e, sid in self.segment_occurrences:
                if e >= start_t and s <= now:
                    x1 = self.wave_margin_left + plot_w * (max(s, start_t) - start_t) / history_sec
                    x2 = self.wave_margin_left + plot_w * (min(e, now) - start_t) / history_sec
                    color = seg_colors.get(sid, mark_color)
                    canvas.create_rectangle(x1, self.wave_margin_top, x2, self.wave_margin_top + plot_h,
                                            fill=color, outline="", stipple="gray25")

            # 正在进行中的匹配区间（持续≥10秒时实时显示）
            if self.oped_match_active and self.oped_match_start is not None:
                current_duration = now - self.oped_match_start
                if current_duration >= 10:
                    x1 = self.wave_margin_left + plot_w * (max(self.oped_match_start, start_t) - start_t) / history_sec
                    x2 = self.wave_margin_left + plot_w * (min(now, now) - start_t) / history_sec
                    # 使用已分类的颜色（若未分类则用默认金色）
                    color = seg_colors.get(self.oped_pending_id,
                                           '#FFD700') if self.oped_pending_id is not None else '#FFD700'
                    canvas.create_rectangle(x1, self.wave_margin_top, x2, self.wave_margin_top + plot_h,
                                            fill=color, outline="", stipple="gray25")

        # 绘制数据线
        for wave_data, color in data_list:
            points = [(t, v) for t, v in wave_data if start <= t <= now]
            if not points:
                continue
            buckets = [[] for _ in range(plot_w)]
            for t, v in points:
                x = int((t - start) / history_sec * plot_w)
                x = max(0, min(plot_w - 1, x))
                buckets[x].append(min(v, y_max))
            line_pts = []
            for x in range(plot_w):
                if buckets[x]:
                    y = plot_h - (max(buckets[x]) / y_max) * plot_h
                    line_pts.append((self.wave_margin_left + x, self.wave_margin_top + y))
            if len(line_pts) >= 2:
                canvas.create_line(line_pts, fill=color, width=1, smooth=False)
    def _draw_axes_only(self, canvas, plot_w, plot_h, y_max, accent):
        """绘制坐标轴"""
        canvas.create_line(self.wave_margin_left, self.wave_margin_top,
                           self.wave_margin_left, self.wave_margin_top + plot_h, fill=accent)
        for y_val in [0, 6, 12, 18, 24]:
            if y_val > y_max:
                continue
            y = self.wave_margin_top + plot_h - (y_val / y_max) * plot_h
            canvas.create_line(self.wave_margin_left - 3, y, self.wave_margin_left, y, fill=accent)
            canvas.create_text(self.wave_margin_left - 8, y, text=str(y_val), fill=accent,
                               font=("Arial", 8), anchor="e")

    # ---------- OP/ED 检测核心 ----------
    def _detect_oped(self):
        """基于图像哈希的 OP/ED 重复检测（滑动窗口汉明距离最小）"""
        if not self.oped_enabled or not CONFIG.get("OPED_HASH_ENABLED", True):
            return
        if len(self.oped_hash_seq) < 2:
            return

        win_len = CONFIG["OPED_WINDOW_SEC"]
        sample_interval = CONFIG["OPED_SAMPLE_INTERVAL_SEC"]
        win_points = int(win_len / sample_interval)
        if len(self.oped_hash_seq) < 2 * win_points:
            return

        # 获取当前窗口的哈希序列（最近 win_points 帧）
        seq = list(self.oped_hash_seq)
        query = [h for _, h in seq[-win_points:]]
        history = [h for _, h in seq]

        max_dist = CONFIG["OPED_HASH_MAX_DIST"]
        best_start_idx = -1
        best_total_dist = float('inf')

        n_hist = len(history)
        dist_matrix = np.zeros((win_points, n_hist), dtype=np.int32)
        for k in range(win_points):
            q_hash = query[k]
            for t in range(n_hist):
                dist_matrix[k, t] = bin(q_hash ^ history[t]).count('1')

        # 获取 query 窗口的开始时间（用于排除重叠窗口）
        query_start_time = seq[-win_points][0] if len(seq) >= win_points else 0


        for t in range(n_hist - win_points + 1):
            # 排除与当前 query 窗口重叠的历史窗口
            hist_end_time = seq[t + win_points - 1][0]
            if hist_end_time >= query_start_time:
                continue

            total = 0
            for k in range(win_points):
                total += dist_matrix[k, t + k]
            if total < best_total_dist:
                best_total_dist = total
                best_start_idx = t
        if best_start_idx < 0:
            return

        similar_count = sum(1 for k in range(win_points)
                            if dist_matrix[k, best_start_idx + k] <= max_dist)
        match = (similar_count / win_points) >= CONFIG.get("OPED_HASH_WIN_RATIO", 0.8)
        # 调试打印：每次检测都输出关键信息
        print(f"[OPED] ratio={similar_count / win_points:.3f} "
              f"best_start={best_start_idx} "
              f"query_end={len(history) - 1} "
              f"match={match} "
              f"match_cnt={self.oped_match_count} "
              f"mismatch_cnt={self.oped_mismatch_count}")
        dists = [dist_matrix[k, best_start_idx + k] for k in range(win_points)]
        print("Dists sample:", dists[:10], "mean:", np.mean(dists))
        # 状态机：连续匹配判定
        if match:
            self.oped_match_count += 1
            self.oped_mismatch_count = 0
        else:
            self.oped_mismatch_count += 1
            self.oped_match_count = 0
        # 进入匹配状态
        if not self.oped_match_active and self.oped_match_count >= self.OPED_ENTER_COUNT:
            self.oped_match_active = True
            # 匹配起点为当前查询窗口的起始时间（即当前 OP/ED 的开始）
            self.oped_match_start = seq[-win_points][0] if len(seq) >= win_points else self.elapsed_time
            # 归类
            self.oped_pending_id = self._classify_hash_segment(query)




        # 退出匹配状态
        if self.oped_match_active and self.oped_mismatch_count >= self.OPED_EXIT_COUNT:
            self.oped_match_active = False
            match_end = seq[-1][0]  # 当前时间
            duration = match_end - self.oped_match_start


            # 基本高亮
            if duration >= 20:
                self.segment_occurrences.append(
                    (self.oped_match_start, match_end, self.oped_pending_id)
                )

            self.oped_pending_id = None


    def _find_nearest_index(self, lst, value):
        """返回列表中值最接近 value 的索引"""
        return min(range(len(lst)), key=lambda i: abs(lst[i] - value))

    def _schedule_refine(self, rough_start, rough_end, template_f, template_r, pending_id):
        """
        延迟启动精确定位修正（异步，无锁，防并发）
        """
        def delayed():
            with self._refine_lock:
                if self._refining:
                    return
                self._refining = True
            try:
                # 1. 快速复制所需数据（加锁）
                with self.lock:
                    times = list(self.oped_history_time)
                    f_vals = list(self.oped_history_filtered)
                    r_vals = list(self.oped_history_raw)
                # 2. 执行耗时计算（无锁）
                refined_start, refined_end = self._do_refine(
                    rough_start, rough_end,
                    template_f, template_r,
                    times, f_vals, r_vals
                )
                # 3. 快速写回结果（加锁）
                with self.lock:
                    # 更新 segment_occurrences
                    if (len(self.segment_occurrences) > 0 and
                            self.segment_occurrences[-1][2] == pending_id):
                        self.segment_occurrences[-1] = (refined_start, refined_end, pending_id)
                    else:
                        self.segment_occurrences.append((refined_start, refined_end, pending_id))
                    if refined_end - refined_start >= 30:
                        self._apply_oped_deduction(refined_start, refined_end)
            finally:
                with self._refine_lock:
                    self._refining = False

        timer = threading.Timer(5.0, delayed)
        timer.daemon = True
        timer.start()

    def _do_refine(self, rough_start, rough_end, template_f, template_r,
                   times, f_vals, r_vals):
        """
        基于 FFT 快速互相关精确定位 OP/ED 区间起止。
        步骤：
          1. 降采样到 2 秒间隔
          2. 在降采样信号上 FFT 互相关找到最佳偏移
          3. 在原采样率下，仅在最佳偏移附近小范围精确匹配
        此函数不持有任何锁，可安全在后台线程执行。
        """
        sample_interval = CONFIG["OPED_SAMPLE_INTERVAL_SEC"]
        win_len = CONFIG["OPED_WINDOW_SEC"]
        search_start = max(0, rough_start - 30)
        search_end = rough_end + 30

        if len(times) < 2:
            return rough_start, rough_end

        t_min, t_max = times[0], times[-1]
        search_start = max(search_start, t_min)
        search_end = min(search_end, t_max)
        if search_end - search_start < win_len:
            return rough_start, rough_end

        # ---------- 1. 生成等间隔采样信号（避免时间轴不均匀）----------
        t = np.array(times)
        f_arr = np.array(f_vals)
        r_arr = np.array(r_vals)
        # 统一时间轴：从 search_start 到 search_end - win_len，步长 sample_interval
        # 但 FFT 需要覆盖整个 search 范围到 search_end（含模板长度）
        # 信号长度：search_start ～ search_end
        # 模板长度：win_len 秒
        # 我们将信号扩展为 search_start ～ search_end，以便滑动模板
        uniform_t = np.arange(search_start, search_end + sample_interval, sample_interval)
        # 插值得到等间隔序列
        signal_f = np.interp(uniform_t, t, f_arr)
        signal_r = np.interp(uniform_t, t, r_arr)

        # 模板：win_len 秒对应点数
        tpl_len = int(win_len / sample_interval)
        tpl_f = np.array(template_f[:tpl_len]) if len(template_f) >= tpl_len else np.pad(
            template_f, (0, tpl_len - len(template_f)), 'constant')
        tpl_r = np.array(template_r[:tpl_len]) if len(template_r) >= tpl_len else np.pad(
            template_r, (0, tpl_len - len(template_r)), 'constant')

        # ---------- 2. 降采样到约 2 秒间隔，加速 FFT ----------
        ds_factor = max(1, int(2.0 / sample_interval))  # 2秒一个点
        signal_f_ds = signal_f[::ds_factor]
        signal_r_ds = signal_r[::ds_factor]
        tpl_f_ds = tpl_f[::ds_factor]
        tpl_r_ds = tpl_r[::ds_factor]

        # 确保降采样后模板长度至少为 3
        if len(tpl_f_ds) < 3:
            ds_factor = 1
            signal_f_ds, signal_r_ds = signal_f, signal_r
            tpl_f_ds, tpl_r_ds = tpl_f, tpl_r




        # ---------- 3. 在降采样信号上 FFT 互相关 ----------
        def fft_argmax_offset(signal, template):
            """返回归一化互相关最大的偏移量（0 <= offset <= len(signal)-len(template)）"""
            s = signal - np.mean(signal)
            t = template - np.mean(template)
            n = len(s)
            m = len(t)
            # FFT 互相关
            corr = np.fft.irfft(np.fft.rfft(s, n + m - 1) * np.conj(np.fft.rfft(t[::-1], n + m - 1)))
            corr = corr[:n - m + 1]  # 有效偏移
            # 归一化：除以能量
            # 注意：完整皮尔逊相关系数需窗口动态标准差，这里用全局近似
            # 为提高精度，我们用局部能量修正？简化：直接比较协方差，因为降采样后信号平稳
            # 实际效果足够找到大致位置
            best_offset = np.argmax(corr)
            return best_offset

        offset_f = fft_argmax_offset(signal_f_ds, tpl_f_ds)
        offset_r = fft_argmax_offset(signal_r_ds, tpl_r_ds)

        # 综合两个偏移（取平均或选更可信的，这里简单平均）
        best_offset_ds = int(round((offset_f + offset_r) / 2.0))

        # 映射回原始采样间隔的偏移
        best_offset_orig = best_offset_ds * ds_factor

        # ---------- 4. 在原采样下精确搜索（仅 ±5 个采样点）----------
        fine_window = 5  # 原采样点
        start_idx = max(0, best_offset_orig - fine_window)
        end_idx = min(len(signal_f) - tpl_len, best_offset_orig + fine_window)

        best_combined = -1.0
        best_shift = best_offset_orig
        for shift in range(start_idx, end_idx + 1):
            seg_f = signal_f[shift:shift + tpl_len]
            seg_r = signal_r[shift:shift + tpl_len]
            # 可能因边界造成长度不足
            if len(seg_f) < tpl_len or len(seg_r) < tpl_len:
                continue
            corr_f = np.corrcoef(seg_f, tpl_f)[0, 1] if (np.std(seg_f) > 1e-6 and np.std(tpl_f) > 1e-6) else 0
            corr_r = np.corrcoef(seg_r, tpl_r)[0, 1] if (np.std(seg_r) > 1e-6 and np.std(tpl_r) > 1e-6) else 0
            combined = (corr_f + corr_r) / 2.0
            if combined > best_combined:
                best_combined = combined
                best_shift = shift

        best_start_time = search_start + best_shift * sample_interval
        best_end_time = best_start_time + win_len

        if best_combined < 0.6:
            return rough_start, rough_end
        return best_start_time, best_end_time

    def _classify_hash_segment(self, query_hashes):
        """根据哈希窗口的相似度归类或创建模板，返回模板ID"""
        threshold = 0.75  # 窗口内帧汉明距离≤MAX_DIST的比例
        max_dist = CONFIG["OPED_HASH_MAX_DIST"]
        best_ratio = 0.0
        best_id = -1
        best_idx = -1

        for idx, tpl in enumerate(self.oped_templates):
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
            # 更新模板（平滑：保留原有哈希的70%，30%用新窗口）
            updated = []
            for q, t in zip(query_hashes, self.oped_templates[best_idx]['hash_sequence']):
                # 简单的按位更新？可以保留原模板不动，此处仅更新比率较高的帧
                updated.append(t)  # 暂时保持原模板不变，或根据汉明距离决定是否替换
            # 这里可简单替换为当前窗口（如果完全匹配则更新，否则保留）
            self.oped_templates[best_idx]['hash_sequence'] = updated
            return best_id
        else:
            new_id = len(self.oped_templates)
            color = self.oped_color_palette[self.oped_next_color_idx % len(self.oped_color_palette)]
            self.oped_next_color_idx += 1
            self.oped_templates.append({
                'id': new_id,
                'hash_sequence': query_hashes.copy(),
                'color': color,
                'wave_filtered': [],  # 保留兼容字段，但不再使用
                'wave_raw': []
            })
            return new_id

    def _get_hash_template(self, pending_id):
        """根据ID返回哈希序列模板"""
        for tpl in self.oped_templates:
            if tpl['id'] == pending_id:
                return tpl.get('hash_sequence', [])
        return None
    def _calc_cels_in_interval(self, start, end):
        """返回时间段 [start, end] 内过滤后张数的净增"""
        history = list(self.total_history)
        if not history:
            return 0
        start_cels = None
        end_cels = None
        for t, _, filtered, *_ in history:
            if t <= start:
                start_cels = filtered
            if t >= end and end_cels is None:
                end_cels = filtered
        if start_cels is None:
            start_cels = history[0][2]
        if end_cels is None:
            end_cels = history[-1][2]
        return max(0, end_cels - start_cels)




    def _save_record(self):
        import tkinter.messagebox as mb
        import tkinter.simpledialog as sd
        name = sd.askstring("保存记录", "输入记录名称：")
        if not name:
            return

        # 计算各种统计
        net_cels = self.total_cels_count - self.oped_deducted_cels
        net_time = self.elapsed_time - self.oped_deducted_time

        # 构建 OP/ED 片段明细
        oped_segments = []
        for start, end, sid in self.segment_occurrences:
            # 计算该片段的实际扣除张数（可从 total_history 精确计算）
            deducted_cels = self._calc_cels_in_interval(start, end)  # 需要实现
            oped_segments.append({
                "start": round(start, 2),
                "end": round(end, 2),
                "duration": round(end - start, 2),
                "segment_id": sid,
                "deducted_cels": deducted_cels,
                "deducted_time": round(end - start, 2)
            })


        instant_fps_values = []
        if hasattr(self, 'instant_fps_history') and self.instant_fps_history:
            instant_fps_values = [v for _, v in self.instant_fps_history]

        record = {
            "name": name,
            "record_time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "start_timestamp": self.start_real_time,
            "end_timestamp": time.time(),
            "duration_sec": round(self.elapsed_time, 2),
            "total_raw_cels": self.total_raw_cels_count,
            "total_filtered_cels": self.total_cels_count,
            "filtering": {
                "translation": self.total_translation_filtered,
                "optical_flow": self.total_optical_flow_filtered,
                "hash": self.total_hash_filtered,
                "still": self.total_still_filtered,
                "local_motion": self.total_local_filtered,
                "zoom": self.total_zoom_filtered,
                "other": self.total_other_unknown_filtered
            },
            "oped": {
                "total_deducted_cels": self.oped_deducted_cels,
                "total_deducted_time": round(self.oped_deducted_time, 2),
                "segments": oped_segments
            },
            "avg_fps_filtered": round(net_cels / net_time, 2) if net_time > 0 else 0,
            "avg_fps_raw": round(self.total_raw_cels_count / self.elapsed_time, 2) if self.elapsed_time > 0 else 0,
            "extra": {
                "max_instant_fps": round(max(instant_fps_values), 2) if instant_fps_values else 0,
                "min_instant_fps": round(min(instant_fps_values), 2) if instant_fps_values else 0,
                "median_fps": round(np.median(instant_fps_values), 2) if instant_fps_values else 0
            }
        }

        # 保存到文件
        settings_dir = os.path.dirname(self._get_settings_path())
        path = os.path.join(settings_dir, "cel_counter_records.json")
        try:
            with open(path, "r", encoding="utf-8") as f:
                records = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            records = []
        records.append(record)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(records, f, indent=2, ensure_ascii=False)
        mb.showinfo("保存成功", f"记录已保存至 {path}")


    def _apply_oped_deduction(self, start, end):
        """计算并扣除指定 OP/ED 区间的张数和时长"""
        # 去重检查
        with self.lock:
            for old_start, old_end in self.deducted_intervals:
                if abs(old_start - start) < 1.0 and abs(old_end - end) < 1.0:
                    return
            self.deducted_intervals.append((start, end))

        # 从 total_history 中提取区间内的累计张数增量
        history = list(self.total_history)
        if not history:
            return

        start_cels = None
        end_cels = None

        # 正向查找区间起始对应的累计张数
        for item in history:
            t, _, filtered_total, *_ = item
            if t <= start:
                start_cels = filtered_total

        # 如果找不到 start 之前的记录（区间开始太早），放弃本次扣除
        if start_cels is None:
            return

        # 反向查找区间结束对应的累计张数
        for item in reversed(history):
            t, _, filtered_total, *_ = item
            if t >= end:
                end_cels = filtered_total
            else:
                break

        # 如果 end 超过了历史记录范围，用最新记录的累计值代替
        if end_cels is None:
            end_cels = history[-1][2]

        cels_in_interval = end_cels - start_cels
        # 防止出现负数导致扣除量反向增加
        if cels_in_interval < 0:
            cels_in_interval = 0

        time_in_interval = end - start
        self.oped_deducted_cels += cels_in_interval
        self.oped_deducted_time += time_in_interval
    def _draw_settings_wave(self):
        """绘制设置界面中的总张数波形（过滤前后及各类过滤数量）"""
        if not hasattr(self, 'settings_wave_canvas') or self.settings_wave_canvas is None:
            return
        canvas = self.settings_wave_canvas
        if not canvas.winfo_exists():
            return

        history = list(self.total_history)
        width = canvas.winfo_width()
        height = canvas.winfo_height()
        canvas.delete("all")

        if width < 50 or height < 50 or not history:
            if hasattr(self, '_settings_win') and self._settings_win is not None and self._settings_win.winfo_exists():
                self.root.after(100, self._draw_settings_wave)
            return

        times = [p[0] for p in history]
        raw = [p[1] for p in history]
        filtered = [p[2] for p in history]
        trans = [p[3] for p in history]
        flow = [p[4] for p in history]
        hashes = [p[5] for p in history]
        still = [p[6] for p in history]
        local = [p[7] for p in history]
        other_unknown = [p[8] for p in history]
        zoom = [p[9] for p in history]

        min_t = times[0]
        max_t = times[-1]
        max_raw = max(raw) if raw else 0
        y_max = max_raw * 1.1 if max_raw > 0 else 10
        y_max = max(y_max, 10)

        margin = {'left': 50, 'right': 150, 'top': 20, 'bottom': 30}
        plot_w = width - margin['left'] - margin['right']
        plot_h = height - margin['top'] - margin['bottom']
        if plot_w <= 0 or plot_h <= 0:
            return

        accent = color_manager.get_color('accent')
        canvas.create_line(margin['left'], margin['top'],
                           margin['left'], margin['top'] + plot_h, fill=accent)
        canvas.create_line(margin['left'], margin['top'] + plot_h,
                           margin['left'] + plot_w, margin['top'] + plot_h, fill=accent)

        for y_val in [0, y_max]:
            y = margin['top'] + plot_h - (y_val / y_max) * plot_h
            canvas.create_line(margin['left'] - 3, y, margin['left'], y, fill=accent)
            label = "0" if y_val == 0 else f'{int(y_val)}'
            canvas.create_text(margin['left'] - 5, y, text=label, fill=accent,
                               anchor='e', font=('Arial', 8))

        t_range = max_t - min_t if max_t > min_t else 1
        for i in range(3):
            t = min_t + t_range * i / 2
            x = margin['left'] + ((t - min_t) / t_range) * plot_w
            canvas.create_line(x, margin['top'] + plot_h, x, margin['top'] + plot_h + 3, fill=accent)
            if t < 60:
                label = f'{int(t)}s'
            else:
                m, s = divmod(int(t), 60)
                label = f'{m}:{s:02d}'
            canvas.create_text(x, margin['top'] + plot_h + 10, text=label,
                               fill=accent, font=('Arial', 8), anchor='n')

        def to_points(values):
            pts = []
            for i, t in enumerate(times):
                x = margin['left'] + ((t - min_t) / t_range) * plot_w
                y = margin['top'] + plot_h - (values[i] / y_max) * plot_h
                pts.append((x, y))
            return pts

        line_defs = [
            (raw, color_manager.get_color('filter_raw_total'), '过滤前总张数'),
            (filtered, color_manager.get_color('filter_filtered_total'), '过滤后总张数'),
            (trans, color_manager.get_color('filter_translation'), '全局平移对齐过滤'),
            (flow, color_manager.get_color('filter_optical_flow'), '光流法过滤'),
            (hashes, color_manager.get_color('filter_hash'), '哈希重复帧过滤'),
            (still, color_manager.get_color('filter_still'), '静止过滤'),
            (local, color_manager.get_color('filter_local'), '局部变化过滤'),
            (other_unknown, color_manager.get_color('filter_other'), '其他'),
            (zoom, color_manager.get_color('filter_zoom'), '缩放过滤'),
        ]

        final_values = {}
        for data, color, name in line_defs:
            pts = to_points(data)
            if len(pts) >= 2:
                canvas.create_line(pts, fill=color, width=1)
            final_values[name] = data[-1] if data else 0

        right_x = margin['left'] + plot_w + 5
        y_step = 15
        start_y = margin['top'] + 5
        for i, (name, color) in enumerate([(name, color) for _, color, name in line_defs]):
            val = final_values[name]
            y_pos = start_y + i * y_step
            canvas.create_rectangle(right_x, y_pos, right_x + 10, y_pos + 10, fill=color, outline='')
            canvas.create_text(right_x + 15, y_pos + 5, text=f'{name}: {int(val)}', fill=color,
                               anchor='w', font=('Arial', 8))

        # 近5秒过滤类型占比
        now = max_t
        since = now - 5
        recent = [p for p in history if p[0] >= since]
        if recent:
            first = recent[0]
            last = recent[-1]
            inc = {
                '平移': last[3] - first[3],
                '光流': last[4] - first[4],
                '哈希': last[5] - first[5],
                '静止': last[6] - first[6],
                '局部': last[7] - first[7],
                '其他': last[8] - first[8],
                '缩放': last[9] - first[9]
            }
            total_inc = sum(inc.values())
            if total_inc > 0:
                inc_colors = {
                    '平移': color_manager.get_color('filter_translation'),
                    '光流': color_manager.get_color('filter_optical_flow'),
                    '哈希': color_manager.get_color('filter_hash'),
                    '静止': color_manager.get_color('filter_still'),
                    '局部': color_manager.get_color('filter_local'),
                    '其他': color_manager.get_color('filter_other'),
                    '缩放': color_manager.get_color('filter_zoom')
                }
                bar_w = 80
                bar_h = 40
                bar_x = margin['left'] + plot_w + bar_w - 35
                bar_y = margin['top'] + plot_h - bar_h + 2
                canvas.create_rectangle(bar_x - 1, bar_y - 1, bar_x + bar_w + 1, bar_y + bar_h + 1,
                                        outline=accent)
                cum_y = bar_y + bar_h
                for key in ['平移', '光流', '哈希', '静止', '局部', '其他', '缩放']:
                    val = inc[key]
                    if val <= 0:
                        continue
                    h = int(bar_h * val / total_inc)
                    if h < 1:
                        h = 1
                    canvas.create_rectangle(bar_x, cum_y - h, bar_x + bar_w, cum_y,
                                            fill=inc_colors[key], outline='')
                    cum_y -= h
                canvas.create_text(bar_x + bar_w // 2, bar_y - 8, text="近5秒过滤占比",
                                   fill=accent, font=('Arial', 7), anchor='s')

        if hasattr(self, '_settings_win') and self._settings_win is not None and self._settings_win.winfo_exists():
            refresh_ms = int(CONFIG["TOTAL_WAVE_REFRESH_SEC"] * 1000)
            self.root.after(refresh_ms, self._draw_settings_wave)

if __name__ == "__main__":
    AnimeCelCounter()