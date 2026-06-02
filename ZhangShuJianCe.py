<<<<<<< HEAD
import cv2
import numpy as np
import tkinter as tk
import time
import threading
import json
import copy
import os
import sys
from collections import deque


# 预览功能依赖 PIL
try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

try:
    import dxcam
    DX_AVAILABLE = True
except ImportError:
    DX_AVAILABLE = False

if not DX_AVAILABLE:
    raise ImportError("请安装 dxcam 库: pip install dxcam")

# ==================== 颜色管理器 ====================
class ColorManager:
    """统一管理全局调色板，支持主题切换与实时更新控件"""
    def __init__(self, config_colors):

        self._config = config_colors
        self._widget_color_map = {}
        self._widgets_to_update = []

    def get_color(self, key):
        return self._config.get(key, "#FFFFFF")

    def register_widget(self, widget, color_key):
        self._widget_color_map[id(widget)] = color_key
        if not hasattr(widget, '_color_key'):
            widget._color_key = color_key
        self._widgets_to_update.append(widget)

    def apply_theme(self):
        for widget in self._widgets_to_update:
            if not hasattr(widget, '_color_key'):
                continue
            key = widget._color_key
            try:
                bg = self._config.get('bg', '#000000')
                fg = self._config.get(key, '#FFA500')
                if isinstance(widget, (tk.Frame, tk.Canvas)):
                    widget.configure(bg=bg)
                elif isinstance(widget, (tk.Label, tk.Checkbutton)):
                    widget.configure(fg=fg, bg=bg)
                # 可根据需要扩展其他控件类型，如 Button 等
                elif isinstance(widget, tk.Button):
                    widget.configure(fg=fg, bg=self._config.get('btn_bg', '#222222'))
            except Exception:
                pass

    def clear(self):
        self._widget_color_map.clear()
        self._widgets_to_update.clear()

color_manager = None

# ==================== 配置参数（已移除所有缩放运镜相关常量） ====================
CONFIG = {

    "USE_CUSTOM_COLORS": True,
    # ========== 新增 ==========
    "SHOW_INFO_PANEL": True,  # 显示实时数据面板
    "SHOW_WAVE1": True,  # 显示波形1
    "SHOW_WAVE2": True,  # 显示波形2
    "REFRESH_INTERVAL": 0.4,           # UI 标签刷新间隔（秒）
    "SCALE_FACTOR": 0.5,               # 截图缩放比例
    "SSIM_THRESHOLD": 0.90,            # 背景相似度阈值（用于局部变化过滤）
    "WAVE_HISTORY_SEC": 60,            # 波形1历史时长（秒）
    "WAVE_REFRESH_MS": 200,            # 波形1刷新间隔（毫秒）
    "WAVE2_HISTORY_SEC": 1500,         # 波形2历史时长（秒）
    "WAVE2_REFRESH_MS": 3000,          # 波形2刷新间隔（毫秒）
    "JingzhiShiJian": 60,              # 静止自动暂停（秒）
    "WAVE_MAX_Y": 24,                  # 波形Y轴最大值（张/秒）
    "CROP_RATIO": 0.7,                 # 截取屏幕中心区域比例
    "CROP_REGION": None,               # 手动截图区域（left, top, right, bottom），None则自动计算
    "ALPHA": 1,                        # 窗口透明度（0.0~1.0）
    "DIFF_THRESHOLD": 22,              # 静态二值化阈值（备选），实际使用自适应阈值
    "MIN_DIFF_THRESHOLD": 5,           # 极暗场景下的最低阈值
    "MAX_DIFF_THRESHOLD": 30,          # 亮场景下的最高阈值
    "MIN_CHANGE_RATIO": 0.002,         # 最小变化像素占比（原始检测/基础检测均用）
    "SIGNIFICANT_CHANGE_RATIO": 0.01,  # 变化面积大于此值视为新作画（大面积直接通过）
    "ALIGNED_CHANGE_THRESHOLD": 0.012, # 全局平移对齐后剩余变化面积上限（低于此值视为纯平移运镜）
    # 光流法
    "FLOW_FEATURE_COUNT": 200,         # 光流法提取特征点最大数量
    "FLOW_STATIC_THRESH": 1,         # 位移小于此像素视为静止点（对应原图2像素）
    "FLOW_MEDIAN_SHIFT_MIN": 0.01,     # 主平移量至少大于此值才考虑图层分离
    "FLOW_LAYER_STATIC_THRESH": 2.0,   # 对齐后剩余移动的静止判断阈值（像素）
    "FLOW_LAYER_CONSISTENCY": 0.75,    # 方向一致性阈值（大于此值视为运镜）
    "SUBTITLE_BOTTOM_RATIO": 0.12,      # 底部屏蔽区域高度占比（用于特征提取），设为0则不屏蔽
    "FLOW_QUALITY_LEVEL": 0.03,        # 角点检测质量阈值，降低以增加点数
    "FLOW_MIN_DISTANCE": 12,           # 角点最小间距，减小以允许更密集
    "SUBTITLE_CONTRAST_FILTER": True,  # 是否启用底部文字对比度过滤（否则直接屏蔽整个底部）
    "SUBTITLE_GRADIENT_THRESH": 50,    # 底部文字梯度强度阈值（0-255）
    "SUBTITLE_DENSITY_THRESH": 1,      # 局部高对比度像素密度阈值（0-1）

    "FRAME_BUFFER_SIZE": 24,           # 哈希缓冲区大小（帧数）
    "HASH_THRESHOLD": 1,               # 汉明距离阈值，≤此值视为相同帧
    "TOTAL_WAVE_REFRESH_SEC": 2,       # 设置界面总张数波形刷新间隔（秒）
    "FILTER_TRIGGER_WINDOW_MS": 200,   # 动态过滤触发窗口（毫秒）
    "FILTER_TRIGGER_COUNT": 3,         # 窗口内基础检测新张数达到此值触发完整过滤
    "FULL_FILTER_HOLD_SEC": 5,         # 触发后保持完整过滤的秒数

    "BASIC_CORR_THRESHOLD": 0.995,     # 基础检测快速相似度阈值（高于此值且变化极小则判静止）
    "BASIC_MIN_RAW_RATIO_STILL": 0.003,# 基础检测极慢平移/静止的最小变化面积
    "BASIC_SIGNIFICANT_RATIO": 0.01,   # 基础检测大面积直接通过阈值

    "FULL_CORR_THRESHOLD": 0.985,      # 完整检测快速相似度阈值
    "FULL_STILL_RATIO": 0.01,          # 完整检测静止变化面积阈值

    "LOCAL_AREA_THRESH": 0.003,        # 局部运动判断：最大连通域面积占比下限
    "LOCAL_BBOX_RATIO_MAX": 0.8,       # 局部运动判断：变化点包围盒面积占比上限
    "LOCAL_ASPECT_RATIO_MAX": 8,       # 局部运动判断：最大连通域宽高比上限

    "LAYER_MIN_VALID_POINTS": 15,       # 光流图层分离：最小有效特征点数
    "LAYER_MIN_MOVING_POINTS": 10,      # 光流图层分离：最小移动点数
    "LAYER_DIRECTION_CONSISTENCY": 0.75,# 光流图层分离：方向一致性阈值（与FLOW_LAYER_CONSISTENCY一致）
    "LAYER_MEAN_VEC_MIN": 0.1,         # 光流图层分离：主方向向量最小模长
    "LAYER_COS_SIM_THRESH": 0.7,       # 光流图层分离：与主方向夹角余弦阈值

    "PREVIEW_DENSE_ALPHA": 0.6,        # 预览稠密光流叠加透明度
    "PREVIEW_DIFF_DECAY": 0.7,         # 预览差分残影衰减系数 越小残留越短
    "PREVIEW_MOTION_DECAY": 0.85,      # 预览运动拖影衰减系数
    "PREVIEW_MOTION_MAX_SPEED": 50,    # 预览运动速度映射最大值（像素）
    "COLORS": {
        "accent": "#FFA500",
        "bg": "#000000",
        "secondary": "#778899",
        "btn_bg": "#222222",
        "title_bg": "#111111",
        "canvas_bg": "#000000",
        "wave_line_filtered": "#FFA500",
        "wave_line_raw": "#778899",
        "filter_translation": "#FF4444",
        "filter_optical_flow": "#4488FF",
        "filter_hash": "#CC44CC",
        "filter_still": "#FF8C00",
        "filter_local": "#00FFFF",
        "filter_other": "#888888",
        "filter_raw_total": "#FFA500",
        "filter_filtered_total": "#00CC66"
    },
    "USE_CUSTOM_COLORS": True,
}
CONFIG_DEFAULT = copy.deepcopy(CONFIG)
color_manager = ColorManager(CONFIG["COLORS"])


# ==================== 可拖动面板类 ====================
class DraggablePanel:
    def __init__(self, parent, title, width=300, height=200, min_width=150, min_height=100):
        self.parent = parent
        self.min_width = min_width
        self.min_height = min_height
        self.locked = False
        self._drag_data = {"x": 0, "y": 0, "mode": None}

        bg = color_manager.get_color('bg')
        accent = color_manager.get_color('accent')
        title_bg = color_manager.get_color('title_bg')

        self.frame = tk.Frame(parent, bg=bg, bd=2, relief=tk.RAISED)
        self.frame.place(x=50, y=50, width=width, height=height)

        self.title_bar = tk.Frame(self.frame, bg=title_bg, height=25, cursor="fleur")
        self.title_bar.pack(fill=tk.X)
        self.title_label = tk.Label(self.title_bar, text=title, bg=title_bg, fg=accent,
                                    font=("Arial", 9, "bold"), cursor="fleur")
        self.title_label.pack(side=tk.LEFT, padx=5)

        self.title_bar.bind("<Button-1>", lambda e: self._check_lock(e, "move"))
        self.title_bar.bind("<B1-Motion>", self._on_drag)
        self.title_label.bind("<Button-1>", lambda e: self._check_lock(e, "move"))
        self.title_label.bind("<B1-Motion>", self._on_drag)

        self.content = tk.Frame(self.frame, bg=bg)
        self.content.pack(fill=tk.BOTH, expand=True)

        self._resize_handles = []
        handle_se = tk.Frame(self.frame, bg="#444444", width=10, height=10, cursor="size_nw_se")
        handle_se.place(relx=1.0, rely=1.0, anchor="se")
        handle_se.bind("<Button-1>", lambda e: self._check_lock(e, "resize_se"))
        handle_se.bind("<B1-Motion>", self._on_drag)
        self._resize_handles.append(handle_se)

        handle_e = tk.Frame(self.frame, bg="#333333", width=5, cursor="size_we")
        handle_e.place(relx=1.0, rely=0.0, anchor="ne", height=25)
        handle_e.bind("<Button-1>", lambda e: self._check_lock(e, "resize_e"))
        handle_e.bind("<B1-Motion>", self._on_drag)
        self._resize_handles.append(handle_e)

        handle_s = tk.Frame(self.frame, bg="#333333", height=5, cursor="size_ns")
        handle_s.place(relx=0.0, rely=1.0, anchor="sw", width=25)
        handle_s.bind("<Button-1>", lambda e: self._check_lock(e, "resize_s"))
        handle_s.bind("<B1-Motion>", self._on_drag)
        self._resize_handles.append(handle_s)

        self.frame.bind("<ButtonRelease-1>", self.end_drag)

        color_manager.register_widget(self.frame, 'bg')
        color_manager.register_widget(self.title_bar, 'title_bg')
        color_manager.register_widget(self.title_label, 'accent')
        color_manager.register_widget(self.content, 'bg')


    def _check_lock(self, event, mode):
        if self.locked:
            return
        self.start_drag(event, mode)

    def start_drag(self, event, mode):
        self._drag_data["x"] = event.x_root
        self._drag_data["y"] = event.y_root
        self._drag_data["mode"] = mode
        self._drag_data["init_geom"] = (
            self.frame.winfo_x(),
            self.frame.winfo_y(),
            self.frame.winfo_width(),
            self.frame.winfo_height()
        )

    def _on_drag(self, event):
        if self.locked:
            return
        mode = self._drag_data.get("mode")
        if not mode:
            return
        dx = event.x_root - self._drag_data["x"]
        dy = event.y_root - self._drag_data["y"]

        if mode == "move":
            new_x = self.frame.winfo_x() + dx
            new_y = self.frame.winfo_y() + dy
            self.frame.place(x=new_x, y=new_y)
            self._drag_data["x"] = event.x_root
            self._drag_data["y"] = event.y_root
        elif mode == "resize_se":
            orig = self._drag_data["init_geom"]
            new_w = max(self.min_width, orig[2] + dx)
            new_h = max(self.min_height, orig[3] + dy)
            self.frame.place(width=new_w, height=new_h)
        elif mode == "resize_e":
            orig = self._drag_data["init_geom"]
            new_w = max(self.min_width, orig[2] + dx)
            self.frame.place(width=new_w)
        elif mode == "resize_s":
            orig = self._drag_data["init_geom"]
            new_h = max(self.min_height, orig[3] + dy)
            self.frame.place(height=new_h)

    def end_drag(self, event):
        self._drag_data["mode"] = None

    def set_locked(self, locked):
        self.locked = locked
        if locked:
            self.title_bar.configure(cursor="")
            self.title_label.configure(cursor="")
        else:
            self.title_bar.configure(cursor="fleur")
            self.title_label.configure(cursor="fleur")

    def get_geometry(self):
        return (
            self.frame.winfo_x(),
            self.frame.winfo_y(),
            self.frame.winfo_width(),
            self.frame.winfo_height()
        )

    def apply_geometry(self, geom):
        x, y, w, h = geom
        parent_w = self.parent.winfo_width()
        parent_h = self.parent.winfo_height()
        if parent_w > 10 and parent_h > 10:
            min_visible = 50
            if x + w < min_visible:
                x = min_visible - w
            if x > parent_w - min_visible:
                x = parent_w - min_visible
            if y + h < min_visible:
                y = min_visible - h
            if y > parent_h - min_visible:
                y = parent_h - min_visible
        self.frame.place(x=x, y=y, width=max(self.min_width, w), height=max(self.min_height, h))

