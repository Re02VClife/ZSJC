# ====== Module: main.py ======
# ==============================================================================
# 项目名称：动漫张数检测系统
# 功能说明：dxcam捕获、多重过滤非新作画帧、实时预览、UI交互一体化工具
# 模块组成：config.py / widgets.py / detection.py / preview.py / ui.py / video_source.py / oped_detector.py / record_manager.py / main.py
# ==============================================================================
import cv2
print(cv2.cuda.getCudaEnabledDeviceCount())  # 输出 >0 表示支持 CUDA

cv2.setNumThreads(6)          # 调用cpu线程数
cv2.ocl.setUseOpenCL(False)   # 若 GPU 光流已手动管理，关闭 OpenCL 避免冲突
import sys, os, time, threading, json, copy, subprocess
from collections import deque
import csv
import cv2
import numpy as np
import tkinter as tk
import tkinter.messagebox as messagebox

# 添加自定义模块导入
from config import CONFIG, color_manager, get_settings_path, CONFIG_DEFAULT
from detection import ( compute_all_hashes,is_raw_change, basic_is_new_cel, full_is_new_cel,unified_motion_analysis)
from preview import PreviewManager
from video_source import FrameSource
from oped_detector import OPEDDetector
from record_manager import RecordManager
from ui import (build_main_ui, create_video_controls, create_settings_window,
               create_crop_window)
from config import get_all_colors
# dxcam 检查
try:
    import dxcam
    DX_AVAILABLE = True
except ImportError:
    DX_AVAILABLE = False

if not DX_AVAILABLE:
    raise ImportError("请安装 dxcam 库: pip install dxcam")

