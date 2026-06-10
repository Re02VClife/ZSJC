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
        "filter_zoom": "#FFA07A",
        "filter_filtered_total": "#E6397C"
    },
    "USE_CUSTOM_COLORS": True,
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

def get_all_colors():
    """返回当前颜色字典的深拷贝（用于持久化）"""
    return copy.deepcopy(CONFIG["COLORS"])

def set_all_colors(colors_dict):
    """批量更新颜色字典并刷新主题"""
    CONFIG["COLORS"].update(colors_dict)
    if color_manager:
        color_manager._config = CONFIG["COLORS"]
        color_manager.apply_theme()