# ==================== 主检测类 ====================
class AnimeCelCounter:
    def __init__(self):
        if getattr(sys, 'frozen', False):
            base_dir = sys._MEIPASS
        else:
            base_dir = os.path.dirname(os.path.abspath(__file__))
        self.readme_path = os.path.join(base_dir, "README.txt")

        self._settings_win = None
        self.lock = threading.Lock()
        self._stop_event = threading.Event()
        self._run_event = threading.Event()
        self._run_event.set()



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



        self._preview_pending = False
        self._latest_preview_img = None


        self._prev_cels_count = -1
        self._last_disp_cels = -1
        self.last_move_type = "静止"

        self.total_history = deque(maxlen=6000)
        self._last_recorded_time = -1

        self.full_filter_active = False
        self.full_filter_active_until = 0
        self.raw_detection_timestamps = deque()

        self.total_translation_filtered = 0
        self.total_optical_flow_filtered = 0
        self.total_hash_filtered = 0
        self.total_still_filtered = 0
        self.total_local_filtered = 0
        self.total_other_unknown_filtered = 0

        self.preview_active = False
        self.preview_thread = None
        self.preview_lock = threading.Lock()
        self.preview_cache = (None, None)
        self.preview_update_interval = 0.05
        self._preview_label = None
        self._diff_decay = None
        self._motion_history = None
        self._preview_control_lock = threading.Lock()
        self._preview_after_id = None

        self.load_settings()

        self.frame_buffer = deque(maxlen=CONFIG["FRAME_BUFFER_SIZE"])
        self.hash_threshold = CONFIG["HASH_THRESHOLD"]

        max_wave_points = int(CONFIG["WAVE_HISTORY_SEC"] * (1000 / CONFIG["WAVE_REFRESH_MS"]))
        self.wave_data = deque(maxlen=max_wave_points)
        self.wave_raw_data = deque(maxlen=max_wave_points)

        max_wave2_points = int(CONFIG["WAVE2_HISTORY_SEC"] * (1000 / CONFIG["WAVE2_REFRESH_MS"]))
        self.wave2_data = deque(maxlen=max_wave2_points)
        self.wave2_raw_data = deque(maxlen=max_wave2_points)

        self.smooth_cels = 0.0
        self.smooth_raw_cels = 0.0

        self.camera = dxcam.create(output_color="BGR")
        self.camera.start(target_fps=0, video_mode=False)  # 0 = 不限速，全速捕获

        monitor_sample = self.camera.get_latest_frame()
        if monitor_sample is not None:
            h, w = monitor_sample.shape[:2]
            screen_w = w
            screen_h = h
            if CONFIG.get("CROP_REGION"):
                base_rect = CONFIG["CROP_REGION"]
            else:
                base_rect = (0, 0, screen_w, screen_h)
            self.region = self._compute_region_from_base(base_rect, CONFIG["CROP_RATIO"], screen_w, screen_h)
            keep_h = self.region[3] - self.region[1]
            keep_w = self.region[2] - self.region[0]
            self.frame_shape = (int(keep_h * CONFIG["SCALE_FACTOR"]),
                                int(keep_w * CONFIG["SCALE_FACTOR"]))
        else:
            self.region = (384, 216, 1536, 864)
            self.frame_shape = (100, 100)

        self._build_ui()

        self.capture_thread = threading.Thread(target=self._capture_loop, daemon=True)
        self.capture_thread.start()

        self.root.after(30, self.loop)
        self.root.after(CONFIG["WAVE_REFRESH_MS"], self.update_wave)
        self.root.after(CONFIG["WAVE2_REFRESH_MS"], self.update_wave2)
        self.root.mainloop()

    def _compute_region_from_base(self, base_rect, ratio, screen_w, screen_h):
        """base_rect: (left, top, right, bottom), ratio: 缩放系数，返回 (l, t, r, b)"""
        left, top, right, bottom = base_rect
        # 计算中心
        cx = (left + right) / 2
        cy = (top + bottom) / 2
        # 新宽高
        orig_w = right - left
        orig_h = bottom - top
        new_w = orig_w * ratio
        new_h = orig_h * ratio
        # 新边界
        new_left = cx - new_w / 2
        new_top = cy - new_h / 2
        new_right = cx + new_w / 2
        new_bottom = cy + new_h / 2
        # 限制在屏幕内
        new_left = max(0, new_left)
        new_top = max(0, new_top)
        new_right = min(screen_w, new_right)
        new_bottom = min(screen_h, new_bottom)
        return (int(new_left), int(new_top), int(new_right), int(new_bottom))
    def _compute_hash(self, gray_img):
        resized = cv2.resize(gray_img, (16, 16), interpolation=cv2.INTER_AREA)
        avg = resized.mean()
        return (resized > avg).flatten()

    def _get_settings_path(self):
        if getattr(sys, 'frozen', False):
            return os.path.join(os.path.expanduser("~"), "cel_counter_settings.json")
        return "cel_counter_settings.json"

    def load_settings(self):
        try:
            settings_path = self._get_settings_path()
            with open(settings_path, "r") as f:
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

            # 同步全局颜色管理器，使加载的颜色立即生效
            color_manager._config = CONFIG["COLORS"]
        except (FileNotFoundError, json.JSONDecodeError):
            self.use_optical_flow = True
            self.use_hash_filter = False
            self.layouts = {}
            self.active_layout = None

    def _build_ui(self):
        self.root = tk.Tk()
        self.root.title("动画作画张数")
        self.root.geometry("1200x420")
        self.root.attributes("-topmost", True)
        TRANSPARENT = "#000000"
        self.root.attributes("-transparentcolor", TRANSPARENT)
        self.root.configure(bg=TRANSPARENT)
        self.root.overrideredirect(True)

        main_frame = tk.Frame(self.root, bg=TRANSPARENT)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        info_frame = tk.Frame(main_frame, bg=TRANSPARENT, width=130, height=200)
        info_frame.grid(row=0, column=0, sticky="nw", padx=(0, 10), pady=(10, 0))
        info_frame.pack_propagate(False)

        accent = color_manager.get_color('accent')
        self.lb_rt = tk.Label(info_frame, text="实时张数：0.0 ", fg=accent, bg=TRANSPARENT,
                              font=("Arial", 10, "bold"), anchor="w")
        self.lb_rt.pack(anchor="w", pady=3)

        self.lb_total_time = tk.Label(info_frame, text="运行时长：0.0 s", fg=accent, bg=TRANSPARENT,
                                      font=("Arial", 10, "bold"), anchor="w")
        self.lb_total_time.pack(anchor="w", pady=3)
        self.lb_total_cels = tk.Label(info_frame, text="总张数：0", fg=accent, bg=TRANSPARENT,
                                      font=("Arial", 10, "bold"), anchor="w")
        self.lb_total_cels.pack(anchor="w", pady=3)

        self.lb_avg = tk.Label(info_frame, text="总平均：0.0", fg=accent, bg=TRANSPARENT,
                               font=("Arial", 10, "bold"), anchor="w")
        self.lb_avg.pack(anchor="w", pady=3)
        self.lb_st = tk.Label(info_frame, text="静止/纯运镜", fg=accent, bg=TRANSPARENT,
                              font=("Arial", 10, "bold"), anchor="w")
        self.lb_st.pack(anchor="w", pady=5)

        self.wave_frame = tk.Frame(main_frame, bg=TRANSPARENT, width=300, height=80)
        self.wave_frame.grid(row=0, column=1, sticky="n", padx=(0, 10), pady=(10, 0))
        self.wave_frame.pack_propagate(False)
        self.canvas = tk.Canvas(self.wave_frame, bg=TRANSPARENT, highlightthickness=0, bd=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)

        self.wave2_frame = tk.Frame(main_frame, bg=TRANSPARENT, width=300, height=80)
        self.wave2_frame.grid(row=0, column=2, sticky="n", padx=(0, 10), pady=(10, 0))
        self.wave2_frame.pack_propagate(False)
        self.canvas2 = tk.Canvas(self.wave2_frame, bg=TRANSPARENT, highlightthickness=0, bd=0)
        self.canvas2.pack(fill=tk.BOTH, expand=True)

        btn_canvas = tk.Canvas(main_frame, bg=TRANSPARENT, highlightthickness=0, bd=0,
                               width=120, height=150)
        btn_canvas.grid(row=0, column=3, sticky="n", padx=(0, 10), pady=(10, 0))
        self.btn_canvas = btn_canvas
        self._draw_buttons()

        self.wave_margin_left = 30
        self.wave_margin_right = 10
        self.wave_margin_top = 10
        self.wave_margin_bottom = 10

        self.make_draggable()

    def _draw_buttons(self):
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
            canvas.tag_bind(rect_id, "<Button-1>", lambda e, c=cmd: c())
            canvas.tag_bind(text_id, "<Button-1>", lambda e, c=cmd: c())
            canvas.tag_bind(rect_id, "<Enter>",
                            lambda e, r=rect_id: canvas.itemconfig(r, fill="#111111"))
            canvas.tag_bind(rect_id, "<Leave>",
                            lambda e, r=rect_id: canvas.itemconfig(r, fill=""))
            canvas.tag_bind(text_id, "<Enter>",
                            lambda e, r=rect_id: canvas.itemconfig(r, fill="#A12F2F"))
            canvas.tag_bind(text_id, "<Leave>",
                            lambda e, r=rect_id: canvas.itemconfig(r, fill=""))

    # ============ 布局保存/加载 ============
    def _save_layout(self, win, panels):
        try:
            layout = {
                'window_geometry': win.winfo_geometry(),
                'panels': {
                    name: panel.get_geometry()
                    for name, panel in panels.items()
                }
            }
            settings_path = self._get_settings_path()
            try:
                with open(settings_path, "r") as f:
                    data = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError):
                data = {}
            data['layout'] = layout
            with open(settings_path, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"保存布局失败: {e}")

    def _apply_layout(self, win, panels):
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



    # ============ 设置窗口 ============
    def open_settings(self):
        if self._settings_win is not None and self._settings_win.winfo_exists():
            self._settings_win.lift()
            return

        win = tk.Toplevel(self.root)
        self._settings_win = win
        win.title("参数设置")
        win.overrideredirect(True)
        win.attributes("-alpha", CONFIG["ALPHA"])
        win.configure(bg=color_manager.get_color('bg'))
        win.geometry("1200x760")

        def on_close():
            if CONFIG["USE_CUSTOM_COLORS"]:
                for key, (var, _) in self.color_vars.items():
                    CONFIG["COLORS"][key] = var.get()
            current_layout = {
                'window_geometry': win.winfo_geometry(),
                'panels': {name: panel.get_geometry() for name, panel in panels.items()}
            }
            if self.active_layout:
                self.layouts[self.active_layout] = current_layout
            else:
                self.layouts["默认布局"] = current_layout
                self.active_layout = "默认布局"
            self._save_all_settings()
            if hasattr(self, 'settings_canvas'):
                self.settings_canvas.unbind_all("<MouseWheel>")
            self._settings_win = None
            self.settings_wave_canvas = None
            self._preview_label = None
            self._stop_preview()
            if hasattr(self, '_crop_window') and self._crop_window:
                self._crop_window.destroy()
            win.destroy()

        accent = color_manager.get_color('accent')
        bg = color_manager.get_color('bg')
        title_bg = color_manager.get_color('title_bg')
        btn_bg = color_manager.get_color('btn_bg')

        title_bar = tk.Frame(win, bg=title_bg, height=30, cursor="fleur")
        title_bar.pack(fill=tk.X)
        title_label = tk.Label(title_bar, text="参数设置", bg=title_bg, fg=accent,
                               font=("Arial", 10, "bold"))
        title_label.pack(side=tk.LEFT, padx=10)

        pin_var = tk.BooleanVar(value=True)
        def toggle_pin():
            pin_var.set(not pin_var.get())
            win.attributes("-topmost", pin_var.get())
            pin_btn.config(text="📌" if pin_var.get() else "📍")

        close_btn = tk.Button(title_bar, text="✕", bg=btn_bg, fg=accent,
                              font=("Arial", 10, "bold"), command=on_close,
                              bd=0, activebackground="#AA0000", width=3)
        close_btn.pack(side=tk.RIGHT, padx=5)
        pin_btn = tk.Button(title_bar, text="📍", bg=btn_bg, fg=accent,
                            font=("Arial", 8), command=toggle_pin, bd=0,
                            activebackground="#444444", width=3)
        pin_btn.pack(side=tk.RIGHT, padx=5)

        def start_move(event):
            win._drag_start_x = event.x_root
            win._drag_start_y = event.y_root
            win._drag_start_win_x = win.winfo_x()
            win._drag_start_win_y = win.winfo_y()

        def do_move(event):
            dx = event.x_root - win._drag_start_x
            dy = event.y_root - win._drag_start_y
            new_x = win._drag_start_win_x + dx
            new_y = win._drag_start_win_y + dy
            win.geometry(f"+{new_x}+{new_y}")

        title_bar.bind("<Button-1>", start_move)
        title_bar.bind("<B1-Motion>", do_move)
        title_label.bind("<Button-1>", start_move)
        title_label.bind("<B1-Motion>", do_move)

        main_frame = tk.Frame(win, bg=bg)
        main_frame.pack(fill=tk.BOTH, expand=True)

        resize_handle = tk.Frame(win, bg="#444444", width=12, height=12, cursor="size_nw_se")
        resize_handle.place(relx=1.0, rely=1.0, anchor="se")

        def start_resize(event):
            win._resize_start_x = event.x_root
            win._resize_start_y = event.y_root
            win._resize_start_w = win.winfo_width()
            win._resize_start_h = win.winfo_height()

        def do_resize(event):
            dx = event.x_root - win._resize_start_x
            dy = event.y_root - win._resize_start_y
            new_w = max(400, win._resize_start_w + dx)
            new_h = max(300, win._resize_start_h + dy)
            win.geometry(f"{new_w}x{new_h}")

        resize_handle.bind("<Button-1>", start_resize)
        resize_handle.bind("<B1-Motion>", do_resize)

        panels = {}

        param_panel = DraggablePanel(main_frame, "参数设置", 400, 650)
        panels['params'] = param_panel
        # 只调用一次 _build_params_content，并保存返回值
        self.param_entries = self._build_params_content(param_panel.content, win, panels)

        wave_panel = DraggablePanel(main_frame, "总张数波形", 600, 400)
        panels['wave'] = wave_panel
        self.settings_wave_canvas = tk.Canvas(wave_panel.content, bg=bg, highlightthickness=0)
        self.settings_wave_canvas.pack(fill=tk.BOTH, expand=True)

        preview_panel = DraggablePanel(main_frame, "实时预览", 600, 400)
        panels['preview'] = preview_panel
        control_bar = tk.Frame(preview_panel.content, bg=bg)
        control_bar.pack(fill="x", pady=(0, 5))

        self.preview_toggle_var = tk.BooleanVar(value=False)
        tk.Checkbutton(control_bar, text="开启实时预览", variable=self.preview_toggle_var,
                       fg=accent, bg=bg, selectcolor="#222222",
                       command=self._on_preview_toggle).pack(side="left", padx=3)

        self.show_dense_flow_var = tk.BooleanVar(value=True)
        tk.Checkbutton(control_bar, text="稠密光流", variable=self.show_dense_flow_var,
                       fg=accent, bg=bg, selectcolor="#222222").pack(side="left", padx=3)

        self.show_sparse_flow_var = tk.BooleanVar(value=True)
        tk.Checkbutton(control_bar, text="稀疏光流", variable=self.show_sparse_flow_var,
                       fg=accent, bg=bg, selectcolor="#222222").pack(side="left", padx=3)

        self.show_curr_var = tk.BooleanVar(value=True)
        tk.Checkbutton(control_bar, text="当前帧", variable=self.show_curr_var,
                       fg=accent, bg=bg, selectcolor="#222222").pack(side="left", padx=3)

        self.show_diff_var = tk.BooleanVar(value=True)
        tk.Checkbutton(control_bar, text="差分", variable=self.show_diff_var,
                       fg=accent, bg=bg, selectcolor="#222222").pack(side="left", padx=3)



        tk.Label(control_bar, text="刷新间隔(秒):", fg=accent, bg=bg).pack(side="left", padx=3)
        self.preview_interval_var = tk.StringVar(value=str(self.preview_update_interval))
        interval_entry = tk.Entry(control_bar, textvariable=self.preview_interval_var, width=4,
                                  bg=btn_bg, fg=accent, insertbackground=accent)
        interval_entry.pack(side="left")
        interval_entry.bind("<Return>", lambda e: self._update_preview_interval())
        tk.Button(control_bar, text="应用", command=self._update_preview_interval,
                  bg=btn_bg, fg=accent, activebackground="#444444").pack(side="left", padx=2)
        self._preview_label = tk.Label(preview_panel.content, bg=bg)
        self._preview_label.pack(fill=tk.BOTH, expand=True)

        self.lock_panels_var = tk.BooleanVar(value=True)
        def toggle_lock():
            locked = self.lock_panels_var.get()
            for p in panels.values():
                p.set_locked(locked)

        lock_check = tk.Checkbutton(title_bar, text="锁定面板", variable=self.lock_panels_var,
                                    command=toggle_lock, fg=accent, bg=title_bg,
                                    selectcolor=title_bg)
        lock_check.pack(side=tk.RIGHT, padx=10)
        toggle_lock()
        # 强制完成布局，确保内部控件获得正确尺寸
        win.update_idletasks()

        # 启动预览（此时 Label 尺寸已就绪）
        self.preview_toggle_var.set(True)
        if not self.preview_active:
            self._start_preview()
        # 加载布局并绘制波形（使用短延迟确保画布尺寸有效）
        self._apply_layout(win, panels)
        win.after(100, self._draw_settings_wave)   # 延迟以获取正确画布尺寸
        win.protocol("WM_DELETE_WINDOW", on_close)

    def _build_params_content(self, parent, win, panels):
        """构建参数设置面板（两列布局）"""
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

        param_names = {
            "REFRESH_INTERVAL": "UI刷新间隔",
            "ALPHA": "背景透明度",
            "SCALE_FACTOR": "截图缩放比例",
            "SSIM_THRESHOLD": "背景相似度阈值",
            "WAVE_HISTORY_SEC": "波形1时长(秒)",
            "WAVE_REFRESH_MS": "波形1刷新间隔(ms)",
            "WAVE2_HISTORY_SEC": "波形2时长(秒)",
            "WAVE2_REFRESH_MS": "波形2刷新间隔(ms)",
            "JingzhiShiJian": "静止自动暂停(秒)",
            "WAVE_MAX_Y": "波形Y轴最大值",
            "CROP_RATIO": "截图区域比例",
            "DIFF_THRESHOLD": "变化检测阈值",
            "MIN_DIFF_THRESHOLD": "最小变化阈值",
            "MAX_DIFF_THRESHOLD": "最大变化阈值",
            "MIN_CHANGE_RATIO": "最小变化占比",
            "ALIGNED_CHANGE_THRESHOLD": "平移对齐残差",
            "SIGNIFICANT_CHANGE_RATIO": "显著变化占比",
            "FLOW_FEATURE_COUNT": "光流特征点数",
            "FLOW_STATIC_THRESH": "静止点位移阈值",
            "FLOW_MEDIAN_SHIFT_MIN": "主平移最小阈值",
            "FRAME_BUFFER_SIZE": "哈希缓冲区大小",
            "HASH_THRESHOLD": "哈希距离阈值",
            "TOTAL_WAVE_REFRESH_SEC": "总张数波形刷新(秒)",
            "FILTER_TRIGGER_WINDOW_MS": "触发窗口(ms)",
            "FILTER_TRIGGER_COUNT": "触发张数阈值",
            "FULL_FILTER_HOLD_SEC": "完整过滤保持(秒)",
            "BASIC_CORR_THRESHOLD": "基础检测相似度阈值",
            "BASIC_MIN_RAW_RATIO_STILL": "基础静止变化面积",
            "BASIC_SIGNIFICANT_RATIO": "基础大面积阈值",
            "FULL_CORR_THRESHOLD": "完整检测相似度阈值",
            "FULL_STILL_RATIO": "完整静止变化面积",
            "LOCAL_AREA_THRESH": "局部面积占比下限",
            "LOCAL_BBOX_RATIO_MAX": "局部包围盒上限",
            "LOCAL_ASPECT_RATIO_MAX": "局部宽高比上限",
            "LAYER_MIN_VALID_POINTS": "光流最小有效点数",
            "LAYER_MIN_MOVING_POINTS": "光流最小移动点数",
            "LAYER_DIRECTION_CONSISTENCY": "光流方向一致性",
            "LAYER_MEAN_VEC_MIN": "光流主方向最小模长",
            "LAYER_COS_SIM_THRESH": "光流夹角阈值",
            "PREVIEW_DENSE_ALPHA": "预览稠密光流透明度",
            "PREVIEW_DIFF_DECAY": "预览差分衰减",
            "PREVIEW_MOTION_DECAY": "预览运动衰减",
            "PREVIEW_MOTION_MAX_SPEED": "预览最大速度",
        }
        entries = {}

        groups = [
            ("基本", ["REFRESH_INTERVAL", "SCALE_FACTOR", "SSIM_THRESHOLD", "JingzhiShiJian", "CROP_RATIO","ALPHA"]),
            ("波形1", ["WAVE_HISTORY_SEC", "WAVE_REFRESH_MS"]),
            ("波形2", ["WAVE2_HISTORY_SEC", "WAVE2_REFRESH_MS", "WAVE_MAX_Y"]),
            ("变化检测", ["DIFF_THRESHOLD", "MIN_DIFF_THRESHOLD", "MAX_DIFF_THRESHOLD", "MIN_CHANGE_RATIO",
                          "ALIGNED_CHANGE_THRESHOLD", "SIGNIFICANT_CHANGE_RATIO"]),
            ("光流法", ["FLOW_FEATURE_COUNT", "FLOW_STATIC_THRESH", "FLOW_MEDIAN_SHIFT_MIN"]),
            ("重复帧过滤", ["FRAME_BUFFER_SIZE", "HASH_THRESHOLD"]),
            ("总张数波形", ["TOTAL_WAVE_REFRESH_SEC"]),
            ("动态过滤触发", ["FILTER_TRIGGER_WINDOW_MS", "FILTER_TRIGGER_COUNT", "FULL_FILTER_HOLD_SEC"]),
            ("基础检测常量", ["BASIC_CORR_THRESHOLD", "BASIC_MIN_RAW_RATIO_STILL", "BASIC_SIGNIFICANT_RATIO"]),
            ("完整检测常量", ["FULL_CORR_THRESHOLD", "FULL_STILL_RATIO"]),
            ("局部运动判断", ["LOCAL_AREA_THRESH", "LOCAL_BBOX_RATIO_MAX", "LOCAL_ASPECT_RATIO_MAX"]),
            ("光流图层分离", ["LAYER_MIN_VALID_POINTS", "LAYER_MIN_MOVING_POINTS",
                              "LAYER_DIRECTION_CONSISTENCY", "LAYER_MEAN_VEC_MIN", "LAYER_COS_SIM_THRESH"]),
            ("预览参数", ["PREVIEW_DENSE_ALPHA", "PREVIEW_DIFF_DECAY", "PREVIEW_MOTION_DECAY", "PREVIEW_MOTION_MAX_SPEED"]),
        ]
        # 找到 CROP_RATIO 的 Label，改为可动态更新的 textvariable
        self.crop_ratio_label_var = tk.StringVar()
        if CONFIG.get("CROP_REGION"):
            self.crop_ratio_label_var.set("手动区域缩放比例:")
        else:
            self.crop_ratio_label_var.set("截图区域比例:")

        entries = {}
        left_frame = tk.Frame(scroll_frame, bg=bg)
        right_frame = tk.Frame(scroll_frame, bg=bg)
        left_frame.grid(row=0, column=0, sticky="nw", padx=(0, 10))
        right_frame.grid(row=0, column=1, sticky="nw")

        for idx, (group_name, keys) in enumerate(groups):
            target = left_frame if idx % 2 == 0 else right_frame
            row = target.grid_size()[1]
            tk.Label(target, text=group_name, font=("Arial", 10, "bold"),
                     fg=accent, bg=bg).grid(row=row, column=0, columnspan=2, sticky="w", pady=(10, 2))
            row += 1
            for key in keys:
                if key == "CROP_RATIO":
                    # 动态标签
                    tk.Label(target, textvariable=self.crop_ratio_label_var, fg=accent, bg=bg,
                             font=("Arial", 9)).grid(row=row, column=0, sticky="w", padx=5)
                else:
                    label_text = param_names.get(key, key) + ":"
                    tk.Label(target, text=label_text, fg=accent, bg=bg,
                             font=("Arial", 9)).grid(row=row, column=0, sticky="w", padx=5)
                var = tk.StringVar(value=str(CONFIG[key]))
                ent = tk.Entry(target, textvariable=var, width=10,
                               font=("Arial", 9), bg=btn_bg, fg=accent,
                               insertbackground=accent)
                ent.grid(row=row, column=1, sticky="w")
                entries[key] = var
                row += 1

        row_left = left_frame.grid_size()[1]
        tk.Label(left_frame, text="功能开关", font=("Arial", 10, "bold"),
                 fg=accent, bg=bg).grid(row=row_left, column=0, columnspan=2, sticky="w", pady=(10, 2))
        row_left += 1
        flow_var = tk.BooleanVar(value=self.use_optical_flow)
        tk.Checkbutton(left_frame, text="启用光流法图层分离", variable=flow_var,
                       fg=accent, bg=bg, selectcolor=btn_bg).grid(row=row_left, column=0, columnspan=2, sticky="w", padx=5)
        row_left += 1
        hash_var = tk.BooleanVar(value=self.use_hash_filter)
        tk.Checkbutton(left_frame, text="启用重复帧过滤", variable=hash_var,
                       fg=accent, bg=bg, selectcolor=btn_bg).grid(row=row_left, column=0, columnspan=2, sticky="w", padx=5)
        row_left += 1

        tk.Label(left_frame, text="全局调色板", font=("Arial", 10, "bold"),
                 fg=accent, bg=bg).grid(row=row_left, column=0, columnspan=2, sticky="w", pady=(10, 2))
        row_left += 1
        self.custom_color_var = tk.BooleanVar(value=CONFIG["USE_CUSTOM_COLORS"])

        def toggle_custom_colors():
            CONFIG["USE_CUSTOM_COLORS"] = self.custom_color_var.get()
            for child in color_frame.winfo_children():
                if isinstance(child, tk.Entry) or isinstance(child, tk.Button):
                    child.configure(state="normal" if CONFIG["USE_CUSTOM_COLORS"] else "disabled")
            color_manager.apply_theme()
            self._redraw_all()

        tk.Checkbutton(left_frame, text="启用自定义调色板", variable=self.custom_color_var,
                       command=toggle_custom_colors,
                       fg=accent, bg=bg, selectcolor=btn_bg).grid(row=row_left, column=0, columnspan=2, sticky="w", padx=5)
        row_left += 1

        color_frame = tk.Frame(left_frame, bg=bg)
        color_frame.grid(row=row_left, column=0, columnspan=2, sticky="we", padx=5)
        row_left += 1

        color_keys = [
            ("accent", "主色调"), ("bg", "背景"), ("secondary", "次要"),
            ("btn_bg", "按钮背景"), ("title_bg", "标题栏背景"), ("canvas_bg", "画布背景"),
            ("wave_line_filtered", "波形1过滤后"), ("wave_line_raw", "波形1过滤前"),
            ("filter_translation", "平移过滤"), ("filter_optical_flow", "光流过滤"),
            ("filter_hash", "哈希过滤"), ("filter_still", "静止过滤"),
            ("filter_local", "局部过滤"), ("filter_other", "其他过滤"),
            ("filter_raw_total", "过滤前总张数"), ("filter_filtered_total", "过滤后总张数")
        ]
        self.color_vars = {}
        row_color = 0
        for key, name in color_keys:
            tk.Label(color_frame, text=name+":", fg=accent, bg=bg).grid(row=row_color, column=0, sticky="w")
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

        btn_frame = tk.Frame(left_frame, bg=bg)
        btn_frame.grid(row=row_left, column=0, columnspan=2, pady=10)

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

        def save_settings():
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
                # 更新 frame_shape
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

        tk.Button(btn_frame, text="保存", command=save_settings,
                  bg=btn_bg, fg=accent).pack(side="left", padx=5)
        tk.Button(btn_frame, text="恢复默认", command=reset_to_default,
                  bg=btn_bg, fg=accent).pack(side="left", padx=5)
        tk.Button(btn_frame, text="说明", command=self.open_readme,
                  bg=btn_bg, fg=accent).pack(side="left", padx=5)
        tk.Button(btn_frame, text="显示截图范围", command=self.show_crop_region,
                  bg=btn_bg, fg=accent).pack(side="left", padx=5)

        def on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        canvas.bind_all("<MouseWheel>", on_mousewheel)

        return entries
    def _pick_color(self, key, var):
        from tkinter import colorchooser
        color = colorchooser.askcolor(color=var.get(), title=f"选择{key}颜色")
        if color[1]:
            var.set(color[1])
            self.color_vars[key][1].configure(bg=color[1])

    def _apply_custom_colors(self):
        if CONFIG["USE_CUSTOM_COLORS"]:
            for key, (var, _) in self.color_vars.items():
                CONFIG["COLORS"][key] = var.get()
        color_manager._config = CONFIG["COLORS"]
        color_manager.apply_theme()
        self._redraw_all()

    def _redraw_all(self):
        self.draw_wave()
        self.draw_wave2()
        if hasattr(self, 'settings_wave_canvas') and self.settings_wave_canvas:
            self._draw_settings_wave()
        self._draw_buttons()

    # ============ 预览相关 ============
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

            # 应用字幕掩膜
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

    # ---------- 屏幕捕获 ----------
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

    def _is_raw_change(self, prev, curr):
        diff = cv2.absdiff(prev, curr)
        thresh = self._adaptive_threshold(curr)
        _, mask = cv2.threshold(diff, thresh, 255, cv2.THRESH_BINARY)

        # 应用字幕掩膜，排除底部字幕区域
        if CONFIG["SUBTITLE_BOTTOM_RATIO"] > 0:
            sub_mask = self._get_bottom_subtitle_mask(curr)
            mask = cv2.bitwise_and(mask, sub_mask)  # 字幕区置0

        kernel = np.ones((3, 3), np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
        ratio = np.count_nonzero(mask) / mask.size
        return ratio >= CONFIG["MIN_CHANGE_RATIO"]

    def _get_bottom_subtitle_mask(self, curr_gray):
        """
        返回一个与 curr_gray 同尺寸的 uint8 掩膜：
        底部字幕区域为 0，其余区域为 255。
        若 SUBTITLE_BOTTOM_RATIO == 0，则返回全 255。
        """
        h, w = curr_gray.shape
        subtitle_ratio = CONFIG.get("SUBTITLE_BOTTOM_RATIO", 0)
        if subtitle_ratio <= 0:
            return np.full((h, w), 255, dtype=np.uint8)

        mask = np.ones((h, w), dtype=np.uint8) * 255
        bottom_cut = int(h * (1 - subtitle_ratio))

        if CONFIG.get("SUBTITLE_CONTRAST_FILTER", True):
            # 使用基于对比度的智能字幕掩膜
            sub_mask = self._get_subtitle_mask(curr_gray, subtitle_ratio)  # 返回 0/255 掩膜
            # sub_mask 中底部非字幕区域为 255，字幕区域为 0
            mask[bottom_cut:, :] = sub_mask[bottom_cut:, :]  # 保留底部区域处理
        else:
            # 直接屏蔽整个底部区域
            mask[bottom_cut:, :] = 0
        return mask
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
                self.current_raw_cels = cels_raw



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

    def _basic_is_new_cel(self, prev, curr):
        corr = cv2.matchTemplate(prev, curr, cv2.TM_CCOEFF_NORMED)[0][0]
        # 获取统一字幕掩膜（基于 curr 生成一次）
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

        # 相关度低，完整差分
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

        # 对齐后差分
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

        # 局部运动判断（不再包含缩放运镜分支）
        if change_ratio >= CONFIG["ALIGNED_CHANGE_THRESHOLD"]:
            if self._has_local_motion(mask):
                self.last_move_type = "新作画"
                return True

            # 光流图层分离的特征提取 mask 进一步叠加字幕屏蔽
            feature_mask = mask.copy()
            if sub_mask is not None:
                feature_mask = cv2.bitwise_and(feature_mask, sub_mask)
            if self.use_optical_flow and self._is_layer_camera_move_v2(prev, curr_aligned, feature_mask):
                self.last_move_type = "图层分离运镜"
                return False

            if change_ratio >= CONFIG["SIGNIFICANT_CHANGE_RATIO"]:
                self.last_move_type = "新作画"
                return True

        # ---- 剩余情况：使用 SSIM 判断是否为局部变化 ----
        if change_ratio < CONFIG["SIGNIFICANT_CHANGE_RATIO"]:
            mask_inv = cv2.bitwise_not(mask)
            score = cv2.matchTemplate(prev, curr_aligned, cv2.TM_CCOEFF_NORMED, mask=mask_inv)[0][0]
            if score >= CONFIG["SSIM_THRESHOLD"]:
                self.last_move_type = "局部变化(过滤)"
                return False

        self.last_move_type = "新作画"
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

    # ---------- UI 更新 ----------
    def get_status(self, v):
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
                return base + "一拍一"
            else:
                return base + "高频作画"

    def loop(self):
        if self.is_running:
            now_real = time.time()
            self.elapsed_time += now_real - self.last_loop_time
            self.last_loop_time = now_real

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
                self.total_history.append((self.elapsed_time, total_r, total_f,
                                           trans_f, flow_f, hash_f,
                                           still_f, local_f, other_f))
                self._last_recorded_time = self.elapsed_time

            with self.lock:
                current_total = self.total_cels_count
                has_frame = self._has_first_frame
            if has_frame:
                if current_total != self._prev_cels_count:
                    self.idle_start_time = None
                elif self.idle_start_time is None:
                    self.idle_start_time = self.elapsed_time
                elif self.elapsed_time - self.idle_start_time >= CONFIG["JingzhiShiJian"]:
                    self.pause()
            self._prev_cels_count = current_total

            if current_total != self._last_disp_cels:
                self.lb_total_cels.config(text=f"总张数：{current_total}")
                self._last_disp_cels = current_total

            if now_real - self.last_refresh >= CONFIG["REFRESH_INTERVAL"]:
                with self.lock:
                    real = self.current_cels
                self.total_sum += real
                self.total_count += 1
                avg = self.total_sum / self.total_count if self.total_count > 0 else 0.0

                accent = color_manager.get_color('accent')
                self.lb_rt.config(text=f"实时张数：{real:.1f} ", fg=accent)
                self.lb_total_time.config(text=f"运行时长：{self.elapsed_time:.1f} s", fg=accent)
                self.lb_total_cels.config(text=f"总张数：{current_total}", fg=accent)
                self.lb_avg.config(text=f"总平均：{avg:.1f}", fg=accent)
                self.lb_st.config(text=f"{self.get_status(real)}", fg=accent)
                self.last_refresh = now_real
                self._last_disp_cels = current_total

        self.root.after(20, self.loop)

    def pause(self):
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

        accent = color_manager.get_color('accent')
        self.lb_rt.config(text="实时张数：0.0 ")
        self.lb_total_time.config(text="运行时长：0.0 s")
        self.lb_total_cels.config(text="总张数：0")
        self.lb_avg.config(text="总平均：0.0")
        self.lb_st.config(text="静止/纯运镜")
        self.draw_wave()
        self.draw_wave2()

        if was_running:
            self._run_event.set()
            try:
                self.camera.release()
            except:
                pass
            self.camera = dxcam.create(output_color="BGR")

            self.camera.start(target_fps=0, video_mode=False)
    def _on_close(self):
        self._stop_event.set()
        self._run_event.set()
        self.root.destroy()

    # ---------- 波形绘制 ----------
    def update_wave(self):
        if self.is_running:
            with self.lock:
                cels = self.current_cels
                raw_cels = self.current_raw_cels
            self.wave_data.append((self.elapsed_time, cels))
            self.wave_raw_data.append((self.elapsed_time, raw_cels))
        self.draw_wave()
        self.root.after(CONFIG["WAVE_REFRESH_MS"], self.update_wave)

    def update_wave2(self):
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
        color_filtered = color_manager.get_color('wave_line_filtered')
        color_raw = color_manager.get_color('wave_line_raw')
        data_list = [
            (self.wave_data, color_filtered),
            (self.wave_raw_data, color_raw)
        ]
        self._draw_wave_multi(self.canvas, data_list,
                              CONFIG["WAVE_HISTORY_SEC"], CONFIG["WAVE_MAX_Y"], "60s")

    def draw_wave2(self):
        color_filtered = color_manager.get_color('wave_line_filtered')
        color_raw = color_manager.get_color('wave_line_raw')
        data_list = [
            (self.wave2_data, color_filtered),
            (self.wave2_raw_data, color_raw)
        ]
        self._draw_wave_multi(self.canvas2, data_list,
                              CONFIG["WAVE2_HISTORY_SEC"], CONFIG["WAVE_MAX_Y"], "25min")

    def _draw_wave_multi(self, canvas, data_list, history_sec, y_max, title=""):
        canvas.delete("all")
        width = canvas.winfo_width()
        height = canvas.winfo_height()
        if width < 50 or height < 50:
            # 尺寸不足时，用短间隔重试，而非等待整个刷新周期
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
        canvas.create_line(self.wave_margin_left, self.wave_margin_top,
                           self.wave_margin_left, self.wave_margin_top + plot_h, fill=accent)
        for y_val in [0, 6, 12, 18, 24]:
            if y_val > y_max:
                continue
            y = self.wave_margin_top + plot_h - (y_val / y_max) * plot_h
            canvas.create_line(self.wave_margin_left - 3, y, self.wave_margin_left, y, fill=accent)
            canvas.create_text(self.wave_margin_left - 8, y, text=str(y_val), fill=accent,
                               font=("Arial", 8), anchor="e")

    def make_draggable(self):
        def start(e):
            self.drag_x = e.x
            self.drag_y = e.y
        def move(e):
            dx = e.x - self.drag_x
            dy = e.y - self.drag_y
            self.root.geometry(f"+{self.root.winfo_x() + dx}+{self.root.winfo_y() + dy}")
        self.root.bind("<Button-1>", start)
        self.root.bind("<B1-Motion>", move)

    def open_readme(self):
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

    # ==========截图范围调整窗口 ==========
    def show_crop_region(self):
        """
        打开全屏截图范围设置窗口。
        功能：
        - 红框表示当前截图区域，可整体拖移。
        - 红框外侧的白色小方块为调整大小的控制手柄，拖拽可改变截图区域尺寸。
        - 点击红框外部或按 ESC 键关闭窗口，并将当前区域保存到配置中。
        """
        # 如果已经存在窗口，先关闭再重新打开（这里实际是关闭后立刻返回，即切换关闭效果）
        if hasattr(self, '_crop_window') and self._crop_window is not None:
            try:
                self._crop_window.destroy()
            except:
                pass
            self._crop_window = None
            return  # 注意：原逻辑是关闭已存在窗口后 return，即点击按钮是开关效果

        # 获取主屏幕分辨率
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        # 当前截图区域 (left, top, right, bottom)
        left, top, right, bottom = self.region

        # 创建全屏无边框窗口
        win = tk.Toplevel(self.root)
        self._crop_window = win
        win.attributes('-fullscreen', True)
        win.attributes('-topmost', True)
        # 将黑色设置为透明色，使背景透明（仅对纯黑像素有效）
        win.attributes('-transparentcolor', 'black')
        win.configure(bg='black')
        win.overrideredirect(True)  # 移除窗口装饰

        # Canvas 用于绘制红框和控制手柄
        canvas = tk.Canvas(win, bg='black', highlightthickness=0, bd=0)
        canvas.pack(fill='both', expand=True)

        # ==================== 可调整的样式参数 ====================
        RECT_OUTLINE_COLOR = 'red'  # 截图框轮廓颜色
        RECT_WIDTH = 3  # 轮廓线宽度（像素）
        HANDLE_SIZE = 8  # 控制手柄半边长，实际方块边长为 2 * HANDLE_SIZE
        HANDLE_FILL = '#FFFFFF'  # 手柄填充色（白色）
        HANDLE_OUTLINE = '#000000'  # 手柄边框色（黑色）
        # 注意：Tkinter 原生不支持半透明，如需模拟可将颜色改为浅灰色（如 '#CCCCCC'）

        # 绘制截图范围矩形（红框）
        rect_id = canvas.create_rectangle(
            left, top, right, bottom,
            outline=RECT_OUTLINE_COLOR,
            width=RECT_WIDTH,
            fill=''  # 不填充内部，保持透明
        )

        # ==================== 控制手柄（8个） ====================
        handles = {}  # 存储手柄信息：name -> {'id': canvas_id, 'logic_center': (x,y)}
        handle_ids = []  # 存储所有手柄的 canvas item id，用于点击检测
        # 每个手柄的逻辑中心位于红框的边界上，视觉方块向外偏移 HANDLE_SIZE 像素
        handle_offsets = {
            'nw': (-HANDLE_SIZE, -HANDLE_SIZE),
            'ne': (HANDLE_SIZE, -HANDLE_SIZE),
            'sw': (-HANDLE_SIZE, HANDLE_SIZE),
            'se': (HANDLE_SIZE, HANDLE_SIZE),
            'n': (0, -HANDLE_SIZE),  # 上边中点
            's': (0, HANDLE_SIZE),  # 下边中点
            'w': (-HANDLE_SIZE, 0),  # 左边中点
            'e': (HANDLE_SIZE, 0)  # 右边中点
        }

        def create_handle(name, lx, ly):
            """在逻辑中心 (lx, ly) 处创建一个控制手柄，并绑定拖拽事件"""
            off_x, off_y = handle_offsets[name]
            # 计算视觉方块的中心坐标（向外偏移后）
            vx = lx + off_x
            vy = ly + off_y
            h = canvas.create_rectangle(
                vx - HANDLE_SIZE, vy - HANDLE_SIZE,
                vx + HANDLE_SIZE, vy + HANDLE_SIZE,
                fill=HANDLE_FILL,
                outline=HANDLE_OUTLINE
            )
            handles[name] = {'id': h, 'logic_center': (lx, ly)}
            handle_ids.append(h)
            # 绑定手柄事件：点击开始 resize，拖拽时改变大小，松开鼠标结束
            canvas.tag_bind(h, '<Button-1>', lambda e, n=name: start_resize(e, n))
            canvas.tag_bind(h, '<B1-Motion>', lambda e: on_resize_drag(e))
            canvas.tag_bind(h, '<ButtonRelease-1>', lambda e: on_resize_release(e))

        def update_handles():
            """根据当前红框坐标，刷新所有手柄的位置"""
            cx = (left + right) // 2
            cy = (top + bottom) // 2
            logic_positions = {
                'nw': (left, top),
                'ne': (right, top),
                'sw': (left, bottom),
                'se': (right, bottom),
                'n': (cx, top),
                's': (cx, bottom),
                'w': (left, cy),
                'e': (right, cy)
            }
            for name, (lx, ly) in logic_positions.items():
                off_x, off_y = handle_offsets[name]
                vx = lx + off_x
                vy = ly + off_y
                canvas.coords(handles[name]['id'],
                              vx - HANDLE_SIZE, vy - HANDLE_SIZE,
                              vx + HANDLE_SIZE, vy + HANDLE_SIZE)
                handles[name]['logic_center'] = (lx, ly)

        # 初始化所有手柄
        for name in ['nw', 'ne', 'sw', 'se', 'n', 's', 'w', 'e']:
            create_handle(name, 0, 0)
        update_handles()  # 放置到正确位置

        # ==================== 拖拽状态管理 ====================
        drag_data = {
            "mode": None,  # 当前操作模式：'move' 移动红框，'resize' 调整大小
            "start_x": 0,
            "start_y": 0,
            "orig_rect": None,  # 拖拽开始时的矩形边界 (left, top, right, bottom)
            "handle": None  # resize 时的手柄名称
        }

        def start_move(event):
            """鼠标在红框内按下：开始移动模式"""
            if left <= event.x <= right and top <= event.y <= bottom:
                drag_data["mode"] = "move"
                drag_data["start_x"] = event.x
                drag_data["start_y"] = event.y
                drag_data["orig_rect"] = (left, top, right, bottom)

        def start_resize(event, handle):
            """鼠标在手柄上按下：开始调整大小模式"""
            drag_data["mode"] = "resize"
            drag_data["start_x"] = event.x
            drag_data["start_y"] = event.y
            drag_data["handle"] = handle
            drag_data["orig_rect"] = (left, top, right, bottom)

        def on_drag(event):
            """处理鼠标拖拽（移动或调整大小）"""
            mode = drag_data.get("mode")
            if not mode:
                return
            dx = event.x - drag_data["start_x"]
            dy = event.y - drag_data["start_y"]
            nonlocal left, top, right, bottom

            if mode == "move":
                # 整体移动矩形，并限制在屏幕内
                orig = drag_data["orig_rect"]
                new_left = orig[0] + dx
                new_top = orig[1] + dy
                new_right = orig[2] + dx
                new_bottom = orig[3] + dy
                # 边界限制：左/上不能小于0，右/下不能超出屏幕
                if new_left < 0:
                    new_right -= new_left
                    new_left = 0
                if new_top < 0:
                    new_bottom -= new_top
                    new_top = 0
                if new_right > screen_w:
                    new_left -= (new_right - screen_w)
                    new_right = screen_w
                if new_bottom > screen_h:
                    new_top -= (new_bottom - screen_h)
                    new_bottom = screen_h
                left, top, right, bottom = new_left, new_top, new_right, new_bottom
                canvas.coords(rect_id, left, top, right, bottom)
                update_handles()
            elif mode == "resize":
                # 根据手柄名称修改对应边界的坐标
                h = drag_data["handle"]
                orig = drag_data["orig_rect"]
                ol, ot, or_, ob = orig
                min_size = 20  # 最小矩形尺寸
                if 'w' in h:  # 包含左侧手柄
                    new_l = ol + dx
                    if new_l < 0: new_l = 0
                    if or_ - new_l < min_size: new_l = or_ - min_size
                    left = new_l
                if 'e' in h:  # 包含右侧手柄
                    new_r = or_ + dx
                    if new_r > screen_w: new_r = screen_w
                    if new_r - left < min_size: new_r = left + min_size
                    right = new_r
                if 'n' in h:  # 包含上侧手柄
                    new_t = ot + dy
                    if new_t < 0: new_t = 0
                    if ob - new_t < min_size: new_t = ob - min_size
                    top = new_t
                if 's' in h:  # 包含下侧手柄
                    new_b = ob + dy
                    if new_b > screen_h: new_b = screen_h
                    if new_b - top < min_size: new_b = top + min_size
                    bottom = new_b
                # 更新红框和手柄
                canvas.coords(rect_id, left, top, right, bottom)
                update_handles()

        def on_release(event):
            """鼠标释放：结束拖拽，保存当前区域到配置并更新相关参数"""
            if drag_data.get("mode"):
                drag_data["mode"] = None
                # 保存截图区域
                self.region = (left, top, right, bottom)
                CONFIG["CROP_REGION"] = self.region
                CONFIG["CROP_RATIO"] = 1.0  # 手动调整后比例固定为1.0
                # 如果设置窗口打开，同步更新比例输入框
                if hasattr(self, '_settings_win') and self._settings_win is not None:
                    if hasattr(self, 'param_entries') and 'CROP_RATIO' in self.param_entries:
                        self.param_entries['CROP_RATIO'].set("1.0")
                # 根据新区域重新计算处理帧的尺寸
                keep_h = self.region[3] - self.region[1]
                keep_w = self.region[2] - self.region[0]
                self.frame_shape = (int(keep_h * CONFIG["SCALE_FACTOR"]),
                                    int(keep_w * CONFIG["SCALE_FACTOR"]))
                self._save_all_settings()
            # 如果设置窗口打开，将比例标签文本改为“手动区域缩放比例”
            if hasattr(self, 'crop_ratio_label_var'):
                self.crop_ratio_label_var.set("手动区域缩放比例:")

        # resize 模式的事件转发（手柄绑定用）
        def on_resize_drag(event):
            on_drag(event)

        def on_resize_release(event):
            on_release(event)

        # ==================== 点击检测与控制 ====================
        def is_on_handle(x, y):
            """判断点 (x,y) 是否落在任意手柄上"""
            items = canvas.find_overlapping(x - 1, y - 1, x + 1, y + 1)  # 小范围查找
            for item in items:
                if item in handle_ids:
                    return True
            return False

        def on_canvas_click(event):
            """
            画布左键点击事件：
            - 若点击在手柄上，什么都不做（手柄事件已经处理）。
            - 若点击在红框内，开始移动。
            - 若点击在红框外，关闭窗口。
            """
            if is_on_handle(event.x, event.y):
                return  # 手柄点击由自身回调处理，此处忽略
            if left <= event.x <= right and top <= event.y <= bottom:
                start_move(event)
            else:
                close_win()

        def close_win(event=None):
            """关闭截图范围窗口"""
            win.destroy()
            self._crop_window = None

        # 绑定画布事件
        canvas.bind("<Button-1>", on_canvas_click)  # 左键按下
        canvas.bind("<B1-Motion>", on_drag)  # 拖拽移动
        canvas.bind("<ButtonRelease-1>", on_release)  # 松开左键
        win.bind("<Escape>", close_win)  # ESC 键关闭

        # 提示文字
        canvas.create_text(
            screen_w // 2, 30,
            text="拖动矩形移动，拖拽白点调整大小，点击外部或ESC关闭",
            fill="white",
            font=("Arial", 12)
        )
    def _save_all_settings(self):
        settings = {
            "config": CONFIG,
            "use_optical_flow": self.use_optical_flow,
            "use_hash_filter": self.use_hash_filter,
            "layouts": self.layouts,
            "active_layout": self.active_layout
        }
        path = self._get_settings_path()
        with open(path, "w") as f:
            json.dump(settings, f, indent=2)

    # ---------- 设置窗口总张数波形（已移除缩放过滤占比） ----------
    def _draw_settings_wave(self):
        if not hasattr(self, 'settings_wave_canvas') or self.settings_wave_canvas is None:
            return
        canvas = self.settings_wave_canvas
        if not canvas.winfo_exists():
            return

        history = list(self.total_history)
        width = canvas.winfo_width()
        height = canvas.winfo_height()
        canvas.delete("all")

        # 尺寸不足时使用短间隔重试，并取消后续依赖长间隔的调度
        if width < 50 or height < 50 or not history:
            if hasattr(self, '_settings_win') and self._settings_win is not None and self._settings_win.winfo_exists():
                self.root.after(100, self._draw_settings_wave)  # 改为100ms重试
            return

        times = [p[0] for p in history]
        raw = [p[1] for p in history]
        filtered = [p[2] for p in history]
        trans = [p[3] for p in history]
        flow = [p[4] for p in history]
        hashes = [p[5] for p in history]
        still = [p[6] for p in history]
        local = [p[7] for p in history]
        other_unknown = [p[8] for p in history]  # 索引由原来的9调整为8

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

        # ---------- 右下角：近5秒过滤类型占比堆叠柱状图（已移除缩放过滤） ----------
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
                '其他': last[8] - first[8]
            }
            total_inc = sum(inc.values())
            if total_inc > 0:
                inc_colors = {
                    '平移': color_manager.get_color('filter_translation'),
                    '光流': color_manager.get_color('filter_optical_flow'),
                    '哈希': color_manager.get_color('filter_hash'),
                    '静止': color_manager.get_color('filter_still'),
                    '局部': color_manager.get_color('filter_local'),
                    '其他': color_manager.get_color('filter_other')
                }
                bar_w = 80
                bar_h = 40
                bar_x = margin['left'] + plot_w + bar_w - 35
                bar_y = margin['top'] + plot_h - bar_h + 2
                canvas.create_rectangle(bar_x - 1, bar_y - 1, bar_x + bar_w + 1, bar_y + bar_h + 1,
                                        outline=accent)
                cum_y = bar_y + bar_h
                for key in ['平移', '光流', '哈希', '静止', '局部', '其他']:
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

        # 持续刷新
        if hasattr(self, '_settings_win') and self._settings_win is not None and self._settings_win.winfo_exists():
            refresh_ms = int(CONFIG["TOTAL_WAVE_REFRESH_SEC"] * 1000)
            self.root.after(refresh_ms, self._draw_settings_wave)

