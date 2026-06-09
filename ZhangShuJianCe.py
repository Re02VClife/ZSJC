#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ==============================================================================
# 项目名称：动漫张数检测系统
# 功能说明：dxcam捕获、多重过滤非新作画帧、实时预览、UI交互一体化工具
# 模块组成：config.py / widgets.py / detection.py / preview.py / video_source.py / oped_detector.py / record_manager.py / ui.py / main.py
#
# 注意：以下为本项目目前合并后的完整代码，
# 若无确认需求，请先向我提问以确认，确认核心框架，并帮我思考可能遗漏的方面，不要直接编写代码；
# 若已经确认了方向，请根据要求对代码进行修改，对于修改的地方，请描述其所在的类和方法，若改动较大则直接给出修改后的完整类或方法，并加上注释
# 对于要改动或新增的代码，请附带其在原代码位置的上下两行代码方便我进行查找
# 如非必要，原代码的注释没什么问题就不要省略，保持原样即可
# ==============================================================================

# Auto-merged file from multiple modules
import sys, os, time, threading, json, copy, subprocess
from collections import deque
import cv2
import numpy as np
import tkinter as tk

# --- Begin merged modules ---

# ====== Module: config.py ======
# config.py
import copy
import os
import sys
import tkinter as tk

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
                elif isinstance(widget, tk.Button):
                    widget.configure(fg=fg, bg=self._config.get('btn_bg', '#222222'))
            except Exception:
                pass

    def clear(self):
        self._widget_color_map.clear()
        self._widgets_to_update.clear()

color_manager = None


# ==================== 配置参数 ====================
CONFIG = {


    # ========== 基本显示与控制 ==========
    "SHOW_INFO_PANEL": True,            # 显示实时数据面板
    "SHOW_WAVE1": True,                 # 显示波形1
    "SHOW_WAVE2": True,                 # 显示波形2
    "REFRESH_INTERVAL": 0.5,            # UI 标签刷新间隔（秒）
    "SCALE_FACTOR": 0.5,                # 截图缩放比例
    "SSIM_THRESHOLD": 0.93,             # 背景相似度阈值（高于阈值则过滤）
    "WAVE_HISTORY_SEC": 60,             # 波形1历史时长（秒）
    "WAVE_REFRESH_MS": 200,             # 波形1刷新间隔（毫秒）
    "WAVE2_HISTORY_SEC": 1500,          # 波形2历史时长（秒）
    "WAVE2_REFRESH_MS": 3000,           # 波形2刷新间隔（毫秒）
    "JingzhiShiJian": 60,               # 静止自动暂停（秒）
    "WAVE_MAX_Y": 24,                   # 波形Y轴最大值（张/秒）
    "CROP_RATIO": 0.8,                  # 截取屏幕中心区域比例
    "CROP_REGION": [100,120,1800,1000], # 手动截图区域（left, top, right, bottom），None则自动计算
    "ALPHA": 1,                         # 窗口透明度（0.0~1.0）


    # ========== 图像处理阈值 ==========
    "MIN_DIFF_THRESHOLD": 5,            # 极暗场景下的最低差分阈值
    "MAX_DIFF_THRESHOLD": 30,           # 亮场景下的最高差分阈值
    "MIN_CHANGE_RATIO": 0.002,          # 最小变化像素占比（原始检测/基础检测共用）

    # ========== 哈希与重复帧过滤 ==========
    "FRAME_BUFFER_SIZE": 24,            # 哈希缓冲区大小（帧数）
    "HASH_THRESHOLD": 0,                # 汉明距离阈值，≤此值视为相同帧

    # ========== 波形相关 ==========
    "TOTAL_WAVE_REFRESH_SEC": 2,        # 设置界面总张数波形刷新间隔（秒）

    # ========== 检测核心参数 ==========
    "SIGNIFICANT_CHANGE_RATIO": 0.02,   # 变化面积大于此值视为新作画（大面积直接通过）
    "ALIGNED_CHANGE_THRESHOLD": 0.02,   # 全局平移对齐后剩余变化面积上限（低于此值视为纯平移运镜）
    "BASIC_CORR_THRESHOLD": 0.995,      # 基础检测快速相似度阈值（高于此值且变化极小则判静止）
    "BASIC_MIN_RAW_RATIO_STILL": 0.004, # 基础检测极慢平移/静止的最小变化面积，需要大于 MIN_CHANGE_RATIO=0.002
    "FILTER_TRIGGER_WINDOW_MS": 200,    # 动态过滤触发窗口（毫秒）
    "FILTER_TRIGGER_COUNT": 3,          # 窗口内基础检测新张数达到此值触发完整过滤
    "FULL_FILTER_HOLD_SEC": 5,          # 触发后保持完整过滤的秒数

    "FULL_CORR_THRESHOLD": 0.998,       # 完整检测快速相似度阈值
    "FULL_STILL_RATIO": 0.002,           # 完整检测静止变化面积阈值

    # ========== 局部运动判断 ==========
    "LOCAL_AREA_THRESH": 0.003,         # 局部运动判断：最大连通域面积占比下限
    "LOCAL_BBOX_RATIO_MAX": 0.8,        # 局部运动判断：变化点包围盒面积占比上限
    "LOCAL_ASPECT_RATIO_MAX": 8,        # 局部运动判断：最大连通域宽高比上限

    # ========== 光流图层分离检测 ==========
    "LAYER_MIN_VALID_POINTS": 15,       # 最小有效特征点数（全局平移估计与图层分离共用）
    "LAYER_MIN_MOVING_POINTS": 10,      # 图层分离：最小移动点数
    "LAYER_DIRECTION_CONSISTENCY": 0.75,# 图层分离：方向一致性阈值
    "LAYER_MEAN_VEC_MIN": 0.1,          # 图层分离：主方向向量最小模长
    "LAYER_COS_SIM_THRESH": 0.7,        # 图层分离：与主方向夹角余弦阈值
    "FLOW_LAYER_STATIC_THRESH": 2.0,    # 对齐后残余移动的静止判断阈值（像素）

    # ========== 光流通用参数 ==========
    "FLOW_FEATURE_COUNT": 150,          # 光流法提取特征点最大数量（预览也用）
    "FLOW_QUALITY_LEVEL": 0.05,         # 角点检测质量阈值
    "FLOW_MIN_DISTANCE": 10,            # 角点最小间距

    # ========== 预览参数 ==========
    "PREVIEW_DENSE_ALPHA": 0.6,         # 预览稠密光流叠加透明度
    "PREVIEW_DIFF_DECAY": 0.7,          # 预览差分残影衰减系数（越小残留越短）
    "PREVIEW_MOTION_DECAY": 0.85,       # 预览运动拖影衰减系数
    "PREVIEW_MOTION_MAX_SPEED": 50,     # 预览运动速度映射最大值（像素）

    # ========== 缩放运镜检测 ==========
    "ZOOM_DIRECTION_CONSISTENCY": 0.7,  # 缩放运镜径向一致性阈值
    "ZOOM_RADIAL_CORRELATION": 0.3,     # 缩放运镜位移-距离相关系数下限

    # OP/ED 检测
    "OPED_DETECTION_ENABLED": True,      # 是否启用
    "OPED_WINDOW_SEC": 90,              # 匹配窗口长度（秒）
    "OPED_MATCH_THRESHOLD": 0.9,       # 相关系数阈值
    "OPED_HISTORY_HOURS": 4,           # 历史波形最长保存小时
    "OPED_SAMPLE_INTERVAL_SEC": 1,    # 历史采样间隔（秒）
    "OPED_CLASSIFY_THRESHOLD": 0.8,   # 归类阈值（平均相关系数）
    # OP/ED 图像哈希匹配参数
    "OPED_HASH_ENABLED": True,
    "OPED_HASH_SIZE": 8,  # 新哈希内部 8x8，这里可忽略，或用于兼容
    "OPED_HASH_MAX_DIST": 12,  # 允许最大汉明距离
    "OPED_HASH_WIN_RATIO": 0.6,  # 可适当提高窗口相似帧占比要求

    # === 新增：帧级实时匹配参数 ===
    "OPED_ENTER_HAMMING": 5,  # 进入匹配的汉明距离上限
    "OPED_EXIT_HAMMING": 15,  # 退出判定距离下限
    "OPED_ENTER_CONSEC": 3,  # 连续匹配帧数（进入）
    "OPED_EXIT_CONSEC": 3,  # 连续不匹配帧数（退出）
    "OPED_MIN_DURATION": 30,  # 最小区间长度（秒），短于此刻丢弃
    "OPED_SELF_EXCLUDE_SEC": 90,  # 自排除窗口（秒），不与此时间内历史比较
    # ========== 视频文件模式 ==========
    "VIDEO_MODE": False,                  # 当前是否为视频文件模式（运行时动态设置，不必持久化）
    "VIDEO_FILE_PATH": "",                # 上次打开的视频文件路径
    "VIDEO_PLAYBACK_SPEED": 1.0,          # 倍速播放（1.0=原速）
    "VIDEO_SEEK_ENABLED": True,           # 允许拖拽进度条

    # ========== GPU 加速 ==========
    "GPU_ENABLED": False,                 # 是否启用 GPU 加速（需 OpenCV CUDA）
    "GPU_FORCE_FALLBACK": False,         # 即使检测到 GPU 也强制使用 CPU（用于测试）
    # ========== 颜色方案 ==========
    "COLORS": {
        "accent": "#E6397C",
        "bg": "#000000",
        "secondary": "#778899",
        "btn_bg": "#222222",
        "title_bg": "#111111",
        "canvas_bg": "#1A1A1D",
        "wave_line_filtered": "#E6397C",
        "wave_line_raw": "#a9ded8",
        "filter_translation": "#88dba3",
        "filter_optical_flow": "#ffff99",
        "filter_hash": "#CC44CC",
        "filter_still": "#2d35d2",
        "filter_local": "#00FFFF",
        "filter_other": "#888888",
        "filter_raw_total": "#9dc1c6",
        "filter_zoom": "#FFA07A",       # 缩放过滤颜色
        "filter_filtered_total": "#E6397C"
    },

    "USE_CUSTOM_COLORS": True,          # 是否启用自定义调色板
}