class AnimeCelCounter:
    """动画作画张数计数器主类，负责屏幕捕获、变化检测、统计与UI"""

    def __init__(self):
        import cProfile
        self.profiler = cProfile.Profile()
        self.profiler.enable()

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
        self.time_lock = threading.Lock()
        self.skip_until_elapsed = 0.0

        # 时钟锁，保护 _current_clock 读写
        self.clock_lock = threading.Lock()
        self._current_clock = 0.0

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
        self.start_real_time = time.time()

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

        self.instant_fps_history = deque(maxlen=3600)

        # ---------- OP/ED 检测器 ----------
        self.oped_detector = OPEDDetector(self)

        # ---------- 运行记录管理器 ----------
        self.record_manager = RecordManager(self)

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

        # ========== 视频模式与 GPU 初始化 ==========
        self.video_mode = CONFIG.get("VIDEO_MODE", False)
        self.video_file_path = CONFIG.get("VIDEO_FILE_PATH", "")
        self.use_gpu = CONFIG.get("GPU_ENABLED", False) and self._check_gpu_usable()
        self.frame_source = None
        self.video_controls = None
        self.video_clock = 0.0
        self.video_is_playing = True
        self.video_playback_speed = CONFIG.get("VIDEO_PLAYBACK_SPEED", 1.0)
        self._realtime_elapsed = 0.0
        self._last_capture_time = None

        # 创建 GPU 光流对象（如果启用且可用）
        self.lk_cuda = None
        if self.use_gpu:
            try:
                self.lk_cuda = cv2.cuda.SparsePyrLKOpticalFlow.create(winSize=(21,21), maxLevel=3, iters=30)
            except:
                self.use_gpu = False

        # 如果配置为视频模式，尝试初始化帧源
        if self.video_mode and self.video_file_path:
            try:
                self._init_video_source(self.video_file_path)
            except Exception as e:
                messagebox.showerror("视频初始化失败", f"无法打开视频文件：{e}，切换回实时模式。")
                self.video_mode = False
                CONFIG["VIDEO_MODE"] = False

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
    def _init_video_source(self, video_path):
        if self.frame_source:
            self.frame_source.close()
        self.frame_source = FrameSource(mode='video', video_path=video_path,
                                        scale_factor=CONFIG["SCALE_FACTOR"],
                                        use_gpu=self.use_gpu,
                                        hw_accel=CONFIG.get("VIDEO_HW_ACCEL", True),
                                        hash_enabled=(self.use_hash_filter or CONFIG.get("OPED_HASH_ENABLED", True)))
        self.frame_shape = self.frame_source.frame_shape
        self.video_mode = True

    def _compute_region_from_base(self, base_rect, ratio, screen_w, screen_h):
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
        return get_settings_path()

    def _check_gpu_usable(self):
        try:
            dummy = np.zeros((100, 100), dtype=np.uint8)
            unified_motion_analysis(dummy, dummy, use_optical_flow=True, use_gpu=True)
            return True
        except:
            return False

    def load_settings(self):
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
            # 加载颜色配置（新增）
            if "custom_colors" in data and CONFIG["USE_CUSTOM_COLORS"]:
                CONFIG["COLORS"].update(data["custom_colors"])
            color_manager._config = CONFIG["COLORS"]
        except (FileNotFoundError, json.JSONDecodeError):
            self.use_optical_flow = True
            self.use_hash_filter = True
            self.layouts = {}
            self.active_layout = None

    def _save_all_settings(self):
        settings = {
            "config": CONFIG,
            "use_optical_flow": self.use_optical_flow,
            "use_hash_filter": self.use_hash_filter,
            "layouts": self.layouts,
            "active_layout": self.active_layout,
            "USE_CUSTOM_COLORS": CONFIG["USE_CUSTOM_COLORS"],
            "custom_colors": get_all_colors() if CONFIG["USE_CUSTOM_COLORS"] else {}  # 新增
        }
        path = self._get_settings_path()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2, ensure_ascii=False)

    def make_draggable(self):
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
        canvas = self.btn_canvas
        canvas.delete("all")
        btn_w, btn_h, gap = 100, 22, 4
        x0, x1 = 5, 5 + btn_w
        pause_text = "暂停/继续" if self.is_running else "继续"
        buttons_info = [
            ("关闭", self._on_close),
            ("设置", self.open_settings),
            ("导入视频", self._import_video),
            (pause_text, self.pause),
            ("重置统计", self.reset_stats)
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

    def _import_video(self):
        from tkinter import filedialog
        file_path = filedialog.askopenfilename(filetypes=[("Video files", "*.mp4 *.avi *.mkv *.mov")])
        if not file_path:
            return
        try:
            if self.frame_source:
                self.frame_source.close()
            self.frame_source = FrameSource(mode='video', video_path=file_path,
                                            scale_factor=CONFIG["SCALE_FACTOR"],
                                            use_gpu=self.use_gpu,
                                            hw_accel=CONFIG.get("VIDEO_HW_ACCEL", True),
                                            hash_enabled=(self.use_hash_filter or CONFIG.get("OPED_HASH_ENABLED", True)))
            self.video_mode = True
            self.frame_shape = self.frame_source.frame_shape
            self.reset_stats()
            self.video_is_playing = True
            self.is_running = True
            self._run_event.set()
            CONFIG["VIDEO_MODE"] = True
            CONFIG["VIDEO_FILE_PATH"] = file_path
            self._realtime_elapsed = 0.0
            # 创建或更新视频控制条

        except Exception as e:
            messagebox.showerror("导入失败", f"无法打开视频文件：{e}")

    def open_settings(self):
        if self._settings_win is not None and self._settings_win.winfo_exists():
            self._settings_win.lift()
            return
        win = create_settings_window(self.root, self)
        self._settings_win = win

    def open_readme(self):
        import subprocess
        path = self.readme_path
        if not os.path.exists(path):
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
            messagebox.showinfo("说明文件", f"请手动打开文件：\n{path}")

    def show_crop_region(self):
        create_crop_window(self)

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

    # 设置窗口内容构建（保持原样，仅修改记录查看回调中的 load_records 调用，已正确）
    def _build_params_content(self, parent, win, panels):
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
            "OPED_WINDOW_SEC": "匹配窗口(s)",
            "OPED_MATCH_THRESHOLD": "匹配相关系数",
            "OPED_HISTORY_HOURS": "历史时长(h)",
            "OPED_SAMPLE_INTERVAL_SEC": "采样间隔(s)",
            "OPED_CLASSIFY_THRESHOLD": "归类阈值",
            "OPED_HASH_ENABLED": "启用哈希匹配",
            "OPED_HASH_SIZE": "哈希尺寸(8)",
            "OPED_HASH_MAX_DIST": "哈希最大距离",
            "OPED_HASH_WIN_RATIO": "哈希窗口占比",
            "OPED_ENTER_HAMMING": "OPED进入距离",
            "OPED_EXIT_HAMMING": "OPED退出距离",
            "OPED_ENTER_CONSEC": "OPED进入连续帧",
            "OPED_EXIT_CONSEC": "OPED退出连续帧",
            "OPED_MIN_DURATION": "OPED最短长度(s)",
            "OPED_SELF_EXCLUDE_SEC": "OPED自排除窗口(s)",
        }

        groups = [
            ("基本", ["REFRESH_INTERVAL", "SCALE_FACTOR", "SSIM_THRESHOLD", "JingzhiShiJian", "CROP_RATIO", "ALPHA"]),
            ("波形1", ["WAVE_HISTORY_SEC", "WAVE_REFRESH_MS"]),
            ("波形2", ["WAVE2_HISTORY_SEC", "WAVE2_REFRESH_MS", "WAVE_MAX_Y"]),
            ("变化检测", ["MIN_DIFF_THRESHOLD", "MAX_DIFF_THRESHOLD", "MIN_CHANGE_RATIO",
                          "ALIGNED_CHANGE_THRESHOLD", "SIGNIFICANT_CHANGE_RATIO"]),
            ("重复帧过滤", ["FRAME_BUFFER_SIZE", "HASH_THRESHOLD"]),
            ("总张数波形", ["TOTAL_WAVE_REFRESH_SEC"]),
            ("动态过滤触发", ["FILTER_TRIGGER_WINDOW_MS", "FILTER_TRIGGER_COUNT", "FULL_FILTER_HOLD_SEC"]),
            ("基础检测常量", ["BASIC_CORR_THRESHOLD", "BASIC_MIN_RAW_RATIO_STILL"]),
            ("完整检测常量", ["FULL_CORR_THRESHOLD", "FULL_STILL_RATIO"]),
            ("局部运动判断", ["LOCAL_AREA_THRESH", "LOCAL_BBOX_RATIO_MAX", "LOCAL_ASPECT_RATIO_MAX"]),
            ("光流图层分离", ["LAYER_MIN_VALID_POINTS", "LAYER_MIN_MOVING_POINTS",
                              "LAYER_DIRECTION_CONSISTENCY", "LAYER_MEAN_VEC_MIN", "LAYER_COS_SIM_THRESH"]),
            ("光流法", ["FLOW_FEATURE_COUNT", "FLOW_QUALITY_LEVEL", "FLOW_MIN_DISTANCE","FLOW_LAYER_STATIC_THRESH"]),
            ("缩放运镜", ["ZOOM_DIRECTION_CONSISTENCY", "ZOOM_RADIAL_CORRELATION"]),
            ("预览参数", ["PREVIEW_DENSE_ALPHA", "PREVIEW_DIFF_DECAY", "PREVIEW_MOTION_DECAY", "PREVIEW_MOTION_MAX_SPEED"]),
            ("OP/ED检测", ["OPED_DETECTION_ENABLED", "OPED_WINDOW_SEC", "OPED_MATCH_THRESHOLD",
                           "OPED_HISTORY_HOURS", "OPED_SAMPLE_INTERVAL_SEC", "OPED_CLASSIFY_THRESHOLD"]),
            ("OP/ED哈希匹配", ["OPED_HASH_ENABLED", "OPED_HASH_SIZE", "OPED_HASH_MAX_DIST",
                               "OPED_HASH_WIN_RATIO", "OPED_ENTER_HAMMING", "OPED_EXIT_HAMMING",
                               "OPED_ENTER_CONSEC", "OPED_EXIT_CONSEC", "OPED_MIN_DURATION",
                               "OPED_SELF_EXCLUDE_SEC"]),
        ]

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

        for idx, (group_name, keys) in enumerate(groups):
            target = left_frame if idx % 2 == 0 else right_frame
            row = target.grid_size()[1]
            outer = tk.Frame(target, bg=bg)
            outer.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(8, 2))
            title_frame = tk.Frame(outer, bg=bg)
            title_frame.pack(fill="x")
            expanded = tk.BooleanVar(value=True)
            fold_btn = tk.Button(title_frame, text="[−]", font=("Arial", 10), width=3,
                                 bg=btn_bg, fg=accent, bd=0, relief="flat",
                                 activebackground=btn_bg, activeforeground=accent)
            fold_btn.pack(side="left", padx=(0, 5))
            tk.Label(title_frame, text=group_name, font=("Arial", 10, "bold"),
                     fg=accent, bg=bg).pack(side="left")
            content_frame = tk.Frame(outer, bg=bg)
            content_frame.pack(fill="x", padx=(10, 0))

            def make_toggle(fr, btn, var):
                def toggle():
                    if var.get():
                        fr.pack_forget()
                        btn.config(text="[+]")
                        var.set(False)
                    else:
                        fr.pack(fill="x", padx=(10, 0))
                        btn.config(text="[−]")
                        var.set(True)
                return toggle
            fold_btn.config(command=make_toggle(content_frame, fold_btn, expanded))

            c_row = 0
            for key in keys:
                if key == "CROP_RATIO":
                    tk.Label(content_frame, textvariable=self.crop_ratio_label_var, fg=accent, bg=bg,
                             font=("Arial", 9)).grid(row=c_row, column=0, sticky="w", padx=5)
                else:
                    label_text = param_names.get(key, key) + ":"
                    tk.Label(content_frame, text=label_text, fg=accent, bg=bg,
                             font=("Arial", 9)).grid(row=c_row, column=0, sticky="w", padx=(0, 2))
                default_val = CONFIG.get(key, "")
                if isinstance(default_val, bool):
                    var = tk.BooleanVar(value=default_val)
                    cb = tk.Checkbutton(content_frame, variable=var,
                                        fg=accent, bg=bg, selectcolor=btn_bg,
                                        activebackground=bg, activeforeground=accent)
                    cb.grid(row=c_row, column=1, sticky="w", padx=(2, 0))
                    entries[key] = var
                else:
                    var = tk.StringVar(value=str(default_val))
                    ent = tk.Entry(content_frame, textvariable=var, width=5,
                                   font=("Arial", 9), bg=btn_bg, fg=accent,
                                   insertbackground=accent)
                    ent.grid(row=c_row, column=1, sticky="e", padx=(2, 0))
                    entries[key] = var
                c_row += 1

        # 功能开关
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
                       fg=accent, bg=bg, selectcolor=btn_bg).grid(row=row_left, column=0, columnspan=2, sticky="w", padx=5)
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

        # 布局管理
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

        # 运行记录管理
        tk.Label(right_frame, text="运行记录管理", font=("Arial", 10, "bold"),
                 fg=accent, bg=bg).grid(row=row_right, column=0, columnspan=2, sticky="w", pady=(10, 2))
        row_right += 1
        record_listbox = tk.Listbox(right_frame, bg=btn_bg, fg=accent, height=6, selectmode=tk.SINGLE)
        self.record_listbox = record_listbox
        record_listbox.grid(row=row_right, column=0, columnspan=2, sticky="we", padx=5)
        row_right += 1

        btn_frame_records = tk.Frame(right_frame, bg=bg)
        btn_frame_records.grid(row=row_right, column=0, columnspan=2, pady=5)
        row_right += 1

        def _refresh_record_list():
            if self.record_listbox.winfo_exists():
                self.record_listbox.delete(0, tk.END)
                records = self.record_manager.load_records()
                for rec in records:
                    display = f"{rec['name']}  |  {rec.get('record_time', '')}"
                    self.record_listbox.insert(tk.END, display)

        def load_record_list():
            _refresh_record_list()

        def view_record():
            sel = record_listbox.curselection()
            if not sel:
                return
            records = self.record_manager.load_records()
            idx = sel[0]
            if idx >= len(records):
                return
            rec = records[idx]
            self.record_manager.show_record_detail(rec, idx)

        def edit_record():
            sel = record_listbox.curselection()
            if not sel:
                return
            records = self.record_manager.load_records()
            idx = sel[0]
            if idx >= len(records):
                return
            rec = records[idx]
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
                    records[idx]['total_filtered_cels'] = new_cels
                    records[idx]['duration_sec'] = new_time
                    records[idx]['avg_fps_filtered'] = new_cels / new_time if new_time > 0 else 0
                    self.record_manager.save_records(records)
                    load_record_list()
                    edit_win.destroy()
                except ValueError:
                    messagebox.showerror("输入错误", "请输入有效的数字")
            tk.Button(edit_win, text="保存", command=save_changes,
                      bg=btn_bg, fg=accent).grid(row=3, column=0, columnspan=2, pady=10)
            edit_win.transient(win)
            edit_win.grab_set()

        def delete_record():
            sel = record_listbox.curselection()
            if not sel:
                return
            records = self.record_manager.load_records()
            idx = sel[0]
            if idx >= len(records):
                return
            if messagebox.askyesno("确认删除", "确定删除该记录吗？"):
                self.record_manager.delete_record(idx)
                load_record_list()

        tk.Button(btn_frame_records, text="查看", command=view_record,
                  bg=btn_bg, fg=accent).pack(side="left", padx=2)
        tk.Button(btn_frame_records, text="编辑", command=edit_record,
                  bg=btn_bg, fg=accent).pack(side="left", padx=2)
        tk.Button(btn_frame_records, text="删除", command=delete_record,
                  bg=btn_bg, fg=accent).pack(side="left", padx=2)
        tk.Button(btn_frame_records, text="刷新", command=load_record_list,
                  bg=btn_bg, fg=accent).pack(side="left", padx=2)
        load_record_list()

        # 保存 / 恢复默认设置函数
        def save_settings():
            for key, var in entries.items():
                if isinstance(var, tk.BooleanVar):
                    CONFIG[key] = var.get()
                else:
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
            # 更新区域
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

        def on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        canvas.bind_all("<MouseWheel>", on_mousewheel)

        top_btn_frame = tk.Frame(parent, bg=bg)
        top_btn_frame.pack(side="top", fill="x", before=canvas, pady=(5, 0), padx=5)
        row1 = tk.Frame(top_btn_frame, bg=bg)
        row1.pack(fill="x", pady=(0, 2))
        tk.Button(row1, text="保存", command=save_settings,
                  bg=btn_bg, fg=accent).pack(side="left", padx=3)
        tk.Button(row1, text="恢复默认", command=reset_to_default,
                  bg=btn_bg, fg=accent).pack(side="left", padx=3)
        tk.Button(row1, text="说明", command=self.open_readme,
                  bg=btn_bg, fg=accent).pack(side="left", padx=3)
        self.switch_btn = tk.Button(row1, text="切换到视频" if not self.video_mode else "切换到实时",
                                    command=self.toggle_video_mode,
                                    bg=btn_bg, fg=accent)
        self.switch_btn.pack(side="left", padx=3)
        tk.Button(row1, text="显示截图范围", command=self.show_crop_region, bg=btn_bg, fg=accent).pack(side="left", padx=3)
        tk.Button(row1, text="保存运行记录", command=self.record_manager._save_record,
                  bg=btn_bg, fg=accent).pack(side="left", padx=3)
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

    def _on_preview_toggle(self, state):
        if state:
            self.preview_manager.start()
        else:
            self.preview_manager.stop()

    def _get_screen_dxcam(self):
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

    # ==================== 核心捕获与检测循环====================
    def _capture_loop(self):
        target_fps = 48
        next_frame_time = time.perf_counter()
        oped_sample_interval = CONFIG["OPED_SAMPLE_INTERVAL_SEC"]

        while not self._stop_event.is_set():
            if not self._run_event.is_set():
                self._run_event.wait(timeout=0.05)
                continue
            if self._stop_event.is_set():
                break

            # ---------- 帧率控制（仅实时模式）----------
            if not self.video_mode:
                # 实时模式才限制帧率
                now = time.perf_counter()
                if now < next_frame_time:
                    sleep_time = next_frame_time - now
                    if sleep_time > 0.002:
                        time.sleep(sleep_time * 0.8)
                    while time.perf_counter() < next_frame_time:
                        pass
            # 视频模式：不做任何等待，全速处理


            hash_256 = oped_hash = None

            if self.video_mode and self.frame_source is not None:
                frame_data = self.frame_source.next_frame()
                if frame_data is None or frame_data[0] is None:
                    time.sleep(0.001)
                    continue
                ts, now_frame, bgr_raw, v_hash_256, v_oped_hash = frame_data
                # 使用帧源提供的哈希，不再重复计算
                hash_256 = v_hash_256 if self.use_hash_filter else None
                oped_hash = v_oped_hash if CONFIG.get("OPED_HASH_ENABLED", True) else None
                if ts is None or ts < 0:
                    ts = 0.0
                if not self.video_is_playing:
                    time.sleep(0.001)
                    continue
                with self.clock_lock:
                    self._current_clock = ts
            else:
                self.video_mode = False
                frame = self.camera.get_latest_frame()
                if frame is None:
                    with self.lock:
                        last = self.last_frame.copy() if self.last_frame is not None else np.zeros(self.frame_shape, dtype=np.uint8)
                    now_frame = last
                else:
                    l, t, r, b = self.region
                    frame = frame[t:b, l:r]
                    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                    now_frame = cv2.resize(gray, (0, 0), fx=CONFIG["SCALE_FACTOR"], fy=CONFIG["SCALE_FACTOR"])
                ts = time.time()
                if not hasattr(self, '_last_capture_time') or self._last_capture_time is None:
                    self._last_capture_time = ts
                    self._realtime_elapsed = 0.0
                dt = ts - self._last_capture_time
                self._last_capture_time = ts
                self._realtime_elapsed += dt
                with self.clock_lock:
                    self._current_clock = self._realtime_elapsed
                # 实时模式统一计算哈希（仅一次）
                if self.use_hash_filter or CONFIG.get("OPED_HASH_ENABLED", True):
                    hash_256, oped_hash = compute_all_hashes(now_frame)

            # 获取上一帧
            with self.lock:
                last = self.last_frame.copy() if self.last_frame is not None else None
            if last is not None and last.shape != now_frame.shape:
                last = None

            new_cel = 0
            raw_new_cel = 0
            move_type = "静止"

            if last is not None:
                raw_detected = is_raw_change(last, now_frame)
                raw_new_cel = 1 if raw_detected else 0

                basic_detected, move_type = basic_is_new_cel(last, now_frame)
                if basic_detected:
                    self.raw_detection_timestamps.append(self._current_clock)

                window_sec = CONFIG["FILTER_TRIGGER_WINDOW_MS"] / 1000.0
                while self.raw_detection_timestamps and self.raw_detection_timestamps[0] < self._current_clock - window_sec:
                    self.raw_detection_timestamps.popleft()

                if len(self.raw_detection_timestamps) >= CONFIG["FILTER_TRIGGER_COUNT"]:
                    if not self.full_filter_active:
                        self.full_filter_active = True
                    self.full_filter_active_until = self._current_clock + CONFIG["FULL_FILTER_HOLD_SEC"]
                elif self.full_filter_active and self._current_clock > self.full_filter_active_until:
                    self.full_filter_active = False

                if self.full_filter_active:
                    final_detected, move_type = full_is_new_cel(last, now_frame, self.use_optical_flow, use_gpu=self.use_gpu, lk_cuda=self.lk_cuda)
                else:
                    final_detected = basic_detected

                duplicate = False
                if self.use_hash_filter and hash_256 is not None:
                    with self.lock:
                        buf = list(self.frame_buffer)
                        for h in buf[:-1]:
                            if np.sum(hash_256 != h) <= self.hash_threshold:
                                duplicate = True
                                break
                        self.frame_buffer.append(hash_256)

                if not duplicate and final_detected:
                    new_cel = 1

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
            else:
                if self.use_hash_filter and hash_256 is not None:
                    with self.lock:
                        self.frame_buffer.append(hash_256)

            if new_cel:
                self.total_cels_count += 1
                if not self._has_first_frame:
                    self._has_first_frame = True
            if raw_new_cel:
                self.total_raw_cels_count += 1

            with self.lock:
                self.last_frame = now_frame
                if not self._has_first_frame:
                    self._has_first_frame = True

            self.preview_cache = (last, now_frame.copy() if now_frame is not None else None)

            clock = self._current_clock  # 局部快照
            self.recent_frames.append((clock, new_cel))
            self.recent_sum += new_cel
            while self.recent_frames and clock - self.recent_frames[0][0] > 1.0:
                old = self.recent_frames.popleft()
                self.recent_sum -= old[1]

            self.recent_frames_raw.append((clock, raw_new_cel))
            self.recent_sum_raw += raw_new_cel
            while self.recent_frames_raw and clock - self.recent_frames_raw[0][0] > 1.0:
                old = self.recent_frames_raw.popleft()
                self.recent_sum_raw -= old[1]

            span = clock - self.recent_frames[0][0] if self.recent_frames else 0.001
            cels = self.recent_sum / max(span, 0.001) if self.recent_frames else 0.0
            span_raw = clock - self.recent_frames_raw[0][0] if self.recent_frames_raw else 0.001
            cels_raw = self.recent_sum_raw / max(span_raw, 0.001) if self.recent_frames_raw else 0.0

            with self.lock:
                self.current_cels = cels
                self.current_raw_cels = cels_raw
                self.last_move_type = move_type if last is not None else "静止"

            if CONFIG["OPED_DETECTION_ENABLED"] and clock - self.oped_detector.last_sample_time >= oped_sample_interval:
                with self.lock:
                    fc = self.current_cels
                    rc = self.current_raw_cels
                self.oped_detector.sample(clock, fc, rc, oped_hash)
                self.instant_fps_history.append((clock, self.current_cels))

            # 视频播放倍速简单实现：通过 sleep 控制帧率
            if self.video_mode and self.video_is_playing:
                # 基于视频时间戳计算睡眠时间，实现倍速
                # 简化：每帧后 sleep 一段时间
                # 实际应该计算目标播放速度下的帧间隔，但这里仅示例
                pass

    # ==================== UI 更新与波形 ====================
    def loop(self):
        if self.is_running:
            now_real = time.time()
            with self.clock_lock:
                current_clock = self._current_clock
            self.last_loop_time = now_real

            if not hasattr(self, '_last_recorded_time'):
                self._last_recorded_time = -1
            if current_clock - self._last_recorded_time >= 1.0:
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
                self.total_history.append((current_clock, total_r, total_f,
                                           trans_f, flow_f, hash_f,
                                           still_f, local_f, other_f, zoom_f))
                self._last_recorded_time = current_clock

            with self.lock:
                current_total = self.total_cels_count
                has_frame = self._has_first_frame

            net_cels = current_total - self.oped_detector.deducted_cels
            net_time = current_clock - self.oped_detector.deducted_time

            if has_frame:
                if net_cels != self._prev_cels_count:
                    self.idle_start_time = None
                elif self.idle_start_time is None:
                    self.idle_start_time = current_clock
                elif current_clock - self.idle_start_time >= CONFIG["JingzhiShiJian"]:
                    self.pause()
            self._prev_cels_count = net_cels

            if net_cels != self._last_disp_cels:
                self.lb_total_cels.config(text=f"总张数：{net_cels}")
                self._last_disp_cels = net_cels

            if now_real - self.last_refresh >= CONFIG["REFRESH_INTERVAL"]:
                with self.lock:
                    real = self.current_cels
                self.total_sum += real
                self.total_count += 1

                avg_net = net_cels / net_time if net_time > 0 else 0.0

                self.lb_rt.config(text=f"实时张数：{real:.1f} ")
                self.lb_total_time.config(text=f"运行时长：{net_time:.1f} s")
                self.lb_total_cels.config(text=f"总张数：{net_cels}")
                self.lb_avg.config(text=f"总平均：{avg_net:.1f}")
                status = self.get_status(real)
                self.lb_st.config(text=status)
                self.last_refresh = now_real
                self._last_disp_cels = current_total

            if self.video_mode and self.video_controls:
                self._update_video_controls()

        self.root.after(20, self.loop)

    def _update_video_controls(self):
        if not self.video_controls or self.frame_source is None:
            return
        current_time, duration, cur_frame, total_frames = self.frame_source.get_progress()
        progress = (current_time / duration * 100) if duration > 0 else 0
        self.video_controls['progress_var'].set(progress)
        time_str = f"{self._format_time(current_time)}/{self._format_time(duration)}"
        self.video_controls['time_label'].config(text=time_str)

    def toggle_video_mode(self):
        """在实时模式和视频导入模式之间切换"""
        if self.video_mode:
            # 从视频切回实时
            self.video_mode = False
            CONFIG["VIDEO_MODE"] = False
            CONFIG["VIDEO_FILE_PATH"] = ""
            if self.frame_source:
                self.frame_source.close()
                self.frame_source = None
            self.reset_stats()  # 重置统计并重启摄像头
            self.video_is_playing = True
            self.is_running = True
            self._run_event.set()
            # 如果存在视频控制条，隐藏它
            if self.video_controls and self.video_controls['frame'].winfo_exists():
                self.video_controls['frame'].pack_forget()
            self._save_all_settings()
            # 更新按钮文字
            if hasattr(self, 'switch_btn') and self.switch_btn.winfo_exists():
                self.switch_btn.config(text="切换到视频")
        else:
            # 从实时切换到视频（复用原有的导入逻辑）
            from tkinter import filedialog
            file_path = filedialog.askopenfilename(
                filetypes=[("Video files", "*.mp4 *.avi *.mkv *.mov")])
            if not file_path:
                return
            try:
                if self.frame_source:
                    self.frame_source.close()
                self.frame_source = FrameSource(
                    mode='video', video_path=file_path,
                    scale_factor=CONFIG["SCALE_FACTOR"],
                    use_gpu=self.use_gpu,
                    hw_accel=CONFIG.get("VIDEO_HW_ACCEL", True),
                    hash_enabled=(self.use_hash_filter or CONFIG.get("OPED_HASH_ENABLED", True))
                )
                self.video_mode = True
                CONFIG["VIDEO_MODE"] = True
                CONFIG["VIDEO_FILE_PATH"] = file_path
                self.frame_shape = self.frame_source.frame_shape
                self.reset_stats()
                self.video_is_playing = True
                self.is_running = True
                self._run_event.set()
                self._realtime_elapsed = 0.0
                self._save_all_settings()
                # 更新按钮文字
                if hasattr(self, 'switch_btn') and self.switch_btn.winfo_exists():
                    self.switch_btn.config(text="切换到实时")
            except Exception as e:
                messagebox.showerror("导入失败", f"无法打开视频文件：{e}")
    def _format_time(self, seconds):
        m, s = divmod(int(seconds), 60)
        return f"{m:02d}:{s:02d}"

    def _on_seek(self, value):
        if self.frame_source is None or not self.video_mode:
            return
        total_dur = self.frame_source.video_duration
        pos = float(value) / 100.0 * total_dur
        self.frame_source.seek(pos)
        # Seek 后重置状态，避免统计错乱
        self.reset_stats()
        self._last_recorded_time = -1
        with self.lock:
            self.last_frame = None
            self.recent_frames.clear()
            self.recent_sum = 0
            self.recent_frames_raw.clear()
            self.recent_sum_raw = 0
            self.current_cels = 0.0
            self.current_raw_cels = 0.0

    def _on_speed_change(self, value):
        speed = float(value.replace('x', ''))
        CONFIG["VIDEO_PLAYBACK_SPEED"] = speed
        self.video_playback_speed = speed

    def pause_video(self):
        self.video_is_playing = False
        self.is_running = False
        self._run_event.clear()
        if self.frame_source:
            self.frame_source.pause()
        if self.video_controls:
            self.video_controls['play_var'].set(False)
            self.video_controls['play_btn'].config(text="▶")

    def resume_video(self):
        self.video_is_playing = True
        self.is_running = True
        self._run_event.set()
        self.last_loop_time = time.time()
        if self.frame_source:
            self.frame_source.resume()
        if self.video_controls:
            self.video_controls['play_var'].set(True)
            self.video_controls['play_btn'].config(text="⏸")

    def _save_gpu_video_config(self, gpu_enabled, speed_str):
        CONFIG["GPU_ENABLED"] = gpu_enabled
        try:
            CONFIG["VIDEO_PLAYBACK_SPEED"] = float(speed_str)
        except:
            pass
        self._save_all_settings()

    def pause(self):
        self.is_running = not self.is_running
        if self.is_running:
            self._run_event.set()
            self.last_loop_time = time.time()
            # 重置捕获时间戳，防止时间跳跃
            self._last_capture_time = time.time()
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
        with self.clock_lock:
            self._current_clock = 0.0
        self.last_loop_time = time.time()
        self._realtime_elapsed = 0.0
        self._last_capture_time = None
        self.start_real_time = time.time()
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

        self.oped_detector.reset()
        self.instant_fps_history.clear()

        with self.time_lock:
            self.skip_until_elapsed = 0.0

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
            if not self.video_mode:
                try:
                    self.camera.release()
                except:
                    pass
                self.camera = dxcam.create(output_color="BGR")
                self.camera.start(target_fps=0, video_mode=False)

        if self.video_mode and self.frame_source:
            self.frame_source.seek(0)
            self.video_clock = 0.0

    def _save_profile(self):
        try:
            self.profiler.disable()
            self.profiler.dump_stats("profile_output.prof")
            print("[Profiler] 数据已保存至 profile_output.prof")
        except:
            pass

    def _on_close(self):
        self._save_profile()
        self._stop_event.set()
        self._run_event.set()
        self.preview_manager.stop()
        if self.frame_source:
            self.frame_source.close()
        if hasattr(self, 'capture_thread') and self.capture_thread.is_alive():
            self.capture_thread.join(timeout=2.0)
        if hasattr(self.preview_manager, 'thread') and self.preview_manager.thread and self.preview_manager.thread.is_alive():
            self.preview_manager.thread.join(timeout=1.0)
        self.root.destroy()

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
        elif mt in ("新作画", "新作画(基础)"):
            if v < 5.0:   return base + "微动"
            elif v < 8.0: return base + "一拍三"
            elif v < 13.0: return base + "一拍二"
            elif v < 20.0: return base + "一拍一"
            else:          return base + "全动画"
        else:
            if v < 0.3:    return base + "极低变化"
            elif v < 1.0:  return base + "极微变化"
            elif v < 5.0:  return base + "微动"
            elif v < 8.0:  return base + "一拍三"
            elif v < 13.0: return base + "一拍二"
            elif v < 20.0: return base + "高频作画"

    def update_wave(self):
        if self.is_running:
            with self.lock:
                cels = self.current_cels
                raw_cels = self.current_raw_cels
            clock = self._current_clock  # 锁外读，但更新频繁，轻微不一致可接受
            self.wave_data.append((clock, cels))
            self.wave_raw_data.append((clock, raw_cels))
        self.draw_wave()
        self.root.after(CONFIG["WAVE_REFRESH_MS"], self.update_wave)

    def update_wave2(self):
        if self.is_running:
            with self.lock:
                raw = self.current_cels
                raw_raw = self.current_raw_cels
            self.smooth_cels = 0.2 * raw + 0.8 * self.smooth_cels
            self.smooth_raw_cels = 0.2 * raw_raw + 0.8 * self.smooth_raw_cels
            clock = self._current_clock
            self.wave2_data.append((clock, self.smooth_cels))
            self.wave2_raw_data.append((clock, self.smooth_raw_cels))
        self.draw_wave2()
        self.root.after(CONFIG["WAVE2_REFRESH_MS"], self.update_wave2)

    def draw_wave(self):
        color_filtered = color_manager.get_color('wave_line_filtered')
        color_raw = color_manager.get_color('wave_line_raw')
        data_list = [
            (self.wave_raw_data, color_raw),
            (self.wave_data, color_filtered)
        ]
        self._draw_wave_multi(self.canvas, data_list,
                              CONFIG["WAVE_HISTORY_SEC"], CONFIG["WAVE_MAX_Y"], "60s")

    def draw_wave2(self):
        color_filtered = color_manager.get_color('wave_line_filtered')
        color_raw = color_manager.get_color('wave_line_raw')
        data_list = [
            (self.wave2_raw_data, color_raw),
            (self.wave2_data, color_filtered)
        ]
        self._draw_wave_multi(self.canvas2, data_list,
                              CONFIG["WAVE2_HISTORY_SEC"], CONFIG["WAVE_MAX_Y"], "25min")

    def _draw_wave_multi(self, canvas, data_list, history_sec, y_max, title=""):
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
        now = self._current_clock
        start = now - history_sec
        accent = color_manager.get_color('accent')
        self._draw_axes_only(canvas, plot_w, plot_h, y_max, accent)
        if title:
            canvas.create_text(self.wave_margin_left, self.wave_margin_top - 5,
                               text=title, fill=accent, font=("Arial", 8), anchor="nw")

        if CONFIG["OPED_DETECTION_ENABLED"]:
            mark_color = "#FFD700"
            start_t = now - history_sec
            seg_colors = {t['id']: t['color'] for t in self.oped_detector.templates}
            for s, e, sid in self.oped_detector.segment_occurrences:
                if e >= start_t and s <= now:
                    x1 = self.wave_margin_left + plot_w * (max(s, start_t) - start_t) / history_sec
                    x2 = self.wave_margin_left + plot_w * (min(e, now) - start_t) / history_sec
                    color = seg_colors.get(sid, mark_color)
                    canvas.create_rectangle(x1, self.wave_margin_top, x2, self.wave_margin_top + plot_h,
                                            fill=color, outline="", stipple="gray25")
            if self.oped_detector.match_active and self.oped_detector.match_start is not None:
                current_duration = now - self.oped_detector.match_start
                if current_duration >= 10:
                    x1 = self.wave_margin_left + plot_w * (max(self.oped_detector.match_start, start_t) - start_t) / history_sec
                    x2 = self.wave_margin_left + plot_w * (min(now, now) - start_t) / history_sec
                    color = seg_colors.get(self.oped_detector.pending_id, '#FFD700') if self.oped_detector.pending_id is not None else '#FFD700'
                    canvas.create_rectangle(x1, self.wave_margin_top, x2, self.wave_margin_top + plot_h,
                                            fill=color, outline="", stipple="gray25")

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