if __name__ == "__main__":
=======
import cv2
import numpy as np
import tkinter as tk
import time
import threading
import json
import copy
import os
import sys
from collections import deque


# 预览功能依赖 PIL
try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

try:
    import dxcam
    DX_AVAILABLE = True
except ImportError:
    DX_AVAILABLE = False

if not DX_AVAILABLE:
    raise ImportError("请安装 dxcam 库: pip install dxcam")

# ==================== 颜色管理器 ====================
class ColorManager:
    """统一管理全局调色板，支持主题切换与实时更新控件"""
    def __init__(self, config_colors):

        self._config = config_colors
        self._widget_color_map = {}
        self._widgets_to_update = []

    def get_color(self, key):
        return self._config.get(key, "#FFFFFF")

    def register_widget(self, widget, color_key):
        self._widget_color_map[id(widget)] = color_key
        if not hasattr(widget, '_color_key'):
            widget._color_key = color_key
        self._widgets_to_update.append(widget)
    def apply_theme(self):
        for widget_id, color_key in self._widget_color_map.items():
            for widget in self._widgets_to_update:
                if id(widget) == widget_id:
                    try:
                        bg = self._config.get('bg', '#000000')
                        fg = self._config.get('accent', '#FFA500')
                        if isinstance(widget, tk.Frame) or isinstance(widget, tk.Canvas):
                            widget.configure(bg=bg)
                        elif isinstance(widget, tk.Label) or isinstance(widget, tk.Checkbutton):
                            widget.configure(fg=fg, bg=bg)
                    except Exception:
                        pass

    def clear(self):
        self._widget_color_map.clear()
        self._widgets_to_update.clear()