# 深拷贝一份作为默认配置，用于恢复出厂设置
CONFIG_DEFAULT = copy.deepcopy(CONFIG)
color_manager = ColorManager(CONFIG["COLORS"])
def get_project_dir():
    """
    返回可读写的项目文件夹路径。
    - 源码运行时为脚本所在目录
    - 打包成 exe 后为 exe 所在目录（sys._MEIPASS 不可写）
    """
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    else:
        return os.path.dirname(os.path.abspath(__file__))

def get_settings_path():
    """返回配置文件存放路径（统一放在项目文件夹下）"""
    return os.path.join(get_project_dir(), "cel_counter_settings.json")

# ====== Module: widgets.py ======
# widgets.py
import tkinter as tk

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
# ====== Module: detection.py ======
# detection.py
"""
动画作画张数检测模块。
包含感知哈希、自适应阈值、全局平移估计、图层分离、缩放检测、
局部运动判断、基础/完整检测等功能。
"""

import cv2
import numpy as np
try:
    import cv2.cuda
    CUDA_AVAILABLE = True
except ImportError:
    CUDA_AVAILABLE = False
# ======================== 基本工具 ========================

def compute_hash(gray_img):
    """计算感知哈希（16x16）"""
    resized = cv2.resize(gray_img, (16, 16), interpolation=cv2.INTER_AREA)
    avg = resized.mean()
    return (resized > avg).flatten()
def compute_oped_hash(gray_img):     # <-- 从此行开始替换整个函数
    """
    使用感知哈希 (pHash) 计算 64 位哈希值，用于 OP/ED 匹配。
    流程：缩放 -> DCT -> 取左上角 8x8 低频系数 -> 二值化。
    返回整数 (64 位)。
    """
    # 1. 缩放为 32x32（提高分辨率以增加信息量）
    resized = cv2.resize(gray_img, (32, 32), interpolation=cv2.INTER_AREA)
    # 2. 离散余弦变换
    dct = cv2.dct(np.float32(resized))
    # 3. 保留左上角 8x8 低频分量（64 位）
    dct_low = dct[:8, :8]
    # 4. 计算均值并二值化
    avg = dct_low.mean()
    bits = dct_low > avg
    # 5. 打包为 64 位整数 (little-endian)
    packed = np.packbits(bits.flatten())
    return int.from_bytes(packed.tobytes(), 'little')
def adaptive_threshold(frame):
    """根据帧的平均亮度计算自适应差分阈值"""
    mean_val = np.mean(frame)
    t = CONFIG["MIN_DIFF_THRESHOLD"] + (mean_val / 255.0) * (CONFIG["MAX_DIFF_THRESHOLD"] - CONFIG["MIN_DIFF_THRESHOLD"])
    return max(CONFIG["MIN_DIFF_THRESHOLD"], min(CONFIG["MAX_DIFF_THRESHOLD"], t))





# ======================== 原始变化检测 ========================

