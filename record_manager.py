# record_manager.py
"""
运行记录管理器：保存/加载/查看/编辑/导出运行记录。
"""
import json, os, time, csv, tkinter as tk
from tkinter import filedialog, messagebox
import tkinter.ttk as ttk
import numpy as np
from config import color_manager, get_settings_path

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