color_manager = None

# ==================== 配置参数（已移除所有缩放运镜相关常量） ====================
CONFIG = {

    "USE_CUSTOM_COLORS": True,
    # ========== 新增 ==========
    "SHOW_INFO_PANEL": True,  # 显示实时数据面板
    "SHOW_WAVE1": True,  # 显示波形1
    "SHOW_WAVE2": True,  # 显示波形2
    "REFRESH_INTERVAL": 0.4,           # UI 标签刷新间隔（秒）
    "SCALE_FACTOR": 0.5,               # 截图缩放比例
    "SSIM_THRESHOLD": 0.90,            # 背景相似度阈值（用于局部变化过滤）
    "WAVE_HISTORY_SEC": 60,            # 波形1历史时长（秒）
    "WAVE_REFRESH_MS": 200,            # 波形1刷新间隔（毫秒）
    "WAVE2_HISTORY_SEC": 1500,         # 波形2历史时长（秒）
    "WAVE2_REFRESH_MS": 3000,          # 波形2刷新间隔（毫秒）
    "JingzhiShiJian": 60,              # 静止自动暂停（秒）
    "WAVE_MAX_Y": 24,                  # 波形Y轴最大值（张/秒）
    "CROP_RATIO": 0.7,                 # 截取屏幕中心区域比例
    "CROP_REGION": None,               # 手动截图区域（left, top, right, bottom），None则自动计算
    "ALPHA": 1,                        # 窗口透明度（0.0~1.0）
    "DIFF_THRESHOLD": 22,              # 静态二值化阈值（备选），实际使用自适应阈值
    "MIN_DIFF_THRESHOLD": 5,           # 极暗场景下的最低阈值
    "MAX_DIFF_THRESHOLD": 30,          # 亮场景下的最高阈值
    "MIN_CHANGE_RATIO": 0.002,         # 最小变化像素占比（原始检测/基础检测均用）
    "SIGNIFICANT_CHANGE_RATIO": 0.01,  # 变化面积大于此值视为新作画（大面积直接通过）
    "ALIGNED_CHANGE_THRESHOLD": 0.012, # 全局平移对齐后剩余变化面积上限（低于此值视为纯平移运镜）
    # 光流法
    "FLOW_FEATURE_COUNT": 200,         # 光流法提取特征点最大数量
    "FLOW_STATIC_THRESH": 1,         # 位移小于此像素视为静止点（对应原图2像素）
    "FLOW_MEDIAN_SHIFT_MIN": 0.01,     # 主平移量至少大于此值才考虑图层分离
    "FLOW_LAYER_STATIC_THRESH": 2.0,   # 对齐后剩余移动的静止判断阈值（像素）
    "FLOW_LAYER_CONSISTENCY": 0.75,    # 方向一致性阈值（大于此值视为运镜）
    "SUBTITLE_BOTTOM_RATIO": 0.12,      # 底部屏蔽区域高度占比（用于特征提取），设为0则不屏蔽
    "FLOW_QUALITY_LEVEL": 0.03,        # 角点检测质量阈值，降低以增加点数
    "FLOW_MIN_DISTANCE": 12,           # 角点最小间距，减小以允许更密集
    "SUBTITLE_CONTRAST_FILTER": True,  # 是否启用底部文字对比度过滤（否则直接屏蔽整个底部）
    "SUBTITLE_GRADIENT_THRESH": 50,    # 底部文字梯度强度阈值（0-255）
    "SUBTITLE_DENSITY_THRESH": 1,      # 局部高对比度像素密度阈值（0-1）

    "FRAME_BUFFER_SIZE": 24,           # 哈希缓冲区大小（帧数）
    "HASH_THRESHOLD": 1,               # 汉明距离阈值，≤此值视为相同帧
    "TOTAL_WAVE_REFRESH_SEC": 2,       # 设置界面总张数波形刷新间隔（秒）
    "FILTER_TRIGGER_WINDOW_MS": 200,   # 动态过滤触发窗口（毫秒）
    "FILTER_TRIGGER_COUNT": 3,         # 窗口内基础检测新张数达到此值触发完整过滤
    "FULL_FILTER_HOLD_SEC": 5,         # 触发后保持完整过滤的秒数

    "BASIC_CORR_THRESHOLD": 0.995,     # 基础检测快速相似度阈值（高于此值且变化极小则判静止）
    "BASIC_MIN_RAW_RATIO_STILL": 0.003,# 基础检测极慢平移/静止的最小变化面积
    "BASIC_SIGNIFICANT_RATIO": 0.01,   # 基础检测大面积直接通过阈值

    "FULL_CORR_THRESHOLD": 0.985,      # 完整检测快速相似度阈值
    "FULL_STILL_RATIO": 0.01,          # 完整检测静止变化面积阈值

    "LOCAL_AREA_THRESH": 0.003,        # 局部运动判断：最大连通域面积占比下限
    "LOCAL_BBOX_RATIO_MAX": 0.8,       # 局部运动判断：变化点包围盒面积占比上限
    "LOCAL_ASPECT_RATIO_MAX": 8,       # 局部运动判断：最大连通域宽高比上限

    "LAYER_MIN_VALID_POINTS": 15,       # 光流图层分离：最小有效特征点数
    "LAYER_MIN_MOVING_POINTS": 10,      # 光流图层分离：最小移动点数
    "LAYER_DIRECTION_CONSISTENCY": 0.75,# 光流图层分离：方向一致性阈值（与FLOW_LAYER_CONSISTENCY一致）
    "LAYER_MEAN_VEC_MIN": 0.1,         # 光流图层分离：主方向向量最小模长
    "LAYER_COS_SIM_THRESH": 0.7,       # 光流图层分离：与主方向夹角余弦阈值

    "PREVIEW_DENSE_ALPHA": 0.6,        # 预览稠密光流叠加透明度
    "PREVIEW_DIFF_DECAY": 0.7,         # 预览差分残影衰减系数 越小残留越短
    "PREVIEW_MOTION_DECAY": 0.85,      # 预览运动拖影衰减系数
    "PREVIEW_MOTION_MAX_SPEED": 50,    # 预览运动速度映射最大值（像素）
    "COLORS": {
        "accent": "#FFA500",
        "bg": "#000000",
        "secondary": "#778899",
        "btn_bg": "#222222",
        "title_bg": "#111111",
        "canvas_bg": "#000000",
        "wave_line_filtered": "#FFA500",
        "wave_line_raw": "#778899",
        "filter_translation": "#FF4444",
        "filter_optical_flow": "#4488FF",
        "filter_hash": "#CC44CC",
        "filter_still": "#FF8C00",
        "filter_local": "#00FFFF",
        "filter_other": "#888888",
        "filter_raw_total": "#FFA500",
        "filter_filtered_total": "#00CC66"
    },
    "USE_CUSTOM_COLORS": True,
}
CONFIG_DEFAULT = copy.deepcopy(CONFIG)
color_manager = ColorManager(CONFIG["COLORS"])

