import copy
import os
import sys
import tkinter as tk  # ColorManager 中用到，需要导入

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



# ==================== 配置参数====================
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

    "FLOW_QUALITY_LEVEL": 0.03,        # 角点检测质量阈值，降低以增加点数
    "FLOW_MIN_DISTANCE": 12,           # 角点最小间距，减小以允许更密集


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


def get_settings_path():
    if getattr(sys, 'frozen', False):
        return os.path.join(os.path.expanduser("~"), "cel_counter_settings.json")
    return "cel_counter_settings.json"