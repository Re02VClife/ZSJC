# ui.py
import tkinter as tk
import json
import os
import sys
from config import CONFIG, CONFIG_DEFAULT, color_manager
from widgets import DraggablePanel

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
    return ui

def create_settings_window(parent, counter):
    """
    创建并返回设置窗口，内部绑定所有控件和回调。
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
        counter._stop_preview()
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
    # 参数面板
    param_panel = DraggablePanel(main_frame, "参数设置", 400, 650)
    panels['params'] = param_panel
    counter.param_entries = counter._build_params_content(param_panel.content, win, panels)

    # 总张数波形面板
    wave_panel = DraggablePanel(main_frame, "总张数波形", 600, 400)
    panels['wave'] = wave_panel
    counter.settings_wave_canvas = tk.Canvas(wave_panel.content, bg=bg, highlightthickness=0)
    counter.settings_wave_canvas.pack(fill=tk.BOTH, expand=True)

    # 实时预览面板
    preview_panel = DraggablePanel(main_frame, "实时预览", 600, 400)
    panels['preview'] = preview_panel
    control_bar = tk.Frame(preview_panel.content, bg=bg)
    control_bar.pack(fill="x", pady=(0, 5))

    preview_toggle_var = tk.BooleanVar(value=False)
    tk.Checkbutton(control_bar, text="开启实时预览", variable=preview_toggle_var,
                   fg=accent, bg=bg, selectcolor="#222222",
                   command=lambda: counter._on_preview_toggle(preview_toggle_var.get())
                   ).pack(side="left", padx=3)

    show_dense_var = tk.BooleanVar(value=True)
    tk.Checkbutton(control_bar, text="稠密光流", variable=show_dense_var,
                   fg=accent, bg=bg, selectcolor="#222222",
                   command=lambda: setattr(counter.preview_manager, 'show_dense', show_dense_var.get())
                   ).pack(side="left", padx=3)

    show_sparse_var = tk.BooleanVar(value=True)
    tk.Checkbutton(control_bar, text="稀疏光流", variable=show_sparse_var,
                   fg=accent, bg=bg, selectcolor="#222222",
                   command=lambda: setattr(counter.preview_manager, 'show_sparse', show_sparse_var.get())
                   ).pack(side="left", padx=3)

    show_curr_var = tk.BooleanVar(value=True)
    tk.Checkbutton(control_bar, text="当前帧", variable=show_curr_var,
                   fg=accent, bg=bg, selectcolor="#222222",
                   command=lambda: setattr(counter.preview_manager, 'show_curr', show_curr_var.get())
                   ).pack(side="left", padx=3)

    show_diff_var = tk.BooleanVar(value=True)
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