# ==================== 可拖动面板类（已去除磁吸） ====================
class DraggablePanel:
    def __init__(self, parent, title, width=300, height=200, min_width=150, min_height=100):
        self.parent = parent
        self.min_width = min_width
        self.min_height = min_height
        self.locked = False
        self._drag_data = {"x": 0, "y": 0, "mode": None}

        bg = color_manager.get_color('bg')
        accent = color_manager.get_color('accent')
        title_bg = color_manager.get_color('title_bg')

        self.frame = tk.Frame(parent, bg=bg, bd=2, relief=tk.RAISED)
        self.frame.place(x=50, y=50, width=width, height=height)

        self.title_bar = tk.Frame(self.frame, bg=title_bg, height=25, cursor="fleur")
        self.title_bar.pack(fill=tk.X)
        self.title_label = tk.Label(self.title_bar, text=title, bg=title_bg, fg=accent,
                                    font=("Arial", 9, "bold"), cursor="fleur")
        self.title_label.pack(side=tk.LEFT, padx=5)

        self.title_bar.bind("<Button-1>", lambda e: self._check_lock(e, "move"))
        self.title_bar.bind("<B1-Motion>", self._on_drag)
        self.title_label.bind("<Button-1>", lambda e: self._check_lock(e, "move"))
        self.title_label.bind("<B1-Motion>", self._on_drag)

        self.content = tk.Frame(self.frame, bg=bg)
        self.content.pack(fill=tk.BOTH, expand=True)

        self._resize_handles = []
        handle_se = tk.Frame(self.frame, bg="#444444", width=10, height=10, cursor="size_nw_se")
        handle_se.place(relx=1.0, rely=1.0, anchor="se")
        handle_se.bind("<Button-1>", lambda e: self._check_lock(e, "resize_se"))
        handle_se.bind("<B1-Motion>", self._on_drag)
        self._resize_handles.append(handle_se)

        handle_e = tk.Frame(self.frame, bg="#333333", width=5, cursor="size_we")
        handle_e.place(relx=1.0, rely=0.0, anchor="ne", height=25)
        handle_e.bind("<Button-1>", lambda e: self._check_lock(e, "resize_e"))
        handle_e.bind("<B1-Motion>", self._on_drag)
        self._resize_handles.append(handle_e)

        handle_s = tk.Frame(self.frame, bg="#333333", height=5, cursor="size_ns")
        handle_s.place(relx=0.0, rely=1.0, anchor="sw", width=25)
        handle_s.bind("<Button-1>", lambda e: self._check_lock(e, "resize_s"))
        handle_s.bind("<B1-Motion>", self._on_drag)
        self._resize_handles.append(handle_s)

        self.frame.bind("<ButtonRelease-1>", self.end_drag)

        color_manager.register_widget(self.frame, 'bg')
        color_manager.register_widget(self.title_bar, 'title_bg')
        color_manager.register_widget(self.title_label, 'accent')
        color_manager.register_widget(self.content, 'bg')


    def _check_lock(self, event, mode):
        if self.locked:
            return
        self.start_drag(event, mode)

    def start_drag(self, event, mode):
        self._drag_data["x"] = event.x_root
        self._drag_data["y"] = event.y_root
        self._drag_data["mode"] = mode
        self._drag_data["init_geom"] = (
            self.frame.winfo_x(),
            self.frame.winfo_y(),
            self.frame.winfo_width(),
            self.frame.winfo_height()
        )

    def _on_drag(self, event):
        if self.locked:
            return
        mode = self._drag_data.get("mode")
        if not mode:
            return
        dx = event.x_root - self._drag_data["x"]
        dy = event.y_root - self._drag_data["y"]

        if mode == "move":
            new_x = self.frame.winfo_x() + dx
            new_y = self.frame.winfo_y() + dy
            self.frame.place(x=new_x, y=new_y)
            self._drag_data["x"] = event.x_root
            self._drag_data["y"] = event.y_root
        elif mode == "resize_se":
            orig = self._drag_data["init_geom"]
            new_w = max(self.min_width, orig[2] + dx)
            new_h = max(self.min_height, orig[3] + dy)
            self.frame.place(width=new_w, height=new_h)
        elif mode == "resize_e":
            orig = self._drag_data["init_geom"]
            new_w = max(self.min_width, orig[2] + dx)
            self.frame.place(width=new_w)
        elif mode == "resize_s":
            orig = self._drag_data["init_geom"]
            new_h = max(self.min_height, orig[3] + dy)
            self.frame.place(height=new_h)

    def end_drag(self, event):
        self._drag_data["mode"] = None

    def set_locked(self, locked):
        self.locked = locked
        if locked:
            self.title_bar.configure(cursor="")
            self.title_label.configure(cursor="")
        else:
            self.title_bar.configure(cursor="fleur")
            self.title_label.configure(cursor="fleur")

    def get_geometry(self):
        return (
            self.frame.winfo_x(),
            self.frame.winfo_y(),
            self.frame.winfo_width(),
            self.frame.winfo_height()
        )

    def apply_geometry(self, geom):
        x, y, w, h = geom
        parent_w = self.parent.winfo_width()
        parent_h = self.parent.winfo_height()
        if parent_w > 10 and parent_h > 10:
            min_visible = 50
            if x + w < min_visible:
                x = min_visible - w
            if x > parent_w - min_visible:
                x = parent_w - min_visible
            if y + h < min_visible:
                y = min_visible - h
            if y > parent_h - min_visible:
                y = parent_h - min_visible
        self.frame.place(x=x, y=y, width=max(self.min_width, w), height=max(self.min_height, h))