def is_raw_change(prev, curr):
    """原始变化检测（仅基于差分面积比）"""
    diff = cv2.absdiff(prev, curr)
    thresh = adaptive_threshold(curr)
    _, mask = cv2.threshold(diff, thresh, 255, cv2.THRESH_BINARY)
    kernel = np.ones((3, 3), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    ratio = np.count_nonzero(mask) / mask.size
    return ratio >= CONFIG["MIN_CHANGE_RATIO"]



def fast_global_corr(img1, img2):
    """快速计算两幅等尺寸灰度图的整体归一化相关系数（-1~1）"""
    f1 = img1.astype(np.float64).ravel()
    f2 = img2.astype(np.float64).ravel()
    m1 = np.mean(f1)
    m2 = np.mean(f2)
    num = np.dot(f1 - m1, f2 - m2)
    den = np.sqrt(np.dot(f1 - m1, f1 - m1) * np.dot(f2 - m2, f2 - m2))
    if den < 1e-10:
        return 1.0
    return num / den
# ======================== 基础检测 ========================

def basic_is_new_cel(prev, curr):
    """基础检测，返回 (是否为新张, 移动类型描述)"""
    corr = fast_global_corr(prev, curr)   # 替换模板匹配

    if corr > CONFIG["BASIC_CORR_THRESHOLD"]:
        raw_diff = cv2.absdiff(prev, curr)
        raw_thresh = adaptive_threshold(curr)
        _, raw_mask = cv2.threshold(raw_diff, raw_thresh, 255, cv2.THRESH_BINARY)
        # --- 插入闭运算 ---
        kernel = np.ones((3, 3), np.uint8)
        raw_mask = cv2.morphologyEx(raw_mask, cv2.MORPH_CLOSE, kernel)
        raw_ratio = np.count_nonzero(raw_mask) / raw_mask.size
        if raw_ratio < CONFIG["BASIC_MIN_RAW_RATIO_STILL"]:
            return False, "极慢平移/静止"
        if raw_ratio >= CONFIG["SIGNIFICANT_CHANGE_RATIO"]:
            return True, "新作画(基础)"
        else:
            return True, "新作画(基础)"

    diff_thresh = adaptive_threshold(curr)
    raw_diff = cv2.absdiff(prev, curr)
    _, raw_mask = cv2.threshold(raw_diff, diff_thresh, 255, cv2.THRESH_BINARY)
    kernel = np.ones((3, 3), np.uint8)
    raw_mask = cv2.morphologyEx(raw_mask, cv2.MORPH_CLOSE, kernel)
    raw_ratio = np.count_nonzero(raw_mask) / raw_mask.size

    if raw_ratio < CONFIG["MIN_CHANGE_RATIO"]:
        return False, "静止"
    if raw_ratio >= CONFIG["SIGNIFICANT_CHANGE_RATIO"]:
        return True, "新作画(基础)"
    return True, "新作画(基础)"

# ======================== 局部运动判断 ========================

def has_local_motion(binary_mask):
    """判断变化掩膜是否为局部运动（如口型）"""
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

# ======================== 统一运动分析（一次角点，两次光流） ========================

def unified_motion_analysis(prev, curr, use_optical_flow=True, use_gpu=False):
    """
    统一运动分析，支持 GPU 加速（仅光流部分）。
    若 use_gpu=True 且 CUDA 可用，则使用 cv2.cuda 接口。
    返回: (has_shift, dx, dy, curr_aligned, is_layer_move, is_zoom)
    """
    max_corners = 200
    # 角点检测仍在 CPU 上执行（GPU 版 goodFeaturesToTrack 存在但较复杂，暂用 CPU）
    corners = cv2.goodFeaturesToTrack(
        prev,
        maxCorners=max_corners,
        qualityLevel=CONFIG["FLOW_QUALITY_LEVEL"],
        minDistance=CONFIG["FLOW_MIN_DISTANCE"],
        mask=None
    )

    has_shift = False
    dx, dy = 0.0, 0.0
    curr_aligned = curr
    is_layer_move = False
    is_zoom = False

    if corners is None:
        return has_shift, dx, dy, curr_aligned, is_layer_move, is_zoom

    p1 = np.float32(corners).reshape(-1, 2)

    # 尝试使用 GPU 光流
    gpu_available = False
    if use_gpu:
        try:
            gpu_prev = cv2.cuda_GpuMat()
            gpu_curr = cv2.cuda_GpuMat()
            gpu_prev.upload(prev)
            gpu_curr.upload(curr)
            gpu_p1 = cv2.cuda_GpuMat()
            gpu_p1.upload(p1)
            # 注意：CUDA 的 PyrLK 参数略有不同，需要创建光流对象
            lk = cv2.cuda.SparsePyrLKOpticalFlow.create(
                winSize=(21, 21), maxLevel=3, iters=30)
            gpu_p2, gpu_status, _ = lk.calc(gpu_prev, gpu_curr, gpu_p1)
            p2_curr = gpu_p2.download()
            status_curr = gpu_status.download()
            gpu_available = True
        except Exception as e:
            # 回退到 CPU
            gpu_available = False

    if not gpu_available:
        p2_curr, status_curr, _ = cv2.calcOpticalFlowPyrLK(prev, curr, p1, None)

    if p2_curr is not None and np.sum(status_curr) >= 20:
        valid = status_curr.flatten() == 1
        vecs_curr = p2_curr[valid] - p1[valid]
        dx = np.median(vecs_curr[:, 0])
        dy = np.median(vecs_curr[:, 1])
        if np.sqrt(dx*dx + dy*dy) >= 0.3:
            has_shift = True
            h, w = curr.shape
            M = np.float32([[1, 0, -dx], [0, 1, -dy]])
            curr_aligned = cv2.warpAffine(curr, M, (w, h), borderMode=cv2.BORDER_REPLICATE)
    if not use_optical_flow:
        return has_shift, dx, dy, curr_aligned, False, False

    # 第二次追踪：prev -> curr_aligned，分析残余运动
    if gpu_available:
        try:
            gpu_aligned = cv2.cuda_GpuMat()
            gpu_aligned.upload(curr_aligned)
            gpu_p2_aligned, gpu_status_aligned, _ = lk.calc(gpu_prev, gpu_aligned, gpu_p1)
            p2_aligned = gpu_p2_aligned.download()
            status_aligned = gpu_status_aligned.download()
        except:
            p2_aligned, status_aligned, _ = cv2.calcOpticalFlowPyrLK(prev, curr_aligned, p1, None)
    else:
        p2_aligned, status_aligned, _ = cv2.calcOpticalFlowPyrLK(prev, curr_aligned, p1, None)

    if p2_aligned is not None and np.sum(status_aligned) >= 20:
        valid = status_aligned.flatten() == 1
        vecs_res = p2_aligned[valid] - p1[valid]
        pts = p1[valid]
        norms_res = np.linalg.norm(vecs_res, axis=1)

        # 图层分离检测 (逻辑不变)
        moving_mask = norms_res > CONFIG["FLOW_LAYER_STATIC_THRESH"]
        if np.sum(moving_mask) >= CONFIG["LAYER_MIN_MOVING_POINTS"]:
            moving_vecs = vecs_res[moving_mask]
            mean_vec = np.mean(moving_vecs, axis=0)
            if np.linalg.norm(mean_vec) >= CONFIG["LAYER_MEAN_VEC_MIN"]:
                cos_sim = np.dot(moving_vecs, mean_vec) / (
                    np.linalg.norm(moving_vecs, axis=1) * np.linalg.norm(mean_vec) + 1e-8
                )
                consistency = np.mean(cos_sim > CONFIG["LAYER_COS_SIM_THRESH"])
                if consistency > CONFIG["LAYER_DIRECTION_CONSISTENCY"]:
                    is_layer_move = True

        # 缩放检测 (逻辑不变)
        h, w = prev.shape
        center = np.array([w/2, h/2])
        radial_vecs = pts - center
        radial_norms = np.linalg.norm(radial_vecs, axis=1)
        far_mask = radial_norms > 5.0
        if np.sum(far_mask) >= 10:
            final_mask = far_mask & (norms_res > 0.5)
            if np.sum(final_mask) >= 10:
                pts_f = pts[final_mask]
                vecs_f = vecs_res[final_mask]
                radial_f = radial_vecs[final_mask]
                radial_n_f = radial_norms[final_mask]
                vec_n_f = norms_res[final_mask]

                radial_unit = radial_f / (radial_n_f[:, np.newaxis] + 1e-8)
                vec_unit = vecs_f / (vec_n_f[:, np.newaxis] + 1e-8)
                cos_sim_rad = np.abs(np.sum(vec_unit * radial_unit, axis=1))
                consistency_rad = np.mean(cos_sim_rad > 0.85)

                if consistency_rad >= CONFIG.get("ZOOM_DIRECTION_CONSISTENCY", 0.7):
                    if len(vec_n_f) > 5:
                        corr = np.corrcoef(radial_n_f, vec_n_f)[0, 1]
                        if corr >= CONFIG.get("ZOOM_RADIAL_CORRELATION", 0.3):
                            is_zoom = True

    return has_shift, dx, dy, curr_aligned, is_layer_move, is_zoom

# ======================== 完整检测 ========================

def full_is_new_cel(prev, curr, use_optical_flow, use_gpu=False):
    """
    完整检测，返回 (是否为新张, 移动类型描述)
    使用统一运动分析进行全局平移、图层分离、缩放检测，
    并对齐后差分，结合局部运动、相似度等进行综合判断。
    """
    # 相关性及初始差分检查（用于极慢平移/静止直接返回）
    corr = fast_global_corr(prev, curr)

    if corr > CONFIG["FULL_CORR_THRESHOLD"]:
        raw_diff = cv2.absdiff(prev, curr)
        raw_thresh = adaptive_threshold(curr)
        _, raw_mask = cv2.threshold(raw_diff, raw_thresh, 255, cv2.THRESH_BINARY)
        raw_ratio = np.count_nonzero(raw_mask) / raw_mask.size
        if raw_ratio < CONFIG["FULL_STILL_RATIO"]:
            return False, "极慢平移/静止"

    diff_thresh = adaptive_threshold(curr)
    raw_diff = cv2.absdiff(prev, curr)
    _, raw_mask = cv2.threshold(raw_diff, diff_thresh, 255, cv2.THRESH_BINARY)
    raw_ratio = np.count_nonzero(raw_mask) / raw_mask.size

    # 统一运动分析（传入 use_gpu）
    has_shift, dx, dy, curr_aligned, is_layer_move, is_zoom = unified_motion_analysis(
        prev, curr, use_optical_flow, use_gpu=use_gpu)

    # 对齐后差分
    diff = cv2.absdiff(prev, curr_aligned)
    _, mask = cv2.threshold(diff, diff_thresh, 255, cv2.THRESH_BINARY)
    kernel = np.ones((3, 3), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    change_ratio = np.count_nonzero(mask) / mask.size

    # 全局平移过滤
    if raw_ratio >= CONFIG["MIN_CHANGE_RATIO"] and change_ratio < CONFIG["ALIGNED_CHANGE_THRESHOLD"] and has_shift:
        return False, "全局平移"

    if change_ratio >= CONFIG["ALIGNED_CHANGE_THRESHOLD"]:
        if has_local_motion(mask):
            return True, "新作画"

        if use_optical_flow and is_layer_move:
            return False, "图层分离运镜"
        if use_optical_flow and is_zoom:
            return False, "缩放运镜"

        if change_ratio >= CONFIG["SIGNIFICANT_CHANGE_RATIO"]:
            return True, "新作画"

    if change_ratio < CONFIG["SIGNIFICANT_CHANGE_RATIO"]:
        mask_inv = cv2.bitwise_not(mask)
        score = cv2.matchTemplate(prev, curr_aligned, cv2.TM_CCOEFF_NORMED, mask=mask_inv)[0][0]
        if score >= CONFIG["SSIM_THRESHOLD"]:
            return False, "局部变化(过滤)"



    # 剩余微小变化的补充检查
    if change_ratio < CONFIG["SIGNIFICANT_CHANGE_RATIO"]:
        mask_inv = cv2.bitwise_not(mask)
        score = cv2.matchTemplate(prev, curr_aligned, cv2.TM_CCOEFF_NORMED, mask=mask_inv)[0][0]
        if score >= CONFIG["SSIM_THRESHOLD"]:
            return False, "局部变化(过滤)"

    return True, "新作画"
# ====== Module: preview.py ======
# preview.py
"""
实时预览模块：生成叠加了稠密光流、差分热力图、稀疏光流的预览图像。
"""

import cv2
import numpy as np
import time
import threading

# 预览功能依赖 PIL
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

        # 显示标志
        self.show_dense = False            # 默认关闭稠密光流
        self.show_sparse = True
        self.show_curr = True
        self.show_diff = True

        # 回调
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
        """停止预览线程"""
        self.active = False
        if self.thread is not None:
            self.thread.join(timeout=0.5)
            self.thread = None

    def _loop(self):
        """预览主循环，定时获取最新帧并生成预览图"""
        while self.active:
            if self._get_cache_cb:
                last, curr = self._get_cache_cb()
                if curr is not None:
                    combined = self.make_preview_image(last, curr)
                    if self._update_image_cb and self._root:
                        self._root.after(1, lambda img=combined: self._update_image_cb(img))
            time.sleep(self.update_interval)

    def make_preview_image(self, last, curr):
        """
        根据当前帧和上一帧生成叠加预览图像。
        包含：稠密光流、差分热力图（带衰减）、稀疏光流拖影。
        """
        # 基础层：当前帧灰度转 BGR
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

        # ---------- 差分热力图（带衰减） ----------
        if self.show_diff and last is not None and last.shape == curr.shape:
            diff = cv2.absdiff(last, curr)
            thresh = adaptive_threshold(curr)
            _, diff_mask = cv2.threshold(diff, thresh, 255, cv2.THRESH_BINARY)

            # 仅保留超过阈值的差分区域
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
            # 如果关闭了差分显示，让残留逐渐衰减
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
                    mask=None                     # 全图检测，不再使用字幕掩膜
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
                            # 颜色映射：快速移动偏蓝，慢速偏红
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
            # 按比例缩放并居中显示
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
# ====== Module: video_source.py ======
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
# ====== Module: oped_detector.py ======
# oped_detector.py
"""
OP/ED 实时检测器。
基于帧级哈希匹配，连续匹配/不匹配判定进入/退出 OP/ED 片段。
负责归类、扣除张数和时长统计。
"""
import time
from collections import deque

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
# ====== Module: record_manager.py ======
# record_manager.py
"""
运行记录管理器：保存/加载/查看/编辑/导出运行记录。
"""
import json, os, time, csv, tkinter as tk
from tkinter import filedialog, messagebox
import tkinter.ttk as ttk
import numpy as np

class RecordManager:
    def __init__(self, counter):
        self.counter = counter

    def _get_records_path(self):
        settings_dir = os.path.dirname(get_settings_path())
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

    def _save_record(self):
        """从当前计数器状态保存一条运行记录"""
        name = tk.simpledialog.askstring("保存记录", "输入记录名称：")
        if not name:
            return
        counter = self.counter
        oped = counter.oped_detector
        net_cels = counter.total_cels_count - oped.deducted_cels
        current_clock = getattr(counter, '_current_clock', counter.elapsed_time)
        net_time = current_clock - oped.deducted_time

        oped_segments = []
        for start, end, sid in oped.segment_occurrences:
            deducted_cels = self._calc_cels_in_interval(counter.total_history, start, end)
            oped_segments.append({
                "start": round(start, 2),"end": round(end, 2),"duration": round(end - start, 2),"segment_id": sid,"deducted_cels": deducted_cels,"deducted_time": round(end - start, 2)})

        instant_fps_values = [v for _, v in getattr(counter, 'instant_fps_history', [])]
        record = {
            "name": name,
            "record_time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "start_timestamp": counter.start_real_time,
            "end_timestamp": time.time(),
            "duration_sec": round(current_clock, 2),
            "total_raw_cels": counter.total_raw_cels_count,
            "total_filtered_cels": counter.total_cels_count,
            "filtering": {"translation": counter.total_translation_filtered,"optical_flow": counter.total_optical_flow_filtered,"hash": counter.total_hash_filtered,"still": counter.total_still_filtered,"local_motion": counter.total_local_filtered,"zoom": counter.total_zoom_filtered,"other": counter.total_other_unknown_filtered},
            "oped": {
                "total_deducted_cels": oped.deducted_cels,
                "total_deducted_time": round(oped.deducted_time, 2),
                "segments": oped_segments
            },
            "avg_fps_filtered": round(net_cels / net_time, 2) if net_time > 0 else 0,
            "avg_fps_raw": round(counter.total_raw_cels_count / current_clock, 2) if current_clock > 0 else 0,
            "extra": {
                "max_instant_fps": round(max(instant_fps_values), 2) if instant_fps_values else 0,
                "min_instant_fps": round(min(instant_fps_values), 2) if instant_fps_values else 0,
                "median_fps": round(np.median(instant_fps_values), 2) if instant_fps_values else 0
            }
        }

        path = self._get_records_path()
        try:
            with open(path, "r", encoding="utf-8") as f:
                records = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            records = []
        records.append(record)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(records, f, indent=2, ensure_ascii=False)
        messagebox.showinfo("保存成功", f"记录已保存至 {path}")

    def _calc_cels_in_interval(self, history, start, end):
        """返回时间段内过滤后张数净增"""
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

    def show_record_detail(self, record, index):
        """打开记录详情窗口"""
        counter = self.counter
        win = tk.Toplevel(counter.root)
        win.title(f"记录详情 - {record.get('name', '未命名')}")
        win.configure(bg=color_manager.get_color('bg'))
        win.geometry("700x550")
        win.transient(counter.root)
        win.grab_set()

        accent = color_manager.get_color('accent')
        bg = color_manager.get_color('bg')
        btn_bg = color_manager.get_color('btn_bg')

        main = tk.Frame(win, bg=bg)
        main.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        info_frame = tk.Frame(main, bg=bg)
        info_frame.pack(fill=tk.X, pady=(0, 10))

        def make_info_row(parent, label, value, row):
            tk.Label(parent, text=label + ":", fg=accent, bg=bg, font=("Arial", 9, "bold")).grid(row=row, column=0, sticky="w", padx=(0,5))
            tk.Label(parent, text=str(value), fg=accent, bg=bg, font=("Arial", 9)).grid(row=row, column=1, sticky="w")

        row = 0
        make_info_row(info_frame, "名称", record.get('name', ''), row); row += 1
        make_info_row(info_frame, "记录时间", record.get('record_time', ''), row); row += 1
        duration = record.get('duration_sec', 0)
        make_info_row(info_frame, "运行时长", f"{duration:.1f} 秒 ({duration/3600:.2f} 小时)", row); row += 1
        make_info_row(info_frame, "过滤前总张数", record.get('total_raw_cels', 0), row); row += 1
        make_info_row(info_frame, "过滤后总张数", record.get('total_filtered_cels', 0), row); row += 1
        avg_f = record.get('avg_fps_filtered', 0)
        avg_r = record.get('avg_fps_raw', 0)
        make_info_row(info_frame, "平均张数/秒(过滤后)", f"{avg_f:.2f}", row); row += 1
        make_info_row(info_frame, "平均张数/秒(过滤前)", f"{avg_r:.2f}", row); row += 1

        # 过滤分类表格
        filter_frame = tk.LabelFrame(main, text="过滤分类统计", fg=accent, bg=bg, font=("Arial", 9, "bold"))
        filter_frame.pack(fill=tk.BOTH, expand=True, pady=(0,10))
        columns = ("类型", "数量")
        tree_filter = ttk.Treeview(filter_frame, columns=columns, show="headings", height=8)
        tree_filter.heading("类型", text="过滤类型")
        tree_filter.heading("数量", text="过滤帧数")
        tree_filter.column("类型", width=200, anchor="w")
        tree_filter.column("数量", width=100, anchor="e")
        scroll_f = ttk.Scrollbar(filter_frame, orient="vertical", command=tree_filter.yview)
        tree_filter.configure(yscrollcommand=scroll_f.set)
        tree_filter.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll_f.pack(side=tk.RIGHT, fill=tk.Y)

        filtering = record.get('filtering', {})
        filter_items = [
            ("全局平移对齐过滤", filtering.get('translation', 0)),
            ("光流法过滤", filtering.get('optical_flow', 0)),
            ("哈希重复帧过滤", filtering.get('hash', 0)),
            ("静止过滤", filtering.get('still', 0)),
            ("局部变化过滤", filtering.get('local_motion', 0)),
            ("缩放过滤", filtering.get('zoom', 0)),
            ("其他", filtering.get('other', 0))
        ]
        for name, val in filter_items:
            tree_filter.insert("", tk.END, values=(name, val))

        # OP/ED 片段表格
        oped_frame = tk.LabelFrame(main, text="OP/ED 片段", fg=accent, bg=bg, font=("Arial", 9, "bold"))
        oped_frame.pack(fill=tk.BOTH, expand=True, pady=(0,10))
        oped_columns = ("ID", "开始(s)", "结束(s)", "时长(s)", "扣除张数")
        tree_oped = ttk.Treeview(oped_frame, columns=oped_columns, show="headings", height=6)
        for col in oped_columns:
            tree_oped.heading(col, text=col)
            tree_oped.column(col, width=100, anchor="center")
        tree_oped.column("ID", width=50)
        scroll_o = ttk.Scrollbar(oped_frame, orient="vertical", command=tree_oped.yview)
        tree_oped.configure(yscrollcommand=scroll_o.set)
        tree_oped.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll_o.pack(side=tk.RIGHT, fill=tk.Y)

        for seg in record.get('oped', {}).get('segments', []):
            tree_oped.insert("", tk.END, values=(
                seg.get('segment_id', ''),
                f"{seg.get('start', 0):.1f}",
                f"{seg.get('end', 0):.1f}",
                f"{seg.get('duration', 0):.1f}",
                seg.get('deducted_cels', 0)
            ))

        oped_total = record.get('oped', {})
        tk.Label(main, text=f"OP/ED 累计扣除: {oped_total.get('total_deducted_cels', 0)} 张, {oped_total.get('total_deducted_time', 0):.1f} 秒",
                 fg=accent, bg=bg, font=("Arial", 9)).pack(anchor="w")

        btn_frame = tk.Frame(main, bg=bg)
        btn_frame.pack(fill=tk.X, pady=(10,0))

        def export_csv():
            file_path = filedialog.asksaveasfilename(defaultextension=".csv",
                                                     filetypes=[("CSV files", "*.csv")],
                                                     title="导出记录为 CSV")
            if not file_path:
                return
            try:
                with open(file_path, "w", encoding="utf-8-sig", newline="") as f:
                    writer = csv.writer(f)
                    writer.writerow(["字段", "值"])
                    writer.writerow(["名称", record.get('name', '')])
                    writer.writerow(["记录时间", record.get('record_time', '')])
                    writer.writerow(["运行时长(秒)", record.get('duration_sec', '')])
                    writer.writerow(["过滤前总张数", record.get('total_raw_cels', '')])
                    writer.writerow(["过滤后总张数", record.get('total_filtered_cels', '')])
                    writer.writerow(["平均张数/秒(过滤后)", record.get('avg_fps_filtered', '')])
                    writer.writerow(["平均张数/秒(过滤前)", record.get('avg_fps_raw', '')])
                    writer.writerow([])
                    writer.writerow(["过滤类型", "数量"])
                    for fname, fval in filter_items:
                        writer.writerow([fname, fval])
                    writer.writerow([])
                    writer.writerow(["片段ID", "开始(s)", "结束(s)", "时长(s)", "扣除张数"])
                    for seg in record.get('oped', {}).get('segments', []):
                        writer.writerow([seg.get('segment_id', ''),
                                         seg.get('start', ''),
                                         seg.get('end', ''),
                                         seg.get('duration', ''),
                                         seg.get('deducted_cels', '')])
                messagebox.showinfo("导出成功", f"记录已导出至:\n{file_path}")
            except Exception as e:
                messagebox.showerror("导出失败", str(e))

        def edit_basic():
            self._edit_record_detail(record, index, win)

        tk.Button(btn_frame, text="导出 Excel (CSV)", command=export_csv,
                  bg=btn_bg, fg=accent).pack(side=tk.LEFT, padx=5)
        tk.Button(btn_frame, text="编辑基本信息", command=edit_basic,
                  bg=btn_bg, fg=accent).pack(side=tk.LEFT, padx=5)
        tk.Button(btn_frame, text="关闭", command=win.destroy,
                  bg=btn_bg, fg=accent).pack(side=tk.RIGHT, padx=5)

    def _edit_record_detail(self, record, index, parent_win):
        win = tk.Toplevel(parent_win)
        win.title("编辑记录")
        win.configure(bg=color_manager.get_color('bg'))
        accent = color_manager.get_color('accent')
        bg = color_manager.get_color('bg')
        btn_bg = color_manager.get_color('btn_bg')

        tk.Label(win, text="名称:", fg=accent, bg=bg).grid(row=0, column=0, padx=5, pady=5)
        name_var = tk.StringVar(value=record.get('name', ''))
        tk.Entry(win, textvariable=name_var, bg=btn_bg, fg=accent).grid(row=0, column=1, padx=5, pady=5)

        tk.Label(win, text="总张数:", fg=accent, bg=bg).grid(row=1, column=0, padx=5, pady=5)
        cels_var = tk.StringVar(value=str(record.get('total_filtered_cels', 0)))
        tk.Entry(win, textvariable=cels_var, bg=btn_bg, fg=accent).grid(row=1, column=1, padx=5, pady=5)

        tk.Label(win, text="总时长(s):", fg=accent, bg=bg).grid(row=2, column=0, padx=5, pady=5)
        time_var = tk.StringVar(value=str(record.get('duration_sec', 0)))
        tk.Entry(win, textvariable=time_var, bg=btn_bg, fg=accent).grid(row=2, column=1, padx=5, pady=5)

        def save():
            try:
                new_name = name_var.get().strip()
                new_cels = int(float(cels_var.get()))
                new_time = float(time_var.get())
                if not new_name:
                    return
                records = self.load_records()
                if 0 <= index < len(records):
                    records[index]['name'] = new_name
                    records[index]['total_filtered_cels'] = new_cels
                    records[index]['duration_sec'] = new_time
                    records[index]['avg_fps_filtered'] = new_cels / new_time if new_time > 0 else 0
                    self.save_records(records)
                    record.update(records[index])
                    parent_win.destroy()
                    self.show_record_detail(records[index], index)
            except ValueError:
                messagebox.showerror("输入错误", "请输入有效的数字")

        tk.Button(win, text="保存", command=save, bg=btn_bg, fg=accent).grid(row=3, column=0, columnspan=2, pady=10)
        win.transient(parent_win)
        win.grab_set()
# ====== Module: ui.py ======
# ui.py
import tkinter as tk
import copy, json, os
def build_main_ui(master, counter):
    """
    构建主界面，返回一个包含所有引用控件的字典。
    master: 主窗口或 Frame
    counter: AnimeCelCounter 实例，用于回调
    """
    ui = {}
    TRANSPARENT = "#000000"
    main_frame = tk.Frame(master, bg=TRANSPARENT)
    main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

    info_frame = tk.Frame(main_frame, bg=TRANSPARENT, width=130, height=200)
    info_frame.grid(row=0, column=0, sticky="nw", padx=(0, 10), pady=(10, 0))
    info_frame.pack_propagate(False)

    accent = color_manager.get_color('accent')
    lb_rt = tk.Label(info_frame, text="实时张数：0.0 ", fg=accent, bg=TRANSPARENT,
                     font=("Arial", 10, "bold"), anchor="w")
    lb_rt.pack(anchor="w", pady=3)
    lb_total_time = tk.Label(info_frame, text="运行时长：0.0 s", fg=accent, bg=TRANSPARENT,
                             font=("Arial", 10, "bold"), anchor="w")
    lb_total_time.pack(anchor="w", pady=3)
    lb_total_cels = tk.Label(info_frame, text="总张数：0", fg=accent, bg=TRANSPARENT,
                             font=("Arial", 10, "bold"), anchor="w")
    lb_total_cels.pack(anchor="w", pady=3)
    lb_avg = tk.Label(info_frame, text="总平均：0.0", fg=accent, bg=TRANSPARENT,
                      font=("Arial", 10, "bold"), anchor="w")
    lb_avg.pack(anchor="w", pady=3)
    lb_st = tk.Label(info_frame, text="静止/纯运镜", fg=accent, bg=TRANSPARENT,
                     font=("Arial", 10, "bold"), anchor="w")
    lb_st.pack(anchor="w", pady=5)

    wave_frame = tk.Frame(main_frame, bg=TRANSPARENT, width=300, height=80)
    wave_frame.grid(row=0, column=1, sticky="n", padx=(0, 10), pady=(10, 0))
    wave_frame.pack_propagate(False)
    canvas1 = tk.Canvas(wave_frame, bg=TRANSPARENT, highlightthickness=0, bd=0)
    canvas1.pack(fill=tk.BOTH, expand=True)

    wave2_frame = tk.Frame(main_frame, bg=TRANSPARENT, width=300, height=80)
    wave2_frame.grid(row=0, column=2, sticky="n", padx=(0, 10), pady=(10, 0))
    wave2_frame.pack_propagate(False)
    canvas2 = tk.Canvas(wave2_frame, bg=TRANSPARENT, highlightthickness=0, bd=0)
    canvas2.pack(fill=tk.BOTH, expand=True)

    btn_canvas = tk.Canvas(main_frame, bg=TRANSPARENT, highlightthickness=0, bd=0,
                           width=120, height=150)
    btn_canvas.grid(row=0, column=3, sticky="n", padx=(0, 10), pady=(10, 0))

    ui['lb_rt'] = lb_rt
    ui['lb_total_time'] = lb_total_time
    ui['lb_total_cels'] = lb_total_cels
    ui['lb_avg'] = lb_avg
    ui['lb_st'] = lb_st
    ui['canvas1'] = canvas1
    ui['canvas2'] = canvas2
    ui['btn_canvas'] = btn_canvas
    ui['main_frame'] = main_frame
    return ui


def create_video_controls(parent, counter):
    """
    创建视频播放控制条（进度条、播放/暂停、倍速等），返回控件字典。
    仅在视频模式下显示，放在主窗口底部。
    """
    accent = color_manager.get_color('accent')
    bg = color_manager.get_color('bg')
    btn_bg = color_manager.get_color('btn_bg')

    frame = tk.Frame(parent, bg=bg, height=30)
    # 初始隐藏
    frame.pack_forget()

    # 进度条
    progress_var = tk.DoubleVar()
    progress_scale = tk.Scale(frame, from_=0, to=100, orient=tk.HORIZONTAL,
                              variable=progress_var, showvalue=False, bg=bg,
                              fg=accent, troughcolor=btn_bg, highlightthickness=0,
                              command=lambda v: counter._on_seek(float(v)))
    progress_scale.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)

    # 时间标签
    time_label = tk.Label(frame, text="00:00/00:00", fg=accent, bg=bg, font=("Arial", 9))
    time_label.pack(side=tk.LEFT, padx=5)

    # 播放/暂停按钮
    play_var = tk.BooleanVar(value=True)
    def toggle_play():
        if play_var.get():
            counter.pause_video()
            play_btn.config(text="▶")
        else:
            counter.resume_video()
            play_btn.config(text="⏸")
        play_var.set(not play_var.get())
    play_btn = tk.Button(frame, text="⏸", command=toggle_play, bg=btn_bg, fg=accent,
                         bd=0, font=("Arial", 10), width=3)
    play_btn.pack(side=tk.LEFT, padx=2)

    # 倍速选择
    speed_var = tk.StringVar(value="1.0x")
    speed_menu = tk.OptionMenu(frame, speed_var, "0.5x", "1.0x", "1.5x", "2.0x",
                               command=lambda v: counter._on_speed_change(v))
    speed_menu.config(bg=btn_bg, fg=accent, highlightthickness=0)
    speed_menu.pack(side=tk.LEFT, padx=5)

    controls = {
        'frame': frame,
        'progress_var': progress_var,
        'time_label': time_label,
        'play_btn': play_btn,
        'play_var': play_var,
        'speed_var': speed_var,
    }
    return controls


def create_settings_window(parent, counter):
    """
    创建并返回设置窗口，内部绑定所有控件和回调。
    （保持原有结构，仅增加视频/GPU相关选项）
    """
    win = tk.Toplevel(parent)
    win.title("参数设置")
    win.overrideredirect(True)
    win.attributes("-alpha", CONFIG["ALPHA"])
    win.configure(bg=color_manager.get_color('bg'))
    win.geometry("1200x760")

    def on_close():
        if CONFIG["USE_CUSTOM_COLORS"]:
            for key, (var, _) in counter.color_vars.items():
                CONFIG["COLORS"][key] = var.get()
        current_layout = {
            'window_geometry': win.winfo_geometry(),
            'panels': {name: panel.get_geometry() for name, panel in panels.items()}
        }
        if counter.active_layout:
            counter.layouts[counter.active_layout] = current_layout
        else:
            counter.layouts["默认布局"] = current_layout
            counter.active_layout = "默认布局"
        counter._save_all_settings()
        if hasattr(counter, 'settings_canvas'):
            counter.settings_canvas.unbind_all("<MouseWheel>")
        counter._settings_win = None
        counter.settings_wave_canvas = None
        counter.preview_manager.stop()
        if hasattr(counter, '_crop_window') and counter._crop_window:
            counter._crop_window.destroy()
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
    counter.param_entries = counter._build_params_content(param_panel.content, win, panels)

    wave_panel = DraggablePanel(main_frame, "总张数波形", 600, 400)
    panels['wave'] = wave_panel
    counter.settings_wave_canvas = tk.Canvas(wave_panel.content, bg=bg, highlightthickness=0)
    counter.settings_wave_canvas.pack(fill=tk.BOTH, expand=True)

    preview_panel = DraggablePanel(main_frame, "实时预览", 600, 400)
    panels['preview'] = preview_panel
    control_bar = tk.Frame(preview_panel.content, bg=bg)
    control_bar.pack(fill="x", pady=(0, 5))

    preview_toggle_var = tk.BooleanVar(value=False)
    tk.Checkbutton(control_bar, text="开启实时预览", variable=preview_toggle_var,
                   fg=accent, bg=bg, selectcolor="#222222",
                   command=lambda: counter._on_preview_toggle(preview_toggle_var.get())
                   ).pack(side="left", padx=3)

    show_dense_var = tk.BooleanVar(value=counter.preview_manager.show_dense)
    tk.Checkbutton(control_bar, text="稠密光流", variable=show_dense_var,
                   fg=accent, bg=bg, selectcolor="#222222",
                   command=lambda: setattr(counter.preview_manager, 'show_dense', show_dense_var.get())
                   ).pack(side="left", padx=3)

    show_sparse_var = tk.BooleanVar(value=counter.preview_manager.show_sparse)
    tk.Checkbutton(control_bar, text="稀疏光流", variable=show_sparse_var,
                   fg=accent, bg=bg, selectcolor="#222222",
                   command=lambda: setattr(counter.preview_manager, 'show_sparse', show_sparse_var.get())
                   ).pack(side="left", padx=3)

    show_curr_var = tk.BooleanVar(value=counter.preview_manager.show_curr)
    tk.Checkbutton(control_bar, text="当前帧", variable=show_curr_var,
                   fg=accent, bg=bg, selectcolor="#222222",
                   command=lambda: setattr(counter.preview_manager, 'show_curr', show_curr_var.get())
                   ).pack(side="left", padx=3)

    show_diff_var = tk.BooleanVar(value=counter.preview_manager.show_diff)
    tk.Checkbutton(control_bar, text="差分", variable=show_diff_var,
                   fg=accent, bg=bg, selectcolor="#222222",
                   command=lambda: setattr(counter.preview_manager, 'show_diff', show_diff_var.get())
                   ).pack(side="left", padx=3)

    tk.Label(control_bar, text="刷新间隔(秒):", fg=accent, bg=bg).pack(side="left", padx=3)
    interval_var = tk.StringVar(value=str(counter.preview_manager.update_interval))
    interval_entry = tk.Entry(control_bar, textvariable=interval_var, width=4,
                              bg=btn_bg, fg=accent, insertbackground=accent)
    interval_entry.pack(side="left")
    def apply_interval():
        try:
            val = float(interval_var.get())
            if val > 0:
                counter.preview_manager.update_interval = val
        except ValueError:
            pass
    interval_entry.bind("<Return>", lambda e: apply_interval())
    tk.Button(control_bar, text="应用", command=apply_interval,
              bg=btn_bg, fg=accent, activebackground="#444444").pack(side="left", padx=2)

    preview_label = tk.Label(preview_panel.content, bg=bg)
    preview_label.pack(fill=tk.BOTH, expand=True)
    counter.preview_manager._label = preview_label

    def update_preview_cb(img):
        counter.preview_manager.update_image_on_label(img, preview_label)
    counter.preview_manager.set_callbacks(
        lambda: counter.preview_cache,
        update_preview_cb,
        win  # 使用设置窗口作为 root 来调度 after
    )

    lock_panels_var = tk.BooleanVar(value=True)
    def toggle_lock():
        locked = lock_panels_var.get()
        for p in panels.values():
            p.set_locked(locked)

    lock_check = tk.Checkbutton(title_bar, text="锁定面板", variable=lock_panels_var,
                                command=toggle_lock, fg=accent, bg=title_bg,
                                selectcolor=title_bg)
    lock_check.pack(side=tk.RIGHT, padx=10)
    toggle_lock()

    # ========== 新增：视频模式 & GPU 设置组 ==========
    video_group = tk.LabelFrame(param_panel.content, text="视频模式 & GPU", fg=accent, bg=bg, font=("Arial", 9, "bold"))
    video_group.pack(fill="x", padx=5, pady=5)

    # GPU 加速开关
    gpu_var = tk.BooleanVar(value=CONFIG.get("GPU_ENABLED", False))
    tk.Checkbutton(video_group, text="启用 GPU 加速 (需 OpenCV CUDA)", variable=gpu_var,
                   fg=accent, bg=bg, selectcolor=btn_bg).pack(anchor="w", padx=5, pady=2)

    # 视频播放速度
    speed_label = tk.Label(video_group, text="默认播放速度:", fg=accent, bg=bg)
    speed_label.pack(side="left", padx=5)
    speed_entry_var = tk.StringVar(value=str(CONFIG.get("VIDEO_PLAYBACK_SPEED", 1.0)))
    speed_entry = tk.Entry(video_group, textvariable=speed_entry_var, width=5,
                           bg=btn_bg, fg=accent)
    speed_entry.pack(side="left", padx=2)

    # 保存时同步这些新配置项
    def save_settings():
        # 原有保存逻辑在 counter._build_params_content 的 save_settings 中定义，
        # 这里额外保存 GPU 和速度设置
        CONFIG["GPU_ENABLED"] = gpu_var.get()
        try:
            CONFIG["VIDEO_PLAYBACK_SPEED"] = float(speed_entry_var.get())
        except:
            pass
        # 调用 counter 原有的保存设置函数（需确保 counter.param_entries 的保存逻辑被触发）
        # 由于我们不在这个闭包内直接访问 counter._build_params_content 中的变量，
        # 需要通过 counter 的方法来保存。简便做法：在 counter 中提供专门的保存方法。
        counter._save_gpu_video_config(gpu_var.get(), speed_entry_var.get())
        # 同时调用 save_settings 触发原有逻辑（假设 counter 有该方法）


    tk.Button(video_group, text="应用视频设置", command=save_settings,
              bg=btn_bg, fg=accent).pack(pady=5)

    win.update_idletasks()
    # 自动开启预览
    preview_toggle_var.set(True)
    if not counter.preview_manager.active:
        counter.preview_manager.start()
    # 应用布局并启动波形绘制
    counter._apply_layout(win, panels)
    win.after(100, counter._draw_settings_wave)
    win.protocol("WM_DELETE_WINDOW", on_close)
    return win
def create_crop_window(counter):
    """创建全屏截图范围调整窗口"""
    if hasattr(counter, '_crop_window') and counter._crop_window is not None:
        try:
            counter._crop_window.destroy()
        except:
            pass
        counter._crop_window = None
        return

    screen_w = counter.root.winfo_screenwidth()
    screen_h = counter.root.winfo_screenheight()
    left, top, right, bottom = counter.region

    win = tk.Toplevel(counter.root)
    counter._crop_window = win
    win.attributes('-fullscreen', True)
    win.attributes('-topmost', True)
    win.attributes('-transparentcolor', 'black')
    win.configure(bg='black')
    win.overrideredirect(True)

    canvas = tk.Canvas(win, bg='black', highlightthickness=0, bd=0)
    canvas.pack(fill='both', expand=True)

    RECT_OUTLINE_COLOR = 'red'
    RECT_WIDTH = 3
    HANDLE_SIZE = 8
    HANDLE_FILL = '#FFFFFF'
    HANDLE_OUTLINE = '#000000'

    rect_id = canvas.create_rectangle(left, top, right, bottom,
                                      outline=RECT_OUTLINE_COLOR, width=RECT_WIDTH, fill='')

    handles = {}
    handle_ids = []
    handle_offsets = {
        'nw': (-HANDLE_SIZE, -HANDLE_SIZE),
        'ne': (HANDLE_SIZE, -HANDLE_SIZE),
        'sw': (-HANDLE_SIZE, HANDLE_SIZE),
        'se': (HANDLE_SIZE, HANDLE_SIZE),
        'n': (0, -HANDLE_SIZE),
        's': (0, HANDLE_SIZE),
        'w': (-HANDLE_SIZE, 0),
        'e': (HANDLE_SIZE, 0)
    }

    def create_handle(name, lx, ly):
        off_x, off_y = handle_offsets[name]
        vx = lx + off_x
        vy = ly + off_y
        h = canvas.create_rectangle(vx - HANDLE_SIZE, vy - HANDLE_SIZE,
                                    vx + HANDLE_SIZE, vy + HANDLE_SIZE,
                                    fill=HANDLE_FILL, outline=HANDLE_OUTLINE)
        handles[name] = {'id': h, 'logic_center': (lx, ly)}
        handle_ids.append(h)
        canvas.tag_bind(h, '<Button-1>', lambda e, n=name: start_resize(e, n))
        canvas.tag_bind(h, '<B1-Motion>', lambda e: on_drag(e))
        canvas.tag_bind(h, '<ButtonRelease-1>', lambda e: on_release(e))

    def update_handles():
        cx = (left + right) // 2
        cy = (top + bottom) // 2
        logic_positions = {
            'nw': (left, top), 'ne': (right, top), 'sw': (left, bottom), 'se': (right, bottom),
            'n': (cx, top), 's': (cx, bottom), 'w': (left, cy), 'e': (right, cy)
        }
        for name, (lx, ly) in logic_positions.items():
            off_x, off_y = handle_offsets[name]
            vx = lx + off_x
            vy = ly + off_y
            canvas.coords(handles[name]['id'],
                          vx - HANDLE_SIZE, vy - HANDLE_SIZE,
                          vx + HANDLE_SIZE, vy + HANDLE_SIZE)
            handles[name]['logic_center'] = (lx, ly)

    for name in ['nw', 'ne', 'sw', 'se', 'n', 's', 'w', 'e']:
        create_handle(name, 0, 0)
    update_handles()

    drag_data = {"mode": None, "start_x": 0, "start_y": 0, "orig_rect": None, "handle": None}

    def start_move(event):
        if left <= event.x <= right and top <= event.y <= bottom:
            drag_data["mode"] = "move"
            drag_data["start_x"] = event.x
            drag_data["start_y"] = event.y
            drag_data["orig_rect"] = (left, top, right, bottom)

    def start_resize(event, handle):
        drag_data["mode"] = "resize"
        drag_data["start_x"] = event.x
        drag_data["start_y"] = event.y
        drag_data["handle"] = handle
        drag_data["orig_rect"] = (left, top, right, bottom)

    def on_drag(event):
        nonlocal left, top, right, bottom
        mode = drag_data.get("mode")
        if not mode:
            return
        dx = event.x - drag_data["start_x"]
        dy = event.y - drag_data["start_y"]
        if mode == "move":
            orig = drag_data["orig_rect"]
            new_left = orig[0] + dx
            new_top = orig[1] + dy
            new_right = orig[2] + dx
            new_bottom = orig[3] + dy
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
            h = drag_data["handle"]
            orig = drag_data["orig_rect"]
            ol, ot, or_, ob = orig
            min_size = 20
            if 'w' in h:
                new_l = ol + dx
                if new_l < 0: new_l = 0
                if or_ - new_l < min_size: new_l = or_ - min_size
                left = new_l
            if 'e' in h:
                new_r = or_ + dx
                if new_r > screen_w: new_r = screen_w
                if new_r - left < min_size: new_r = left + min_size
                right = new_r
            if 'n' in h:
                new_t = ot + dy
                if new_t < 0: new_t = 0
                if ob - new_t < min_size: new_t = ob - min_size
                top = new_t
            if 's' in h:
                new_b = ob + dy
                if new_b > screen_h: new_b = screen_h
                if new_b - top < min_size: new_b = top + min_size
                bottom = new_b
            canvas.coords(rect_id, left, top, right, bottom)
            update_handles()

    def on_release(event):
        if drag_data.get("mode"):
            drag_data["mode"] = None
            counter.region = (left, top, right, bottom)
            CONFIG["CROP_REGION"] = counter.region
            CONFIG["CROP_RATIO"] = 1.0
            if hasattr(counter, '_settings_win') and counter._settings_win is not None:
                if hasattr(counter, 'param_entries') and 'CROP_RATIO' in counter.param_entries:
                    counter.param_entries['CROP_RATIO'].set("1.0")
            keep_h = counter.region[3] - counter.region[1]
            keep_w = counter.region[2] - counter.region[0]
            counter.frame_shape = (int(keep_h * CONFIG["SCALE_FACTOR"]),
                                   int(keep_w * CONFIG["SCALE_FACTOR"]))
            counter._save_all_settings()
        if hasattr(counter, 'crop_ratio_label_var'):
            counter.crop_ratio_label_var.set("手动区域缩放比例:")

    def is_on_handle(x, y):
        items = canvas.find_overlapping(x - 1, y - 1, x + 1, y + 1)
        for item in items:
            if item in handle_ids:
                return True
        return False

    def on_canvas_click(event):
        if is_on_handle(event.x, event.y):
            return
        if left <= event.x <= right and top <= event.y <= bottom:
            start_move(event)
        else:
            close_win()

    def close_win(event=None):
        win.destroy()
        counter._crop_window = None

    canvas.bind("<Button-1>", on_canvas_click)
    canvas.bind("<B1-Motion>", on_drag)
    canvas.bind("<ButtonRelease-1>", on_release)
    win.bind("<Escape>", close_win)

    canvas.create_text(screen_w // 2, 30,
                       text="拖动矩形移动，拖拽白点调整大小，点击外部或ESC关闭",
                       fill="white", font=("Arial", 12))
# ====== Module: main.py ======
# ==============================================================================
# 项目名称：动漫张数检测系统
# 功能说明：dxcam捕获、多重过滤非新作画帧、实时预览、UI交互一体化工具
# 模块组成：config.py / widgets.py / detection.py / preview.py / ui.py / oped_detector.py / record_manager.py / main.py
# ==============================================================================

import sys, os, time, threading, json, copy, subprocess
from collections import deque
import csv
import cv2
import numpy as np
import tkinter as tk
import tkinter.messagebox as messagebox

# 添加自定义模块导入

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
        self.start_real_time = time.time()  # 记录本次运行开始的时间戳

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

        # ========== 新增：视频模式与 GPU 初始化 ==========
        self.video_mode = CONFIG.get("VIDEO_MODE", False)
        self.video_file_path = CONFIG.get("VIDEO_FILE_PATH", "")
        self.use_gpu = CONFIG.get("GPU_ENABLED", False)
        self.frame_source = None  # 将在导入视频时创建
        self.video_controls = None  # 视频控制条，初始隐藏
        self.video_clock = 0.0  # 视频播放位置（秒）
        self.video_is_playing = True
        self.video_playback_speed = CONFIG.get("VIDEO_PLAYBACK_SPEED", 1.0)
        self._current_clock = 0.0  # 统一的时间基准（秒），波形、显示等均使用此值
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
        # 切换为视频模式
        if self.frame_source:
            self.frame_source.close()
        self.frame_source = FrameSource(mode='video', video_path=file_path,
                                        scale_factor=CONFIG["SCALE_FACTOR"],
                                        use_gpu=self.use_gpu)
        self.video_mode = True
        self.frame_shape = self.frame_source.frame_shape
        # 创建并显示视频控制条
        if self.video_controls is None:
            self.video_controls = create_video_controls(self.root, self)
        self.video_controls['frame'].pack(fill=tk.X, side=tk.BOTTOM, pady=(0, 5))
        # 重置统计并自动开始播放
        self.reset_stats()
        self.video_is_playing = True
        self.is_running = True
        self._run_event.set()
        CONFIG["VIDEO_MODE"] = True
        CONFIG["VIDEO_FILE_PATH"] = file_path
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

        # 参数分组
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

        # 截图区域比例标签
        self.crop_ratio_label_var = tk.StringVar()
        if CONFIG.get("CROP_REGION"):
            self.crop_ratio_label_var.set("手动区域缩放比例:")
        else:
            self.crop_ratio_label_var.set("截图区域比例:")

        entries = {}  # 存放所有参数控件变量（StringVar 或 BooleanVar）
        group_frames = {}  # 存放每个组的内容 frame，方便折叠

        left_frame = tk.Frame(scroll_frame, bg=bg)
        right_frame = tk.Frame(scroll_frame, bg=bg)
        left_frame.grid(row=0, column=0, sticky="nw", padx=(0, 5))
        right_frame.grid(row=0, column=1, sticky="nw")

        # 创建每个组
        for idx, (group_name, keys) in enumerate(groups):
            target = left_frame if idx % 2 == 0 else right_frame
            row = target.grid_size()[1]

            # 外层容器（用于标题 + 内容）
            outer = tk.Frame(target, bg=bg)
            outer.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(8, 2))
            row += 1  # 后续控件行从 row 开始，但我们现在不再直接 grid 到 target

            # 标题栏
            title_frame = tk.Frame(outer, bg=bg)
            title_frame.pack(fill="x")

            # 折叠按钮
            expanded = tk.BooleanVar(value=True)
            fold_btn = tk.Button(title_frame, text="[−]", font=("Arial", 10), width=3,
                                 bg=btn_bg, fg=accent, bd=0, relief="flat",
                                 activebackground=btn_bg, activeforeground=accent)
            fold_btn.pack(side="left", padx=(0, 5))

            # 组标题
            tk.Label(title_frame, text=group_name, font=("Arial", 10, "bold"),
                     fg=accent, bg=bg).pack(side="left")

            # 内容容器
            content_frame = tk.Frame(outer, bg=bg)
            content_frame.pack(fill="x", padx=(10, 0))  # 稍微缩进

            group_frames[group_name] = content_frame

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

            # 在内容容器内生成参数输入控件
            c_row = 0
            for key in keys:
                if key == "CROP_RATIO":
                    # 特殊处理：使用动态标签
                    tk.Label(content_frame, textvariable=self.crop_ratio_label_var, fg=accent, bg=bg,
                             font=("Arial", 9)).grid(row=c_row, column=0, sticky="w", padx=5)
                else:
                    label_text = param_names.get(key, key) + ":"
                    tk.Label(content_frame, text=label_text, fg=accent, bg=bg,
                             font=("Arial", 9)).grid(row=c_row, column=0, sticky="w", padx=(0, 2))

                # 根据 CONFIG 中的值类型创建控件
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

        # ---------- 功能开关（仍需在 left_frame 中添加） ----------
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
        self.record_listbox = record_listbox
        record_listbox.grid(row=row_right, column=0, columnspan=2, sticky="we", padx=5)
        row_right += 1

        btn_frame_records = tk.Frame(right_frame, bg=bg)
        btn_frame_records.grid(row=row_right, column=0, columnspan=2, pady=5)
        row_right += 1

        # 刷新记录列表的方法（主类保留，因为操作 UI 组件）
        def _refresh_record_list():
            if self.record_listbox.winfo_exists():
                self.record_listbox.delete(0, tk.END)
                records = self.record_manager.load_records()  # 修正
                for rec in records:
                    display = f"{rec['name']}  |  {rec.get('record_time', '')}"
                    self.record_listbox.insert(tk.END, display)

        def load_record_list():
            _refresh_record_list()

        def view_record():
            sel = record_listbox.curselection()
            if not sel:
                return
            records = self.record_manager.load_records()  # 修正
            idx = sel[0]
            if idx >= len(records):
                return
            rec = records[idx]
            self.record_manager.show_record_detail(rec, idx)  # 修正

        def edit_record():
            sel = record_listbox.curselection()
            if not sel:
                return
            records = self.record_manager.load_records()  # 修正
            idx = sel[0]
            if idx >= len(records):
                return
            rec = records[idx]
            # 编辑对话框保持不变，但需修改内部 save 函数
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
                    self.record_manager.save_records(records)  # 修正
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
            records = self.record_manager.load_records()          # 修正
            idx = sel[0]
            if idx >= len(records):
                return
            import tkinter.messagebox as messagebox
            if messagebox.askyesno("确认删除", "确定删除该记录吗？"):
                self.record_manager.delete_record(idx)           # 修正
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

        #row2 = tk.Frame(top_btn_frame, bg=bg)
        # row2.pack(fill="x", pady=(2, 5))
        tk.Button(row1, text="显示截图范围", command=self.show_crop_region, bg=btn_bg, fg=accent).pack(side="left", padx=3)
        # 保存运行记录：委托给 record_manager
        tk.Button(row1, text="保存运行记录", command=self.record_manager._save_record,
                  bg=btn_bg, fg=accent).pack(side="left", padx=3)



        return entries


    # ==================== 颜色工具 ====================
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
        统一捕获与检测循环，兼容实时模式和视频模式。
        实时模式仍使用 dxcam，视频模式使用 FrameSource。
        """
        target_fps = 48
        target_interval = 1.0 / target_fps
        next_frame_time = time.perf_counter()

        oped_sample_interval = CONFIG["OPED_SAMPLE_INTERVAL_SEC"]

        while not self._stop_event.is_set():
            if self.video_mode:
                # 视频模式下，如果暂停则等待，否则不等待直接处理
                if not self._run_event.is_set():
                    self._run_event.wait(timeout=0.05)  # 暂停时短暂等待
                    continue
            else:
                self._run_event.wait(timeout=0.1)
            if self._stop_event.is_set():
                break

            # 帧率控制（视频模式下跳过，因为有自己的播放速度）
            if not self.video_mode:
                now = time.perf_counter()
                if now < next_frame_time:
                    sleep_time = next_frame_time - now
                    if sleep_time > 0.002:
                        time.sleep(sleep_time * 0.8)
                    while time.perf_counter() < next_frame_time:
                        pass

            # 获取当前帧
            if self.video_mode and self.frame_source is not None:
                ts, now_frame, _ = self.frame_source.next_frame()
                if now_frame is None:
                    # 视频结束
                    self.pause_video()  # 自动暂停
                    continue
                if not self.video_is_playing:
                    # 暂停状态，不处理新帧，等待恢复
                    time.sleep(0.001)
                    continue
                # 视频模式下，时间基准为视频时间戳
                now_ts = time.time()
                self.video_clock = ts  # 更新视频时钟
            else:
                # 实时模式：使用 dxcam
                frame = self.camera.get_latest_frame()
                if frame is None:
                    with self.lock:
                        last = self.last_frame.copy() if self.last_frame is not None else np.zeros(self.frame_shape,
                                                                                                   dtype=np.uint8)
                    now_frame = last
                else:
                    l, t, r, b = self.region
                    frame = frame[t:b, l:r]
                    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                    now_frame = cv2.resize(gray, (0, 0), fx=CONFIG["SCALE_FACTOR"], fy=CONFIG["SCALE_FACTOR"])
                ts = time.time()  # 实时模式时间戳
                self.video_clock = ts  # 兼容后续代码

            # 更新帧率目标（仅实时模式）
            if not self.video_mode:
                if time.perf_counter() > next_frame_time + target_interval:
                    next_frame_time = time.perf_counter() + target_interval
                else:
                    next_frame_time += target_interval

            # 获取上一帧（线程安全）
            with self.lock:
                last = self.last_frame.copy() if self.last_frame is not None else None

            # ========== 新增：确保上一帧与当前帧尺寸一致 ==========
            if last is not None and last.shape != now_frame.shape:
                last = None
            new_cel = 0
            raw_new_cel = 0
            move_type = "静止"

            if last is not None:
                # 原始变化检测
                raw_detected = is_raw_change(last, now_frame)
                raw_new_cel = 1 if raw_detected else 0

                basic_detected, move_type = basic_is_new_cel(last, now_frame)
                if basic_detected:
                    self.raw_detection_timestamps.append(ts)  # 使用时间戳

                # 动态过滤窗口维护
                window_sec = CONFIG["FILTER_TRIGGER_WINDOW_MS"] / 1000.0
                while self.raw_detection_timestamps and self.raw_detection_timestamps[0] < ts - window_sec:
                    self.raw_detection_timestamps.popleft()

                if len(self.raw_detection_timestamps) >= CONFIG["FILTER_TRIGGER_COUNT"]:
                    if not self.full_filter_active:
                        self.full_filter_active = True
                    self.full_filter_active_until = ts + CONFIG["FULL_FILTER_HOLD_SEC"]
                elif self.full_filter_active and ts > self.full_filter_active_until:
                    self.full_filter_active = False

                if self.full_filter_active:
                    final_detected, move_type = full_is_new_cel(last, now_frame, self.use_optical_flow,
                                                                use_gpu=self.use_gpu)
                else:
                    final_detected = basic_detected

                # 哈希过滤
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

                # 过滤统计
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

                if self.use_hash_filter:
                    with self.lock:
                        self.frame_buffer.append(curr_hash)
            else:
                if self.use_hash_filter:
                    with self.lock:
                        self.frame_buffer.append(compute_hash(now_frame))

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

            # 实时张数滑动窗口（使用视频时钟 ts 代替真实时间）
            self.recent_frames.append((ts, new_cel))
            self.recent_sum += new_cel
            while self.recent_frames and ts - self.recent_frames[0][0] > 1.0:
                old = self.recent_frames.popleft()
                self.recent_sum -= old[1]

            self.recent_frames_raw.append((ts, raw_new_cel))
            self.recent_sum_raw += raw_new_cel
            while self.recent_frames_raw and ts - self.recent_frames_raw[0][0] > 1.0:
                old = self.recent_frames_raw.popleft()
                self.recent_sum_raw -= old[1]

            span = ts - self.recent_frames[0][0] if self.recent_frames else 0.001
            cels = self.recent_sum / max(span, 0.001) if self.recent_frames else 0.0
            span_raw = ts - self.recent_frames_raw[0][0] if self.recent_frames_raw else 0.001
            cels_raw = self.recent_sum_raw / max(span_raw, 0.001) if self.recent_frames_raw else 0.0

            with self.lock:
                self.current_cels = cels
                self.current_raw_cels = cels_raw
                self.last_move_type = move_type if last is not None else "静止"

            # OP/ED 采样（使用视频时钟）
            if CONFIG["OPED_DETECTION_ENABLED"] and ts - self.oped_detector.last_sample_time >= oped_sample_interval:
                with self.lock:
                    fc = self.current_cels
                    rc = self.current_raw_cels
                frame_hash = compute_oped_hash(now_frame) if CONFIG.get("OPED_HASH_ENABLED", True) else None
                self.oped_detector.sample(self.video_clock, fc, rc, frame_hash)
                self.instant_fps_history.append((self.video_clock, self.current_cels))
    # ==================== UI 更新与波形 ====================
    def loop(self):
        """主 UI 更新循环（兼容视频时钟）"""
        if self.is_running:
            now_real = time.time()
            # 使用视频时钟或真实时钟
            if self.video_mode:
                # 视频模式下 self.video_clock 由 _capture_loop 更新
                current_clock = self.video_clock
            else:
                self.elapsed_time += now_real - self.last_loop_time
                current_clock = self.elapsed_time
            self.last_loop_time = now_real
            self._current_clock = current_clock  # 同步全局时钟
            # 每秒记录历史（使用 current_clock）
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
            net_time = current_clock - self.oped_detector.deducted_time  # 扣除OP/ED时间

            # 静止自动暂停检测（使用 net_cels 和 net_time）
            if has_frame:
                if net_cels != self._prev_cels_count:
                    self.idle_start_time = None
                elif self.idle_start_time is None:
                    self.idle_start_time = self._current_clock
                elif self._current_clock - self.idle_start_time >= CONFIG["JingzhiShiJian"]:
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

                avg_net = net_cels / net_time if net_time > 0 else 0.0

                self.lb_rt.config(text=f"实时张数：{real:.1f} ")
                self.lb_total_time.config(text=f"运行时长：{net_time:.1f} s")
                self.lb_total_cels.config(text=f"总张数：{net_cels}")
                self.lb_avg.config(text=f"总平均：{avg_net:.1f}")
                status = self.get_status(real)
                self.lb_st.config(text=status)
                self.last_refresh = now_real
                self._last_disp_cels = current_total


            # 视频模式下更新进度条
            if self.video_mode and self.video_controls:
                self._update_video_controls()

        self.root.after(20, self.loop)

    def _update_video_controls(self):
        """更新视频进度条和时间标签"""
        if not self.video_controls or self.frame_source is None:
            return
        current_time, duration, cur_frame, total_frames = self.frame_source.get_progress()
        progress = (current_time / duration * 100) if duration > 0 else 0
        self.video_controls['progress_var'].set(progress)
        time_str = f"{self._format_time(current_time)}/{self._format_time(duration)}"
        self.video_controls['time_label'].config(text=time_str)

    def _format_time(self, seconds):
        m, s = divmod(int(seconds), 60)
        return f"{m:02d}:{s:02d}"

    def _on_seek(self, value):
        """进度条拖动回调"""
        if self.frame_source is None:
            return
        total_dur = self.frame_source.video_duration
        pos = float(value) / 100.0 * total_dur
        self.frame_source.seek(pos)
        # 重置一些内部状态
        self._last_recorded_time = -1
        with self.lock:
            self.last_frame = None

    def _on_speed_change(self, value):
        """倍速切换"""
        speed = float(value.replace('x', ''))
        CONFIG["VIDEO_PLAYBACK_SPEED"] = speed
        # 视频模式下通过控制帧读取间隔实现倍速，需调整捕获循环
        # 简单方法：在 _capture_loop 中动态调整目标间隔
        # 这里记录速度，在循环中动态调整 target_interval
        self.video_playback_speed = speed

    def pause_video(self):
        self.video_is_playing = False
        self.is_running = False
        self._run_event.clear()
        if self.video_controls:
            self.video_controls['play_var'].set(False)
            self.video_controls['play_btn'].config(text="▶")

    def resume_video(self):
        self.video_is_playing = True
        self.is_running = True
        self._run_event.set()
        self.last_loop_time = time.time()
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
        self._current_clock = 0.0
        self.last_loop_time = time.time()
        self.start_real_time = time.time()  # 重置后重新记录开始时间
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

        # 重置 OP/ED 检测器
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
            self.frame_source.seek(0)  # 从头开始
            self.video_clock = 0.0
    def _save_profile(self):
        """保存 profiling 数据到文件"""
        try:
            self.profiler.disable()
            self.profiler.dump_stats("profile_output.prof")
            print("[Profiler] 数据已保存至 profile_output.prof")
        except:
            pass
    def _on_close(self):
        """关闭程序"""
        self._save_profile()
        self._stop_event.set()
        self._run_event.set()
        self.preview_manager.stop()
        if self.frame_source:
            self.frame_source.close()
        # 等待 capture 线程结束（最多 2 秒）
        if hasattr(self, 'capture_thread') and self.capture_thread.is_alive():
            self.capture_thread.join(timeout=2.0)

        # 等待预览线程结束
        if hasattr(self.preview_manager,
                   'thread') and self.preview_manager.thread and self.preview_manager.thread.is_alive():
            self.preview_manager.thread.join(timeout=1.0)

        self.root.destroy()


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
        elif mt in ("新作画", "新作画(基础)"):
            if v < 5.0:   return base + "微动"
            elif v < 8.0: return base + "一拍三"
            elif v < 13.0: return base + "一拍二"
            elif v < 20.0: return base + "一拍一"
            else:          return base + "全动画"
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
            self.wave_data.append((self._current_clock, cels))
            self.wave_raw_data.append((self._current_clock, raw_cels))

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
            self.wave2_data.append((self._current_clock, self.smooth_cels))
            self.wave2_raw_data.append((self._current_clock, self.smooth_raw_cels))
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
        now = self._current_clock
        start = now - history_sec
        accent = color_manager.get_color('accent')
        self._draw_axes_only(canvas, plot_w, plot_h, y_max, accent)
        if title:
            canvas.create_text(self.wave_margin_left, self.wave_margin_top - 5,
                               text=title, fill=accent, font=("Arial", 8), anchor="nw")

        # OP/ED 高亮
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