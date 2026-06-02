# config.py
import copy

# ==================== 配置参数 ====================
CONFIG = {
    "USE_CUSTOM_COLORS": True,
    "SHOW_INFO_PANEL": True,
    "SHOW_WAVE1": True,
    "SHOW_WAVE2": True,
    "REFRESH_INTERVAL": 0.4,
    "SCALE_FACTOR": 0.5,
    "SSIM_THRESHOLD": 0.90,
    "WAVE_HISTORY_SEC": 60,
    "WAVE_REFRESH_MS": 200,
    "WAVE2_HISTORY_SEC": 1500,
    "WAVE2_REFRESH_MS": 3000,
    "JingzhiShiJian": 60,
    "WAVE_MAX_Y": 24,
    "CROP_RATIO": 0.7,
    "CROP_REGION": None,
    "ALPHA": 1,
    "DIFF_THRESHOLD": 22,
    "MIN_DIFF_THRESHOLD": 5,
    "MAX_DIFF_THRESHOLD": 30,
    "MIN_CHANGE_RATIO": 0.002,
    "SIGNIFICANT_CHANGE_RATIO": 0.01,
    "ALIGNED_CHANGE_THRESHOLD": 0.012,
    "FLOW_FEATURE_COUNT": 200,
    "FLOW_STATIC_THRESH": 1,
    "FLOW_MEDIAN_SHIFT_MIN": 0.01,
    "FLOW_LAYER_STATIC_THRESH": 2.0,
    "FLOW_LAYER_CONSISTENCY": 0.75,
    "SUBTITLE_BOTTOM_RATIO": 0.12,
    "FLOW_QUALITY_LEVEL": 0.03,
    "FLOW_MIN_DISTANCE": 12,
    "SUBTITLE_CONTRAST_FILTER": True,
    "SUBTITLE_GRADIENT_THRESH": 50,
    "SUBTITLE_DENSITY_THRESH": 1,
    "FRAME_BUFFER_SIZE": 24,
    "HASH_THRESHOLD": 1,
    "TOTAL_WAVE_REFRESH_SEC": 2,
    "FILTER_TRIGGER_WINDOW_MS": 200,
    "FILTER_TRIGGER_COUNT": 3,
    "FULL_FILTER_HOLD_SEC": 5,
    "BASIC_CORR_THRESHOLD": 0.995,
    "BASIC_MIN_RAW_RATIO_STILL": 0.003,
    "BASIC_SIGNIFICANT_RATIO": 0.01,
    "FULL_CORR_THRESHOLD": 0.985,
    "FULL_STILL_RATIO": 0.01,
    "LOCAL_AREA_THRESH": 0.003,
    "LOCAL_BBOX_RATIO_MAX": 0.8,
    "LOCAL_ASPECT_RATIO_MAX": 8,
    "LAYER_MIN_VALID_POINTS": 15,
    "LAYER_MIN_MOVING_POINTS": 10,
    "LAYER_DIRECTION_CONSISTENCY": 0.75,
    "LAYER_MEAN_VEC_MIN": 0.1,
    "LAYER_COS_SIM_THRESH": 0.7,
    "PREVIEW_DENSE_ALPHA": 0.6,
    "PREVIEW_DIFF_DECAY": 0.7,
    "PREVIEW_MOTION_DECAY": 0.85,
    "PREVIEW_MOTION_MAX_SPEED": 50,
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
}
CONFIG_DEFAULT = copy.deepcopy(CONFIG)

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

# 创建全局颜色管理器实例
color_manager = ColorManager(CONFIG["COLORS"])