# ==================== 主检测类 ====================
class AnimeCelCounter:
    def __init__(self):
        if getattr(sys, 'frozen', False):
            base_dir = sys._MEIPASS
        else:
            base_dir = os.path.dirname(os.path.abspath(__file__))
        self.readme_path = os.path.join(base_dir, "README.txt")

        self._settings_win = None
        self.lock = threading.Lock()
        self._stop_event = threading.Event()
        self._run_event = threading.Event()
        self._run_event.set()



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



        self._preview_pending = False
        self._latest_preview_img = None


        self._prev_cels_count = -1
        self._last_disp_cels = -1
        self.last_move_type = "静止"

        self.total_history = deque(maxlen=6000)
        self._last_recorded_time = -1

        self.full_filter_active = False
        self.full_filter_active_until = 0
        self.raw_detection_timestamps = deque()

        self.total_translation_filtered = 0
        self.total_optical_flow_filtered = 0
        self.total_hash_filtered = 0
        self.total_still_filtered = 0
        self.total_local_filtered = 0
        self.total_other_unknown_filtered = 0

        self.preview_active = False
        self.preview_thread = None
        self.preview_lock = threading.Lock()
        self.preview_cache = (None, None)
        self.preview_update_interval = 0.05
        self._preview_label = None
        self._diff_decay = None
        self._motion_history = None
        self._preview_control_lock = threading.Lock()
        self._preview_after_id = None

        self.load_settings()

        self.frame_buffer = deque(maxlen=CONFIG["FRAME_BUFFER_SIZE"])
        self.hash_threshold = CONFIG["HASH_THRESHOLD"]

        max_wave_points = int(CONFIG["WAVE_HISTORY_SEC"] * (1000 / CONFIG["WAVE_REFRESH_MS"]))
        self.wave_data = deque(maxlen=max_wave_points)
        self.wave_raw_data = deque(maxlen=max_wave_points)

        max_wave2_points = int(CONFIG["WAVE2_HISTORY_SEC"] * (1000 / CONFIG["WAVE2_REFRESH_MS"]))
        self.wave2_data = deque(maxlen=max_wave2_points)
        self.wave2_raw_data = deque(maxlen=max_wave2_points)

        self.smooth_cels = 0.0
        self.smooth_raw_cels = 0.0

        self.camera = dxcam.create(output_color="BGR")
        self.camera.start(target_fps=0, video_mode=False)  # 0 = 不限速，全速捕获

        monitor_sample = self.camera.get_latest_frame()
        if monitor_sample is not None:
            h, w = monitor_sample.shape[:2]
            if CONFIG.get("CROP_REGION"):
                l, t, r, b = CONFIG["CROP_REGION"]
                self.region = (max(0, l), max(0, t), min(w, r), min(h, b))
            else:
                center_h = int(h * (1 - CONFIG["CROP_RATIO"]) / 2)
                center_w = int(w * (1 - CONFIG["CROP_RATIO"]) / 2)
                self.region = (center_w, center_h, w - center_w, h - center_h)
            keep_h = self.region[3] - self.region[1]
            keep_w = self.region[2] - self.region[0]
            self.frame_shape = (int(keep_h * CONFIG["SCALE_FACTOR"]),
                                int(keep_w * CONFIG["SCALE_FACTOR"]))
        else:
            self.region = (384, 216, 1536, 864)
            self.frame_shape = (100, 100)

        self._build_ui()

        self.capture_thread = threading.Thread(target=self._capture_loop, daemon=True)
        self.capture_thread.start()

        self.root.after(30, self.loop)
        self.root.after(CONFIG["WAVE_REFRESH_MS"], self.update_wave)
        self.root.after(CONFIG["WAVE2_REFRESH_MS"], self.update_wave2)
        self.root.mainloop()

    def _compute_hash(self, gray_img):
        resized = cv2.resize(gray_img, (16, 16), interpolation=cv2.INTER_AREA)
        avg = resized.mean()
        return (resized > avg).flatten()

    def _get_settings_path(self):
        if getattr(sys, 'frozen', False):
            return os.path.join(os.path.expanduser("~"), "cel_counter_settings.json")
        return "cel_counter_settings.json"

    def load_settings(self):
        try:
            settings_path = self._get_settings_path()
            with open(settings_path, "r") as f:
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

            # 同步全局颜色管理器，使加载的颜色立即生效
            color_manager._config = CONFIG["COLORS"]
        except (FileNotFoundError, json.JSONDecodeError):
            self.use_optical_flow = True
            self.use_hash_filter = False
            self.layouts = {}
            self.active_layout = None

    def _build_ui(self):
        self.root = tk.Tk()
        self.root.title("动画作画张数")
        self.root.geometry("1200x420")
        self.root.attributes("-topmost", True)
        TRANSPARENT = "#000000"
        self.root.attributes("-transparentcolor", TRANSPARENT)
        self.root.configure(bg=TRANSPARENT)
        self.root.overrideredirect(True)

        main_frame = tk.Frame(self.root, bg=TRANSPARENT)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        info_frame = tk.Frame(main_frame, bg=TRANSPARENT, width=130, height=200)
        info_frame.grid(row=0, column=0, sticky="nw", padx=(0, 10), pady=(10, 0))
        info_frame.pack_propagate(False)

        accent = color_manager.get_color('accent')
        self.lb_rt = tk.Label(info_frame, text="实时张数：0.0 ", fg=accent, bg=TRANSPARENT,
                              font=("Arial", 10, "bold"), anchor="w")
        self.lb_rt.pack(anchor="w", pady=3)

        self.lb_total_time = tk.Label(info_frame, text="运行时长：0.0 s", fg=accent, bg=TRANSPARENT,
                                      font=("Arial", 10, "bold"), anchor="w")
        self.lb_total_time.pack(anchor="w", pady=3)
        self.lb_total_cels = tk.Label(info_frame, text="总张数：0", fg=accent, bg=TRANSPARENT,
                                      font=("Arial", 10, "bold"), anchor="w")
        self.lb_total_cels.pack(anchor="w", pady=3)

        self.lb_avg = tk.Label(info_frame, text="总平均：0.0", fg=accent, bg=TRANSPARENT,
                               font=("Arial", 10, "bold"), anchor="w")
        self.lb_avg.pack(anchor="w", pady=3)
        self.lb_st = tk.Label(info_frame, text="静止/纯运镜", fg=accent, bg=TRANSPARENT,
                              font=("Arial", 10, "bold"), anchor="w")
        self.lb_st.pack(anchor="w", pady=5)

        self.wave_frame = tk.Frame(main_frame, bg=TRANSPARENT, width=300, height=80)
        self.wave_frame.grid(row=0, column=1, sticky="n", padx=(0, 10), pady=(10, 0))
        self.wave_frame.pack_propagate(False)
        self.canvas = tk.Canvas(self.wave_frame, bg=TRANSPARENT, highlightthickness=0, bd=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)

        self.wave2_frame = tk.Frame(main_frame, bg=TRANSPARENT, width=300, height=80)
        self.wave2_frame.grid(row=0, column=2, sticky="n", padx=(0, 10), pady=(10, 0))
        self.wave2_frame.pack_propagate(False)
        self.canvas2 = tk.Canvas(self.wave2_frame, bg=TRANSPARENT, highlightthickness=0, bd=0)
        self.canvas2.pack(fill=tk.BOTH, expand=True)

        btn_canvas = tk.Canvas(main_frame, bg=TRANSPARENT, highlightthickness=0, bd=0,
                               width=120, height=150)
        btn_canvas.grid(row=0, column=3, sticky="n", padx=(0, 10), pady=(10, 0))
        self.btn_canvas = btn_canvas
        self._draw_buttons()

        self.wave_margin_left = 30
        self.wave_margin_right = 10
        self.wave_margin_top = 10
        self.wave_margin_bottom = 10

        self.make_draggable()

    def _draw_buttons(self):
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
            canvas.tag_bind(rect_id, "<Button-1>", lambda e, c=cmd: c())
            canvas.tag_bind(text_id, "<Button-1>", lambda e, c=cmd: c())
            canvas.tag_bind(rect_id, "<Enter>",
                            lambda e, r=rect_id: canvas.itemconfig(r, fill="#111111"))
            canvas.tag_bind(rect_id, "<Leave>",
                            lambda e, r=rect_id: canvas.itemconfig(r, fill=""))
            canvas.tag_bind(text_id, "<Enter>",
                            lambda e, r=rect_id: canvas.itemconfig(r, fill="#A12F2F"))
            canvas.tag_bind(text_id, "<Leave>",
                            lambda e, r=rect_id: canvas.itemconfig(r, fill=""))

    # ============ 布局保存/加载 ============
    def _save_layout(self, win, panels):
        try:
            layout = {
                'window_geometry': win.winfo_geometry(),
                'panels': {
                    name: panel.get_geometry()
                    for name, panel in panels.items()
                }
            }
            settings_path = self._get_settings_path()
            try:
                with open(settings_path, "r") as f:
                    data = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError):
                data = {}
            data['layout'] = layout
            with open(settings_path, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"保存布局失败: {e}")

    def _apply_layout(self, win, panels):
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



    # ============ 设置窗口 ============
    def open_settings(self):
        if self._settings_win is not None and self._settings_win.winfo_exists():
            self._settings_win.lift()
            return

        win = tk.Toplevel(self.root)
        self._settings_win = win
        win.title("参数设置")
        win.overrideredirect(True)
        win.attributes("-alpha", CONFIG["ALPHA"])
        win.configure(bg=color_manager.get_color('bg'))
        win.geometry("1200x760")

        def on_close():
            if CONFIG["USE_CUSTOM_COLORS"]:
                for key, (var, _) in self.color_vars.items():
                    CONFIG["COLORS"][key] = var.get()
            current_layout = {
                'window_geometry': win.winfo_geometry(),
                'panels': {name: panel.get_geometry() for name, panel in panels.items()}
            }
            if self.active_layout:
                self.layouts[self.active_layout] = current_layout
            else:
                self.layouts["默认布局"] = current_layout
                self.active_layout = "默认布局"
            self._save_all_settings()
            if hasattr(self, 'settings_canvas'):
                self.settings_canvas.unbind_all("<MouseWheel>")
            self._settings_win = None
            self.settings_wave_canvas = None
            self._preview_label = None
            self._stop_preview()
            if hasattr(self, '_crop_window') and self._crop_window:
                self._crop_window.destroy()
            win.destroy()

        accent = color_manager.get_color('accent')
        bg = color_manager.get_color('bg')
        title_bg = color_manager.get_color('title_bg')
        btn_bg = color_manager.get_color('btn_bg')

        title_bar = tk.Frame(win, bg=title_bg, height=30, cursor="fleur")
        title_bar.pack(fill=tk.X)
        title_label = tk.Label(title_bar, text="参数设置", bg=title_bg, fg=accent,
                               font=("Arial", 10, "bold"))
        title_label.pack(side=tk.LEFT, padx=10)

        pin_var = tk.BooleanVar(value=True)
        def toggle_pin():
            pin_var.set(not pin_var.get())
            win.attributes("-topmost", pin_var.get())
            pin_btn.config(text="📌" if pin_var.get() else "📍")

        close_btn = tk.Button(title_bar, text="✕", bg=btn_bg, fg=accent,
                              font=("Arial", 10, "bold"), command=on_close,
                              bd=0, activebackground="#AA0000", width=3)
        close_btn.pack(side=tk.RIGHT, padx=5)
        pin_btn = tk.Button(title_bar, text="📍", bg=btn_bg, fg=accent,
                            font=("Arial", 8), command=toggle_pin, bd=0,
                            activebackground="#444444", width=3)
        pin_btn.pack(side=tk.RIGHT, padx=5)

        def start_move(event):
            win._drag_start_x = event.x_root
            win._drag_start_y = event.y_root
            win._drag_start_win_x = win.winfo_x()
            win._drag_start_win_y = win.winfo_y()

        def do_move(event):
            dx = event.x_root - win._drag_start_x
            dy = event.y_root - win._drag_start_y
            new_x = win._drag_start_win_x + dx
            new_y = win._drag_start_win_y + dy
            win.geometry(f"+{new_x}+{new_y}")

        title_bar.bind("<Button-1>", start_move)
        title_bar.bind("<B1-Motion>", do_move)
        title_label.bind("<Button-1>", start_move)
        title_label.bind("<B1-Motion>", do_move)

        main_frame = tk.Frame(win, bg=bg)
        main_frame.pack(fill=tk.BOTH, expand=True)

        resize_handle = tk.Frame(win, bg="#444444", width=12, height=12, cursor="size_nw_se")
        resize_handle.place(relx=1.0, rely=1.0, anchor="se")

        def start_resize(event):
            win._resize_start_x = event.x_root
            win._resize_start_y = event.y_root
            win._resize_start_w = win.winfo_width()
            win._resize_start_h = win.winfo_height()

        def do_resize(event):
            dx = event.x_root - win._resize_start_x
            dy = event.y_root - win._resize_start_y
            new_w = max(400, win._resize_start_w + dx)
            new_h = max(300, win._resize_start_h + dy)
            win.geometry(f"{new_w}x{new_h}")

        resize_handle.bind("<Button-1>", start_resize)
        resize_handle.bind("<B1-Motion>", do_resize)

        panels = {}

        param_panel = DraggablePanel(main_frame, "参数设置", 400, 650)
        panels['params'] = param_panel
        self._build_params_content(param_panel.content, win, panels)

        wave_panel = DraggablePanel(main_frame, "总张数波形", 600, 400)
        panels['wave'] = wave_panel
        self.settings_wave_canvas = tk.Canvas(wave_panel.content, bg=bg, highlightthickness=0)
        self.settings_wave_canvas.pack(fill=tk.BOTH, expand=True)

        preview_panel = DraggablePanel(main_frame, "实时预览", 600, 400)
        panels['preview'] = preview_panel
        control_bar = tk.Frame(preview_panel.content, bg=bg)
        control_bar.pack(fill="x", pady=(0, 5))

        self.preview_toggle_var = tk.BooleanVar(value=False)
        tk.Checkbutton(control_bar, text="开启实时预览", variable=self.preview_toggle_var,
                       fg=accent, bg=bg, selectcolor="#222222",
                       command=self._on_preview_toggle).pack(side="left", padx=3)

        self.show_dense_flow_var = tk.BooleanVar(value=True)
        tk.Checkbutton(control_bar, text="稠密光流", variable=self.show_dense_flow_var,
                       fg=accent, bg=bg, selectcolor="#222222").pack(side="left", padx=3)

        self.show_sparse_flow_var = tk.BooleanVar(value=True)
        tk.Checkbutton(control_bar, text="稀疏光流", variable=self.show_sparse_flow_var,
                       fg=accent, bg=bg, selectcolor="#222222").pack(side="left", padx=3)

        self.show_curr_var = tk.BooleanVar(value=True)
        tk.Checkbutton(control_bar, text="当前帧", variable=self.show_curr_var,
                       fg=accent, bg=bg, selectcolor="#222222").pack(side="left", padx=3)

        self.show_diff_var = tk.BooleanVar(value=True)
        tk.Checkbutton(control_bar, text="差分", variable=self.show_diff_var,
                       fg=accent, bg=bg, selectcolor="#222222").pack(side="left", padx=3)



        tk.Label(control_bar, text="刷新间隔(秒):", fg=accent, bg=bg).pack(side="left", padx=3)
        self.preview_interval_var = tk.StringVar(value=str(self.preview_update_interval))
        interval_entry = tk.Entry(control_bar, textvariable=self.preview_interval_var, width=4,
                                  bg=btn_bg, fg=accent, insertbackground=accent)
        interval_entry.pack(side="left")
        interval_entry.bind("<Return>", lambda e: self._update_preview_interval())
        tk.Button(control_bar, text="应用", command=self._update_preview_interval,
                  bg=btn_bg, fg=accent, activebackground="#444444").pack(side="left", padx=2)
        self._preview_label = tk.Label(preview_panel.content, bg=bg)
        self._preview_label.pack(fill=tk.BOTH, expand=True)

        self.lock_panels_var = tk.BooleanVar(value=True)
        def toggle_lock():
            locked = self.lock_panels_var.get()
            for p in panels.values():
                p.set_locked(locked)

        lock_check = tk.Checkbutton(title_bar, text="锁定面板", variable=self.lock_panels_var,
                                    command=toggle_lock, fg=accent, bg=title_bg,
                                    selectcolor=title_bg)
        lock_check.pack(side=tk.RIGHT, padx=10)
        toggle_lock()
        # 强制完成布局，确保内部控件获得正确尺寸
        win.update_idletasks()

        # 启动预览（此时 Label 尺寸已就绪）
        self.preview_toggle_var.set(True)
        if not self.preview_active:
            self._start_preview()

        # 加载布局并绘制波形（使用短延迟确保画布尺寸有效）
        self._apply_layout(win, panels)
        win.after(100, self._draw_settings_wave)   # 延迟以获取正确画布尺寸
        win.protocol("WM_DELETE_WINDOW", on_close)

    def _build_params_content(self, parent, win, panels):
        """构建参数设置面板（两列布局）"""
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

        param_names = {
            "REFRESH_INTERVAL": "UI刷新间隔",
            "ALPHA": "背景透明度",
            "SCALE_FACTOR": "截图缩放比例",
            "SSIM_THRESHOLD": "背景相似度阈值",
            "WAVE_HISTORY_SEC": "波形1时长(秒)",
            "WAVE_REFRESH_MS": "波形1刷新间隔(ms)",
            "WAVE2_HISTORY_SEC": "波形2时长(秒)",
            "WAVE2_REFRESH_MS": "波形2刷新间隔(ms)",
            "JingzhiShiJian": "静止自动暂停(秒)",
            "WAVE_MAX_Y": "波形Y轴最大值",
            "CROP_RATIO": "截图区域比例",
            "DIFF_THRESHOLD": "变化检测阈值",
            "MIN_DIFF_THRESHOLD": "最小变化阈值",
            "MAX_DIFF_THRESHOLD": "最大变化阈值",
            "MIN_CHANGE_RATIO": "最小变化占比",
            "ALIGNED_CHANGE_THRESHOLD": "平移对齐残差",
            "SIGNIFICANT_CHANGE_RATIO": "显著变化占比",
            "FLOW_FEATURE_COUNT": "光流特征点数",
            "FLOW_STATIC_THRESH": "静止点位移阈值",
            "FLOW_MEDIAN_SHIFT_MIN": "主平移最小阈值",
            "FRAME_BUFFER_SIZE": "哈希缓冲区大小",
            "HASH_THRESHOLD": "哈希距离阈值",
            "TOTAL_WAVE_REFRESH_SEC": "总张数波形刷新(秒)",
            "FILTER_TRIGGER_WINDOW_MS": "触发窗口(ms)",
            "FILTER_TRIGGER_COUNT": "触发张数阈值",
            "FULL_FILTER_HOLD_SEC": "完整过滤保持(秒)",
            "BASIC_CORR_THRESHOLD": "基础检测相似度阈值",
            "BASIC_MIN_RAW_RATIO_STILL": "基础静止变化面积",
            "BASIC_SIGNIFICANT_RATIO": "基础大面积阈值",
            "FULL_CORR_THRESHOLD": "完整检测相似度阈值",
            "FULL_STILL_RATIO": "完整静止变化面积",
            "LOCAL_AREA_THRESH": "局部面积占比下限",
            "LOCAL_BBOX_RATIO_MAX": "局部包围盒上限",
            "LOCAL_ASPECT_RATIO_MAX": "局部宽高比上限",
            "LAYER_MIN_VALID_POINTS": "光流最小有效点数",
            "LAYER_MIN_MOVING_POINTS": "光流最小移动点数",
            "LAYER_DIRECTION_CONSISTENCY": "光流方向一致性",
            "LAYER_MEAN_VEC_MIN": "光流主方向最小模长",
            "LAYER_COS_SIM_THRESH": "光流夹角阈值",
            "PREVIEW_DENSE_ALPHA": "预览稠密光流透明度",
            "PREVIEW_DIFF_DECAY": "预览差分衰减",
            "PREVIEW_MOTION_DECAY": "预览运动衰减",
            "PREVIEW_MOTION_MAX_SPEED": "预览最大速度",
        }
        entries = {}

        groups = [
            ("基本", ["REFRESH_INTERVAL", "SCALE_FACTOR", "SSIM_THRESHOLD", "JingzhiShiJian", "CROP_RATIO","ALPHA"]),
            ("波形1", ["WAVE_HISTORY_SEC", "WAVE_REFRESH_MS"]),
            ("波形2", ["WAVE2_HISTORY_SEC", "WAVE2_REFRESH_MS", "WAVE_MAX_Y"]),
            ("变化检测", ["DIFF_THRESHOLD", "MIN_DIFF_THRESHOLD", "MAX_DIFF_THRESHOLD", "MIN_CHANGE_RATIO",
                          "ALIGNED_CHANGE_THRESHOLD", "SIGNIFICANT_CHANGE_RATIO"]),
            ("光流法", ["FLOW_FEATURE_COUNT", "FLOW_STATIC_THRESH", "FLOW_MEDIAN_SHIFT_MIN"]),
            ("重复帧过滤", ["FRAME_BUFFER_SIZE", "HASH_THRESHOLD"]),
            ("总张数波形", ["TOTAL_WAVE_REFRESH_SEC"]),
            ("动态过滤触发", ["FILTER_TRIGGER_WINDOW_MS", "FILTER_TRIGGER_COUNT", "FULL_FILTER_HOLD_SEC"]),
            ("基础检测常量", ["BASIC_CORR_THRESHOLD", "BASIC_MIN_RAW_RATIO_STILL", "BASIC_SIGNIFICANT_RATIO"]),
            ("完整检测常量", ["FULL_CORR_THRESHOLD", "FULL_STILL_RATIO"]),
            ("局部运动判断", ["LOCAL_AREA_THRESH", "LOCAL_BBOX_RATIO_MAX", "LOCAL_ASPECT_RATIO_MAX"]),
            ("光流图层分离", ["LAYER_MIN_VALID_POINTS", "LAYER_MIN_MOVING_POINTS",
                              "LAYER_DIRECTION_CONSISTENCY", "LAYER_MEAN_VEC_MIN", "LAYER_COS_SIM_THRESH"]),
            ("预览参数", ["PREVIEW_DENSE_ALPHA", "PREVIEW_DIFF_DECAY", "PREVIEW_MOTION_DECAY", "PREVIEW_MOTION_MAX_SPEED"]),
        ]

        left_frame = tk.Frame(scroll_frame, bg=bg)
        right_frame = tk.Frame(scroll_frame, bg=bg)
        left_frame.grid(row=0, column=0, sticky="nw", padx=(0, 10))
        right_frame.grid(row=0, column=1, sticky="nw")

        for idx, (group_name, keys) in enumerate(groups):
            target = left_frame if idx % 2 == 0 else right_frame
            row = target.grid_size()[1]
            tk.Label(target, text=group_name, font=("Arial", 10, "bold"),
                     fg=accent, bg=bg).grid(row=row, column=0, columnspan=2, sticky="w", pady=(10, 2))
            row += 1
            for key in keys:
                label_text = param_names.get(key, key) + ":"
                tk.Label(target, text=label_text, fg=accent, bg=bg,
                         font=("Arial", 9)).grid(row=row, column=0, sticky="w", padx=5)
                var = tk.StringVar(value=str(CONFIG[key]))
                ent = tk.Entry(target, textvariable=var, width=10,
                               font=("Arial", 9), bg=btn_bg, fg=accent,
                               insertbackground=accent)
                ent.grid(row=row, column=1, sticky="w")
                entries[key] = var
                row += 1

        row_left = left_frame.grid_size()[1]
        tk.Label(left_frame, text="功能开关", font=("Arial", 10, "bold"),
                 fg=accent, bg=bg).grid(row=row_left, column=0, columnspan=2, sticky="w", pady=(10, 2))
        row_left += 1
        flow_var = tk.BooleanVar(value=self.use_optical_flow)
        tk.Checkbutton(left_frame, text="启用光流法图层分离", variable=flow_var,
                       fg=accent, bg=bg, selectcolor=btn_bg).grid(row=row_left, column=0, columnspan=2, sticky="w", padx=5)
        row_left += 1
        hash_var = tk.BooleanVar(value=self.use_hash_filter)
        tk.Checkbutton(left_frame, text="启用重复帧过滤", variable=hash_var,
                       fg=accent, bg=bg, selectcolor=btn_bg).grid(row=row_left, column=0, columnspan=2, sticky="w", padx=5)
        row_left += 1

        tk.Label(left_frame, text="全局调色板", font=("Arial", 10, "bold"),
                 fg=accent, bg=bg).grid(row=row_left, column=0, columnspan=2, sticky="w", pady=(10, 2))
        row_left += 1
        self.custom_color_var = tk.BooleanVar(value=CONFIG["USE_CUSTOM_COLORS"])
        def toggle_custom_colors():
            CONFIG["USE_CUSTOM_COLORS"] = self.custom_color_var.get()
            for child in color_frame.winfo_children():
                if isinstance(child, tk.Entry) or isinstance(child, tk.Button):
                    child.configure(state="normal" if CONFIG["USE_CUSTOM_COLORS"] else "disabled")
            color_manager.apply_theme()
            self._redraw_all()

        tk.Checkbutton(left_frame, text="启用自定义调色板", variable=self.custom_color_var,
                       command=toggle_custom_colors,
                       fg=accent, bg=bg, selectcolor=btn_bg).grid(row=row_left, column=0, columnspan=2, sticky="w", padx=5)
        row_left += 1

        color_frame = tk.Frame(left_frame, bg=bg)
        color_frame.grid(row=row_left, column=0, columnspan=2, sticky="we", padx=5)
        row_left += 1

        color_keys = [
            ("accent", "主色调"), ("bg", "背景"), ("secondary", "次要"),
            ("btn_bg", "按钮背景"), ("title_bg", "标题栏背景"), ("canvas_bg", "画布背景"),
            ("wave_line_filtered", "波形1过滤后"), ("wave_line_raw", "波形1过滤前"),
            ("filter_translation", "平移过滤"), ("filter_optical_flow", "光流过滤"),
            ("filter_hash", "哈希过滤"), ("filter_still", "静止过滤"),
            ("filter_local", "局部过滤"), ("filter_other", "其他过滤"),
            ("filter_raw_total", "过滤前总张数"), ("filter_filtered_total", "过滤后总张数")
        ]
        self.color_vars = {}
        row_color = 0
        for key, name in color_keys:
            tk.Label(color_frame, text=name+":", fg=accent, bg=bg).grid(row=row_color, column=0, sticky="w")
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

        btn_frame = tk.Frame(left_frame, bg=bg)
        btn_frame.grid(row=row_left, column=0, columnspan=2, pady=10)

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

        def save_settings():
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

            if CONFIG.get("CROP_REGION") is None:
                monitor_sample = self.camera.get_latest_frame()
                if monitor_sample is not None:
                    h, w = monitor_sample.shape[:2]
                    center_h = int(h * (1 - CONFIG["CROP_RATIO"]) / 2)
                    center_w = int(w * (1 - CONFIG["CROP_RATIO"]) / 2)
                    self.region = (center_w, center_h, w - center_w, h - center_h)
                    keep_h = int(h * CONFIG["CROP_RATIO"])
                    keep_w = int(w * CONFIG["CROP_RATIO"])
                    self.frame_shape = (int(keep_h * CONFIG["SCALE_FACTOR"]),
                                        int(keep_w * CONFIG["SCALE_FACTOR"]))

            with self.lock:
                self.last_frame = None
                if self.use_hash_filter:
                    self.frame_buffer.clear()

            self._save_all_settings()

        def reset_to_default():
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

        tk.Button(btn_frame, text="保存", command=save_settings,
                  bg=btn_bg, fg=accent).pack(side="left", padx=5)
        tk.Button(btn_frame, text="恢复默认", command=reset_to_default,
                  bg=btn_bg, fg=accent).pack(side="left", padx=5)
        tk.Button(btn_frame, text="说明", command=self.open_readme,
                  bg=btn_bg, fg=accent).pack(side="left", padx=5)
        tk.Button(btn_frame, text="显示截图范围", command=self.show_crop_region,
                  bg=btn_bg, fg=accent).pack(side="left", padx=5)

        def on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        canvas.bind_all("<MouseWheel>", on_mousewheel)

    def _pick_color(self, key, var):
        from tkinter import colorchooser
        color = colorchooser.askcolor(color=var.get(), title=f"选择{key}颜色")
        if color[1]:
            var.set(color[1])
            self.color_vars[key][1].configure(bg=color[1])

    def _apply_custom_colors(self):
        if CONFIG["USE_CUSTOM_COLORS"]:
            for key, (var, _) in self.color_vars.items():
                CONFIG["COLORS"][key] = var.get()
        color_manager._config = CONFIG["COLORS"]
        color_manager.apply_theme()
        self._redraw_all()

    def _redraw_all(self):
        self.draw_wave()
        self.draw_wave2()
        if hasattr(self, 'settings_wave_canvas') and self.settings_wave_canvas:
            self._draw_settings_wave()
        self._draw_buttons()

    # ============ 预览相关 ============
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

            # 应用字幕掩膜
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

    # ---------- 屏幕捕获 ----------
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

    def _is_raw_change(self, prev, curr):
        diff = cv2.absdiff(prev, curr)
        thresh = self._adaptive_threshold(curr)
        _, mask = cv2.threshold(diff, thresh, 255, cv2.THRESH_BINARY)

        # 应用字幕掩膜，排除底部字幕区域
        if CONFIG["SUBTITLE_BOTTOM_RATIO"] > 0:
            sub_mask = self._get_bottom_subtitle_mask(curr)
            mask = cv2.bitwise_and(mask, sub_mask)  # 字幕区置0

        kernel = np.ones((3, 3), np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
        ratio = np.count_nonzero(mask) / mask.size
        return ratio >= CONFIG["MIN_CHANGE_RATIO"]

    def _get_bottom_subtitle_mask(self, curr_gray):
        """
        返回一个与 curr_gray 同尺寸的 uint8 掩膜：
        底部字幕区域为 0，其余区域为 255。
        若 SUBTITLE_BOTTOM_RATIO == 0，则返回全 255。
        """
        h, w = curr_gray.shape
        subtitle_ratio = CONFIG.get("SUBTITLE_BOTTOM_RATIO", 0)
        if subtitle_ratio <= 0:
            return np.full((h, w), 255, dtype=np.uint8)

        mask = np.ones((h, w), dtype=np.uint8) * 255
        bottom_cut = int(h * (1 - subtitle_ratio))

        if CONFIG.get("SUBTITLE_CONTRAST_FILTER", True):
            # 使用基于对比度的智能字幕掩膜
            sub_mask = self._get_subtitle_mask(curr_gray, subtitle_ratio)  # 返回 0/255 掩膜
            # sub_mask 中底部非字幕区域为 255，字幕区域为 0
            mask[bottom_cut:, :] = sub_mask[bottom_cut:, :]  # 保留底部区域处理
        else:
            # 直接屏蔽整个底部区域
            mask[bottom_cut:, :] = 0
        return mask
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
                self.current_raw_cels = cels_raw



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

    def _basic_is_new_cel(self, prev, curr):
        corr = cv2.matchTemplate(prev, curr, cv2.TM_CCOEFF_NORMED)[0][0]
        # 获取统一字幕掩膜（基于 curr 生成一次）
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

        # 相关度低，完整差分
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

        # 对齐后差分
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

        # 局部运动判断（不再包含缩放运镜分支）
        if change_ratio >= CONFIG["ALIGNED_CHANGE_THRESHOLD"]:
            if self._has_local_motion(mask):
                self.last_move_type = "新作画"
                return True

            # 光流图层分离的特征提取 mask 进一步叠加字幕屏蔽
            feature_mask = mask.copy()
            if sub_mask is not None:
                feature_mask = cv2.bitwise_and(feature_mask, sub_mask)
            if self.use_optical_flow and self._is_layer_camera_move_v2(prev, curr_aligned, feature_mask):
                self.last_move_type = "图层分离运镜"
                return False

            if change_ratio >= CONFIG["SIGNIFICANT_CHANGE_RATIO"]:
                self.last_move_type = "新作画"
                return True

        # ---- 剩余情况：使用 SSIM 判断是否为局部变化 ----
        if change_ratio < CONFIG["SIGNIFICANT_CHANGE_RATIO"]:
            mask_inv = cv2.bitwise_not(mask)
            score = cv2.matchTemplate(prev, curr_aligned, cv2.TM_CCOEFF_NORMED, mask=mask_inv)[0][0]
            if score >= CONFIG["SSIM_THRESHOLD"]:
                self.last_move_type = "局部变化(过滤)"
                return False

        self.last_move_type = "新作画"
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

    # ---------- UI 更新 ----------
    def get_status(self, v):
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
                return base + "一拍一"
            else:
                return base + "高频作画"

    def loop(self):
        if self.is_running:
            now_real = time.time()
            self.elapsed_time += now_real - self.last_loop_time
            self.last_loop_time = now_real

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
                self.total_history.append((self.elapsed_time, total_r, total_f,
                                           trans_f, flow_f, hash_f,
                                           still_f, local_f, other_f))
                self._last_recorded_time = self.elapsed_time

            with self.lock:
                current_total = self.total_cels_count
                has_frame = self._has_first_frame
            if has_frame:
                if current_total != self._prev_cels_count:
                    self.idle_start_time = None
                elif self.idle_start_time is None:
                    self.idle_start_time = self.elapsed_time
                elif self.elapsed_time - self.idle_start_time >= CONFIG["JingzhiShiJian"]:
                    self.pause()
            self._prev_cels_count = current_total

            if current_total != self._last_disp_cels:
                self.lb_total_cels.config(text=f"总张数：{current_total}")
                self._last_disp_cels = current_total

            if now_real - self.last_refresh >= CONFIG["REFRESH_INTERVAL"]:
                with self.lock:
                    real = self.current_cels
                self.total_sum += real
                self.total_count += 1
                avg = self.total_sum / self.total_count if self.total_count > 0 else 0.0

                accent = color_manager.get_color('accent')
                self.lb_rt.config(text=f"实时张数：{real:.1f} ", fg=accent)
                self.lb_total_time.config(text=f"运行时长：{self.elapsed_time:.1f} s", fg=accent)
                self.lb_total_cels.config(text=f"总张数：{current_total}", fg=accent)
                self.lb_avg.config(text=f"总平均：{avg:.1f}", fg=accent)
                self.lb_st.config(text=f"{self.get_status(real)}", fg=accent)
                self.last_refresh = now_real
                self._last_disp_cels = current_total

        self.root.after(20, self.loop)

    def pause(self):
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

        accent = color_manager.get_color('accent')
        self.lb_rt.config(text="实时张数：0.0 ")
        self.lb_total_time.config(text="运行时长：0.0 s")
        self.lb_total_cels.config(text="总张数：0")
        self.lb_avg.config(text="总平均：0.0")
        self.lb_st.config(text="静止/纯运镜")
        self.draw_wave()
        self.draw_wave2()

        if was_running:
            self._run_event.set()
            try:
                self.camera.release()
            except:
                pass
            self.camera = dxcam.create(output_color="BGR")

            self.camera.start(target_fps=0, video_mode=False)
    def _on_close(self):
        self._stop_event.set()
        self._run_event.set()
        self.root.destroy()

    # ---------- 波形绘制 ----------
    def update_wave(self):
        if self.is_running:
            with self.lock:
                cels = self.current_cels
                raw_cels = self.current_raw_cels
            self.wave_data.append((self.elapsed_time, cels))
            self.wave_raw_data.append((self.elapsed_time, raw_cels))
        self.draw_wave()
        self.root.after(CONFIG["WAVE_REFRESH_MS"], self.update_wave)

    def update_wave2(self):
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
        color_filtered = color_manager.get_color('wave_line_filtered')
        color_raw = color_manager.get_color('wave_line_raw')
        data_list = [
            (self.wave_data, color_filtered),
            (self.wave_raw_data, color_raw)
        ]
        self._draw_wave_multi(self.canvas, data_list,
                              CONFIG["WAVE_HISTORY_SEC"], CONFIG["WAVE_MAX_Y"], "60s")

    def draw_wave2(self):
        color_filtered = color_manager.get_color('wave_line_filtered')
        color_raw = color_manager.get_color('wave_line_raw')
        data_list = [
            (self.wave2_data, color_filtered),
            (self.wave2_raw_data, color_raw)
        ]
        self._draw_wave_multi(self.canvas2, data_list,
                              CONFIG["WAVE2_HISTORY_SEC"], CONFIG["WAVE_MAX_Y"], "25min")

    def _draw_wave_multi(self, canvas, data_list, history_sec, y_max, title=""):
        canvas.delete("all")
        width = canvas.winfo_width()
        height = canvas.winfo_height()
        if width < 50 or height < 50:
            # 尺寸不足时，用短间隔重试，而非等待整个刷新周期
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
        canvas.create_line(self.wave_margin_left, self.wave_margin_top,
                           self.wave_margin_left, self.wave_margin_top + plot_h, fill=accent)
        for y_val in [0, 6, 12, 18, 24]:
            if y_val > y_max:
                continue
            y = self.wave_margin_top + plot_h - (y_val / y_max) * plot_h
            canvas.create_line(self.wave_margin_left - 3, y, self.wave_margin_left, y, fill=accent)
            canvas.create_text(self.wave_margin_left - 8, y, text=str(y_val), fill=accent,
                               font=("Arial", 8), anchor="e")

    def make_draggable(self):
        def start(e):
            self.drag_x = e.x
            self.drag_y = e.y
        def move(e):
            dx = e.x - self.drag_x
            dy = e.y - self.drag_y
            self.root.geometry(f"+{self.root.winfo_x() + dx}+{self.root.winfo_y() + dy}")
        self.root.bind("<Button-1>", start)
        self.root.bind("<B1-Motion>", move)

    def open_readme(self):
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
        if hasattr(self, '_crop_window') and self._crop_window is not None:
            try:
                self._crop_window.destroy()
            except:
                pass
            self._crop_window = None
            return

        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        left, top, right, bottom = self.region

        win = tk.Toplevel(self.root)
        self._crop_window = win
        win.attributes('-fullscreen', True)
        win.attributes('-topmost', True)
        win.attributes('-transparentcolor', 'black')
        win.configure(bg='black')
        win.overrideredirect(True)

        canvas = tk.Canvas(win, bg='black', highlightthickness=0, bd=0)
        canvas.pack(fill='both', expand=True)

        rect_id = canvas.create_rectangle(left, top, right, bottom, outline='red', width=3, fill='')
        drag_data = {"dragging": False, "start_x": 0, "start_y": 0}

        def on_press(event):
            if left <= event.x <= right and top <= event.y <= bottom:
                drag_data["dragging"] = True
                drag_data["start_x"] = event.x
                drag_data["start_y"] = event.y

        def on_drag(event):
            if not drag_data["dragging"]:
                return
            dx = event.x - drag_data["start_x"]
            dy = event.y - drag_data["start_y"]
            nonlocal left, top, right, bottom
            new_left = left + dx
            new_top = top + dy
            new_right = right + dx
            new_bottom = bottom + dy
            if new_left < 0:
                new_left = 0
                new_right = right - left
            if new_top < 0:
                new_top = 0
                new_bottom = bottom - top
            if new_right > screen_w:
                new_right = screen_w
                new_left = screen_w - (right - left)
            if new_bottom > screen_h:
                new_bottom = screen_h
                new_top = screen_h - (bottom - top)
            canvas.coords(rect_id, new_left, new_top, new_right, new_bottom)
            left, top, right, bottom = new_left, new_top, new_right, new_bottom
            drag_data["start_x"] = event.x
            drag_data["start_y"] = event.y

        def on_release(event):
            if drag_data["dragging"]:
                drag_data["dragging"] = False
                self.region = (left, top, right, bottom)
                CONFIG["CROP_REGION"] = self.region
                keep_h = self.region[3] - self.region[1]
                keep_w = self.region[2] - self.region[0]
                self.frame_shape = (int(keep_h * CONFIG["SCALE_FACTOR"]),
                                    int(keep_w * CONFIG["SCALE_FACTOR"]))
                self._save_all_settings()

        def smart_press(event):
            if left <= event.x <= right and top <= event.y <= bottom:
                on_press(event)
            else:
                win.destroy()
                self._crop_window = None

        canvas.bind("<Button-1>", smart_press)
        canvas.bind("<B1-Motion>", on_drag)
        canvas.bind("<ButtonRelease-1>", on_release)
        win.bind("<Escape>", lambda e: (win.destroy(), setattr(self, '_crop_window', None)))
        canvas.create_text(screen_w // 2, 30, text="拖动矩形调整截图区域，点击外部关闭", fill="white",
                           font=("Arial", 12))

    def _save_all_settings(self):
        settings = {
            "config": CONFIG,
            "use_optical_flow": self.use_optical_flow,
            "use_hash_filter": self.use_hash_filter,
            "layouts": self.layouts,
            "active_layout": self.active_layout
        }
        path = self._get_settings_path()
        with open(path, "w") as f:
            json.dump(settings, f, indent=2)

    # ---------- 设置窗口总张数波形（已移除缩放过滤占比） ----------
    def _draw_settings_wave(self):
        if not hasattr(self, 'settings_wave_canvas') or self.settings_wave_canvas is None:
            return
        canvas = self.settings_wave_canvas
        if not canvas.winfo_exists():
            return

        history = list(self.total_history)
        width = canvas.winfo_width()
        height = canvas.winfo_height()
        canvas.delete("all")

        # 尺寸不足时使用短间隔重试，并取消后续依赖长间隔的调度
        if width < 50 or height < 50 or not history:
            if hasattr(self, '_settings_win') and self._settings_win is not None and self._settings_win.winfo_exists():
                self.root.after(100, self._draw_settings_wave)  # 改为100ms重试
            return

        times = [p[0] for p in history]
        raw = [p[1] for p in history]
        filtered = [p[2] for p in history]
        trans = [p[3] for p in history]
        flow = [p[4] for p in history]
        hashes = [p[5] for p in history]
        still = [p[6] for p in history]
        local = [p[7] for p in history]
        other_unknown = [p[8] for p in history]  # 索引由原来的9调整为8

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

        # ---------- 右下角：近5秒过滤类型占比堆叠柱状图（已移除缩放过滤） ----------
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
                '其他': last[8] - first[8]
            }
            total_inc = sum(inc.values())
            if total_inc > 0:
                inc_colors = {
                    '平移': color_manager.get_color('filter_translation'),
                    '光流': color_manager.get_color('filter_optical_flow'),
                    '哈希': color_manager.get_color('filter_hash'),
                    '静止': color_manager.get_color('filter_still'),
                    '局部': color_manager.get_color('filter_local'),
                    '其他': color_manager.get_color('filter_other')
                }
                bar_w = 80
                bar_h = 40
                bar_x = margin['left'] + plot_w + bar_w - 35
                bar_y = margin['top'] + plot_h - bar_h + 2
                canvas.create_rectangle(bar_x - 1, bar_y - 1, bar_x + bar_w + 1, bar_y + bar_h + 1,
                                        outline=accent)
                cum_y = bar_y + bar_h
                for key in ['平移', '光流', '哈希', '静止', '局部', '其他']:
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

        # 持续刷新
        if hasattr(self, '_settings_win') and self._settings_win is not None and self._settings_win.winfo_exists():
            refresh_ms = int(CONFIG["TOTAL_WAVE_REFRESH_SEC"] * 1000)
            self.root.after(refresh_ms, self._draw_settings_wave)

if __name__ == "__main__":
>>>>>>> 9fc274e7ae6af0036ee6d18f8b684bd055c603aa
    AnimeCelCounter()