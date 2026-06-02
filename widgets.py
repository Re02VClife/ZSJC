# widgets.py
import tkinter as tk
from config import color_manager

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