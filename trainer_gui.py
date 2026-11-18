"""Slotbound v4 desktop trainer. UI events stay on the Tk main thread."""

import argparse
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import queue
import sys
import time
import tkinter as tk
from tkinter import ttk, filedialog

from hotkeys import Hotkeys
from game_paths import find_game_directory, save_directory
from symbol_assets import load_symbol_images, asset_directory
from trainer_engine import (DEFAULT_VALUES, DEFAULT_WEIGHTS, FEATURES, FEATURE_BY_KEY,
                            GRADES, GRADE_PRESETS, GRADE_PATHS, MODES, PARAMETERS, SYMBOLS, VERSION,
                            RuntimeWorker, compose_edits, number, validate_parameter,
                            grade_percentages, grade_distribution, normalize_grade_percentages)

BG = "#191c1f"
PANEL = "#22262a"
FIELD = "#2a3035"
LINE = "#373e44"
FG = "#e9edf0"
MUTED = "#99a4ad"
ORANGE = "#ffa34a"
GREEN = "#79c6a0"
RED = "#f09292"
FONT = ("Microsoft YaHei UI", 10)
SMALL = ("Microsoft YaHei UI", 9)
BOLD = ("Microsoft YaHei UI", 10, "bold")


class Toggle(tk.Canvas):
    def __init__(self, parent, command):
        self.ui_scale = parent.winfo_toplevel().ui_scale
        super().__init__(parent, width=round(38 * self.ui_scale), height=round(22 * self.ui_scale), bg=PANEL,
                         highlightthickness=0, takefocus=True, cursor="hand2")
        self.state = False
        self.command = command
        self.bind("<Button-1>", lambda event: self.flip())
        self.bind("<space>", lambda event: self.flip())
        self.bind("<Return>", lambda event: self.flip())
        self.bind("<FocusIn>", lambda event: self.draw())
        self.bind("<FocusOut>", lambda event: self.draw())
        self.draw()

    def set(self, value):
        self.state = bool(value)
        self.draw()

    def flip(self):
        self.set(not self.state)
        self.command(self.state)
        return "break"

    def draw(self):
        self.delete("all")
        track = ORANGE if self.state else "#4a535b"
        self.create_oval(1, 3, 17, 19, fill=track, outline=track)
        self.create_oval(21, 3, 37, 19, fill=track, outline=track)
        self.create_rectangle(9, 3, 29, 19, fill=track, outline=track)
        left = 21 if self.state else 3
        self.create_oval(left, 5, left + 14, 17, fill=FG, outline=FG)
        if self.focus_get() == self:
            self.create_rectangle(0, 0, 37, 21, outline=ORANGE, dash=(2, 2))
        self.scale("all", 0, 0, self.ui_scale, self.ui_scale)


class NumberInput(tk.Frame):
    def __init__(self, parent, value, minimum=0, maximum=1e9, step=1,
                 integer=False, command=None, error=None, width=7):
        super().__init__(parent, bg=PANEL)
        self.minimum, self.maximum = minimum, maximum
        self.step, self.integer = step, integer
        self.command, self.error = command, error
        self.last_value = value
        self.var = tk.StringVar(value="%g" % value)
        self.minus = tk.Button(self, text="−", command=lambda: self.adjust(-1),
                               width=2, bg=PANEL, fg=ORANGE, relief="flat", font=BOLD,
                               activebackground=FIELD, activeforeground=FG)
        self.minus.pack(side="left")
        self.entry = tk.Entry(self, textvariable=self.var, width=width, bg=FIELD,
                              fg=ORANGE, insertbackground=FG, justify="center",
                              relief="flat", font=FONT, highlightthickness=1,
                              highlightbackground=LINE, highlightcolor=ORANGE)
        self.entry.pack(side="left", ipady=3)
        self.plus = tk.Button(self, text="+", command=lambda: self.adjust(1),
                              width=2, bg=PANEL, fg=ORANGE, relief="flat", font=BOLD,
                              activebackground=FIELD, activeforeground=FG)
        self.plus.pack(side="left")
        self.entry.bind("<Return>", self.commit)
        self.entry.bind("<FocusOut>", self.commit)

    def get(self):
        return number(self.var.get(), self.minimum, self.maximum, self.integer)

    def set(self, value):
        self.last_value = value
        self.var.set("%g" % value)
        self.entry.configure(highlightbackground=LINE)

    def commit(self, event=None):
        try:
            value = self.get()
            self.entry.configure(highlightbackground=LINE)
            if value != self.last_value:
                self.last_value = value
                if self.command:
                    self.command(value)
            return True
        except ValueError as error:
            self.entry.configure(highlightbackground=RED)
            if self.error:
                self.error(str(error))
            return False

    def adjust(self, direction):
        try:
            value = self.get()
        except ValueError:
            value = self.last_value
        value = min(self.maximum, max(self.minimum, value + direction * self.step))
        self.var.set("%g" % value)
        self.commit()


class ScrollPage(tk.Frame):
    def __init__(self, parent):
        super().__init__(parent, bg=BG)
        self.canvas = tk.Canvas(self, bg=BG, highlightthickness=0)
        bar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.body = tk.Frame(self.canvas, bg=BG)
        self.window = self.canvas.create_window(0, 0, anchor="nw", window=self.body)
        self.body.bind("<Configure>", self.resize_body)
        self.canvas.bind("<Configure>", self.resize_view)

    def resize_body(self, event):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def resize_view(self, event):
        self.canvas.itemconfigure(self.window, width=event.width)


class Trainer(tk.Tk):
    def __init__(self, start_threads=True, scale=None):
        super().__init__()
        if scale is not None:
            self.tk.call("tk", "scaling", float(scale) * 96 / 72)
        self.ui_scale = float(self.tk.call("tk", "scaling")) / (96 / 72)
        self.symbol_images = load_symbol_images(self, self.ui_scale)
        self.symbol_small_images = load_symbol_images(self, self.ui_scale, logical_size=18)
        self.app_icon = tk.PhotoImage(master=self, file=str(asset_directory().parent / "app.png"))
        self.iconphoto(True, self.app_icon)
        self.title("Ansel · Slotbound 修改器 " + VERSION)
        self.configure(bg=BG)
        width = min(round(1100 * self.ui_scale), self.winfo_screenwidth() - 70)
        height = min(round(860 * self.ui_scale), self.winfo_screenheight() - 90)
        self.geometry("%dx%d" % (width, height))
        self.minsize(min(round(960 * self.ui_scale), width), min(round(660 * self.ui_scale), height))
        self.worker = RuntimeWorker()
        self.hotkeys = Hotkeys()
        self.started = start_threads
        self.enabled = set()
        self.values = dict(DEFAULT_VALUES)
        self.weights = dict(DEFAULT_WEIGHTS)
        self.custom = {}
        self.rows = {}
        self.inputs = {}
        self.tracked_controls = []
        self.config_job = None
        self.closing = False
        self.shutdown_complete = False
        self.log_lines = []
        self.keep_runs = tk.BooleanVar(value=True)
        self.topmost = tk.BooleanVar(value=False)
        self._style()
        self._build()
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind_all("<MouseWheel>", self.wheel)
        self.report_callback_exception = self.callback_error
        if start_threads:
            self.worker.start()
            self.hotkeys.start()
            self.after(100, self.poll)

    def _style(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TFrame", background=BG)
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", background=BG, foreground=MUTED,
                        font=FONT, padding=(16, 9))
        style.map("TNotebook.Tab", background=[("selected", PANEL), ("active", FIELD)],
                  foreground=[("selected", ORANGE), ("active", FG)])
        style.configure("Treeview", background=FIELD, fieldbackground=FIELD,
                        foreground=FG, font=SMALL, rowheight=round(26 * self.ui_scale),
                        bordercolor=LINE, lightcolor=LINE, darkcolor=LINE)
        style.configure("Treeview.Heading", background=PANEL, foreground=MUTED,
                        font=SMALL, relief="flat", bordercolor=LINE)
        style.map("Treeview.Heading", background=[("active", LINE)])
        style.configure("Vertical.TScrollbar", background=LINE, troughcolor=BG,
                        bordercolor=BG, arrowcolor=MUTED)
        style.configure("TButton", font=FONT, padding=(10, 5), background=FIELD,
                        foreground=FG, bordercolor=LINE, lightcolor=LINE, darkcolor=LINE)
        style.map("TButton", background=[("active", "#3a4249")],
                  foreground=[("disabled", MUTED)])
        style.configure("Accent.TButton", background=ORANGE, foreground=BG,
                        bordercolor=ORANGE)
        style.map("Accent.TButton", background=[("active", "#ffb774")])
        style.configure("TCombobox", font=FONT, fieldbackground=FIELD, background=FIELD,
                        foreground=FG, arrowcolor=ORANGE, bordercolor=LINE)
        style.map("TCombobox", fieldbackground=[("readonly", FIELD)],
                  foreground=[("readonly", FG)])
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", background=BG, foreground=MUTED,
                        padding=(18, 9), font=FONT)
        style.map("TNotebook.Tab", background=[("selected", PANEL)],
                  foreground=[("selected", ORANGE)])
        style.configure("Treeview", background=PANEL, fieldbackground=PANEL,
                        foreground=FG, font=SMALL, rowheight=26, bordercolor=LINE)
        style.configure("Treeview.Heading", background=FIELD, foreground=MUTED,
                        font=SMALL, relief="flat")
        style.map("Treeview", background=[("selected", "#55422e")])
        self.option_add("*TCombobox*Listbox.background", FIELD)
        self.option_add("*TCombobox*Listbox.foreground", FG)
        self.option_add("*TCombobox*Listbox.selectBackground", "#55422e")
        self.option_add("*TCombobox*Listbox.font", FONT)

    def _build(self):
        header = tk.Frame(self, bg=BG)
        header.pack(fill="x", padx=22, pady=(16, 9))
        tk.Label(header, text="转运军团", bg=BG, fg=FG,
                 font=("Microsoft YaHei UI", 21, "bold")).pack(side="left")
        tk.Label(header, text="SLOTBOUND", bg=BG, fg=ORANGE,
                 font=("Microsoft YaHei UI", 11, "bold")).pack(side="left", padx=(14, 0),
                                                              pady=(10, 0))
        tk.Label(header, text="v" + VERSION + "  /  1.0.0.7", bg=BG, fg=MUTED,
                 font=SMALL).pack(side="right", pady=(10, 0))
        connection = tk.Frame(self, bg=PANEL)
        connection.pack(fill="x", padx=22, pady=(0, 8))
        self.connection = tk.Label(connection, text="●  等待游戏", fg=ORANGE,
                                    bg=PANEL, font=FONT)
        self.connection.pack(side="left", padx=12, pady=9)
        self.hotkey_label = tk.Label(connection, text="小键盘快捷键 · Num Lock 开启",
                                     fg=MUTED, bg=PANEL, font=SMALL)
        self.hotkey_label.pack(side="right", padx=12)
        ttk.Button(connection, text="游戏目录…", command=self.choose_game_directory).pack(side="right", padx=8)
        self.stats = {}
        stats = tk.Frame(self, bg=BG)
        stats.pack(fill="x", padx=22, pady=(0, 7))
        titles = (("coins", "本局金币"), ("spins", "剩余旋转"),
                  ("base_hp", "基地生命"), ("wave", "波次"))
        for index, (key, title) in enumerate(titles):
            stats.columnconfigure(index, weight=1)
            group = tk.Frame(stats, bg=BG)
            group.grid(row=0, column=index, sticky="w")
            tk.Label(group, text=title, bg=BG, fg=MUTED, font=SMALL).pack(side="left")
            value = tk.Label(group, text="—", bg=BG, fg=FG, font=BOLD)
            value.pack(side="left", padx=(10, 0))
            self.stats[key] = value
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=18)
        self.pages = {}
        tabs = (("main", "抽奖与经济"), ("units", "单位与战力"),
                ("save", "局外存档"), ("advanced", "高级工具"))
        for key, name in tabs:
            page = ScrollPage(self.notebook)
            self.notebook.add(page, text=name)
            self.pages[key] = page
        self.build_main(self.pages["main"].body)
        self.build_units(self.pages["units"].body)
        self.build_save(self.pages["save"].body)
        self.build_advanced(self.pages["advanced"].body)
        self.notebook.bind("<<NotebookTabChanged>>", self.tab_changed)
        self.log_frame = tk.Frame(self, bg=BG)
        self.log = tk.Text(self.log_frame, height=6, bg=BG, fg=MUTED,
                           font=("Consolas", 9), state="disabled", relief="flat", wrap="word")
        self.log.pack(fill="both", expand=True)
        footer = tk.Frame(self, bg=PANEL)
        footer.pack(fill="x", side="bottom", padx=18, pady=(8, 12))
        controls = tk.Frame(footer, bg=PANEL)
        controls.pack(fill="x", padx=10, pady=(9, 6))
        self.pause_button = ttk.Button(controls, text="暂停热键", command=self.pause_hotkeys)
        self.pause_button.pack(side="left")
        ttk.Button(controls, text="关闭并恢复", command=self.restore).pack(side="left", padx=7)
        tk.Checkbutton(controls, text="跨局保持", variable=self.keep_runs,
                       command=self.schedule_config, fg=FG, bg=PANEL, selectcolor=FIELD,
                       activebackground=PANEL, activeforeground=FG, font=SMALL).pack(side="left", padx=8)
        tk.Checkbutton(controls, text="置顶", variable=self.topmost,
                       command=lambda: self.attributes("-topmost", self.topmost.get()),
                       fg=MUTED, bg=PANEL, selectcolor=FIELD, activebackground=PANEL,
                       activeforeground=FG, font=SMALL).pack(side="left")
        ttk.Button(controls, text="日志", command=self.toggle_log).pack(side="right")
        tk.Label(controls, text="Ctrl + Shift + Home  暂停／恢复热键", font=SMALL,
                 fg=MUTED, bg=PANEL).pack(side="right", padx=10)
        self.feedback = tk.Label(footer, text="等待游戏，进入后自动连接", bg=PANEL,
                                 fg=MUTED, font=SMALL, anchor="w", justify="left",
                                 wraplength=950)
        self.feedback.pack(fill="x", padx=12, pady=(0, 9))

    def section(self, parent, title):
        frame = tk.Frame(parent, bg=PANEL)
        heading = tk.Frame(frame, bg=PANEL)
        heading.pack(fill="x", padx=13, pady=(12, 6))
        tk.Label(heading, text=title, font=BOLD, fg=FG, bg=PANEL).pack(side="left")
        return frame

    def feature_row(self, parent, key, shortcut="", numeric=False, suffix=""):
        feature = FEATURE_BY_KEY[key]
        line = tk.Frame(parent, bg=PANEL)
        line.pack(fill="x", padx=13, pady=(3, 0))
        head = tk.Frame(line, bg=PANEL)
        head.pack(fill="x", pady=(4, 1))
        toggle = Toggle(head, lambda value: self.toggle_feature(key, value))
        toggle.pack(side="left", padx=(0, 9))
        tk.Label(head, text=feature.title, bg=PANEL, fg=FG, font=FONT).pack(side="left")
        self.rows[key] = {"toggle": toggle}
        if numeric:
            if suffix:
                tk.Label(head, text=suffix, bg=PANEL, fg=MUTED, font=SMALL).pack(side="right")
            control = NumberInput(head, feature.default, feature.minimum, feature.maximum,
                                  feature.step, feature.integer,
                                  command=lambda value: self.set_value(key, value),
                                  error=self.show_error, width=6)
            control.pack(side="right", padx=(5, 2))
            self.inputs[key] = control
            self.tracked_controls.append(control.plus)
        lower = tk.Frame(line, bg=PANEL)
        lower.pack(fill="x", padx=(47, 0), pady=(0, 6))
        lower.columnconfigure(0, weight=1)
        timing = feature.timing + (" · " + shortcut if shortcut else "")
        timing_label = tk.Label(lower, text=timing, bg=PANEL, fg=MUTED, font=SMALL,
                                wraplength=260, anchor="w", justify="left")
        timing_label.grid(row=0, column=0, sticky="w")
        status = tk.Label(lower, text="关闭", bg=PANEL, fg=MUTED, font=SMALL)
        status.grid(row=0, column=1, sticky="e", padx=(8, 0))
        lower.bind("<Configure>", lambda event: timing_label.configure(
            wraplength=max(80, event.width - status.winfo_reqwidth() - 10)))
        self.rows[key]["status"] = status
        tk.Frame(parent, height=1, bg=LINE).pack(fill="x", padx=13)
        return line

    def combo(self, parent, values, variable, command, width=14):
        control = ttk.Combobox(parent, values=values, textvariable=variable,
                               state="readonly", width=width, font=FONT)
        control.bind("<<ComboboxSelected>>", lambda event: command(variable.get()))
        return control

    def build_main(self, parent):
        quick = self.section(parent, "快捷修改")
        quick.pack(fill="x", padx=4, pady=(10, 0))
        quick_grid = tk.Frame(quick, bg=PANEL)
        quick_grid.pack(fill="x", padx=13, pady=(0, 6))
        shortcuts = (
            ("一键补给 · Num 0\n金币 +10000 / 免费抽 +20", lambda: self.worker.submit("quick_supply")),
            ("金币 +10000", lambda: self.quick_resource("coins", 10000)),
            ("免费抽 +20", lambda: self.quick_resource("spins", 20)),
            ("稀有优先\n一键开启品质配比", lambda: self.quick_profile("rare")),
            ("全传说\n一键开启 100% 传说", lambda: self.quick_profile("legendary")),
            ("部队 ×2\n攻击 / 生命 / 攻速", lambda: self.quick_profile("troops")),
        )
        self.quick_buttons = []
        for index, (label, command) in enumerate(shortcuts):
            quick_grid.columnconfigure(index % 3, weight=1, uniform="quick")
            button = tk.Button(quick_grid, text=label, command=command, font=SMALL,
                               bg=FIELD, fg=FG, activebackground=LINE, activeforeground=ORANGE,
                               relief="flat", borderwidth=0, padx=6, pady=7)
            button.grid(row=index // 3, column=index % 3, sticky="nsew", padx=3, pady=3)
            self.quick_buttons.append(button)
        tk.Label(quick, text="资源立即增加；品质与部队按钮直接启用对应功能，可在下方或单位页关闭。",
                 bg=PANEL, fg=MUTED, font=SMALL, anchor="w", wraplength=950).pack(
            fill="x", padx=13, pady=(0, 8))
        resources = self.section(parent, "本局即时操作")
        resources.pack(fill="x", padx=4, pady=(10, 9))
        grid = tk.Frame(resources, bg=PANEL)
        grid.pack(fill="x", padx=13, pady=(1, 12))
        self.resources = {}
        specs = (("coins", "本局金币", 1000, "Num 4 加值"),
                 ("spins", "免费旋转次数", 5, "Num 3 增加 · 设值后全部免费"),
                 ("base_hp", "基地生命", 100, "Num 5 设值"))
        for index, (key, title, value, shortcut) in enumerate(specs):
            grid.columnconfigure(index, weight=1)
            cell = tk.Frame(grid, bg=PANEL)
            cell.grid(row=0, column=index, sticky="nw", padx=(0, 12))
            tk.Label(cell, text=title + " · " + shortcut, font=SMALL,
                     fg=MUTED, bg=PANEL).pack(anchor="w", pady=(0, 4))
            control = NumberInput(cell, value, integer=True, error=self.show_error, width=7)
            control.pack(side="left")
            self.resources[key] = control
            if key != "base_hp":
                ttk.Button(cell, text="增加",
                           command=lambda k=key: self.resource(k, "add")).pack(side="left", padx=4)
            ttk.Button(cell, text="设值",
                       command=lambda k=key: self.resource(k, "set")).pack(side="left", padx=3)
        columns = tk.Frame(parent, bg=BG)
        columns.pack(fill="x", padx=4, pady=(0, 9))
        columns.columnconfigure(0, weight=1, uniform="main")
        columns.columnconfigure(1, weight=1, uniform="main")
        draw = self.section(columns, "抽奖控制")
        draw.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        economy = self.section(columns, "经济规则")
        economy.grid(row=0, column=1, sticky="nsew", padx=(7, 0))
        self.feature_row(draw, "draw", "Num 1")
        settings = tk.Frame(draw, bg=PANEL)
        settings.pack(fill="x", padx=13, pady=(9, 5))
        tk.Label(settings, text="模式", font=SMALL, fg=MUTED, bg=PANEL).pack(side="left", padx=(0, 10))
        self.mode_var = tk.StringVar(value=self.values["draw_mode"])
        self.combo(settings, MODES, self.mode_var, self.mode_changed, 15).pack(side="left")
        self.draw_options = tk.Frame(draw, bg=PANEL)
        self.draw_options.pack(fill="x", padx=13, pady=(0, 4))
        self.mode_hint = tk.Label(draw, text="", fg=MUTED, bg=PANEL, font=SMALL,
                                  justify="left", wraplength=420, anchor="w")
        self.mode_hint.pack(fill="x", padx=13, pady=(0, 6))
        self.mode_changed(self.values["draw_mode"], request=False)
        once = tk.Frame(draw, bg=PANEL)
        once.pack(fill="x", padx=13, pady=5)
        tk.Label(once, text="Num 2", fg=MUTED, bg=PANEL, font=("Consolas", 9)).pack(side="left")
        self.once_button = ttk.Button(once, text="下一抽必中", style="Accent.TButton",
                                      command=lambda: self.worker.submit("once_win"))
        self.once_button.pack(side="left", padx=9)
        ttk.Button(once, text="取消待命",
                   command=lambda: self.worker.submit("cancel_win")).pack(side="right")
        self.once_state = tk.Label(draw, text="由游戏自身的一次性标记处理",
                                   fg=MUTED, bg=PANEL, font=SMALL, anchor="w")
        self.once_state.pack(fill="x", padx=13, pady=(0, 6))
        self.feature_row(draw, "weights")
        weights = tk.Frame(draw, bg=PANEL)
        weights.pack(fill="x", padx=13, pady=(7, 4))
        self.weight_inputs = {}
        self.weight_share_labels = {}
        for index, (symbol, value) in enumerate(self.weights.items()):
            weights.columnconfigure(index % 3, weight=1, uniform="symbol_weights")
            cell = tk.Frame(weights, bg=PANEL)
            cell.grid(row=index // 3, column=index % 3, sticky="nsew", padx=3, pady=5)
            tk.Label(cell, text=SYMBOLS[symbol], image=self.symbol_images[symbol], compound="left",
                     fg=FG, bg=PANEL, font=SMALL).pack()
            control = NumberInput(cell, value, maximum=1e6, step=.05,
                                  command=lambda v, s=symbol: self.weight_changed(s, v),
                                  error=self.show_error, width=4)
            control.pack(pady=3)
            share = tk.Label(cell, text="", bg=PANEL, fg=MUTED, font=SMALL)
            share.pack()
            self.weight_share_labels[symbol] = share
            self.weight_inputs[symbol] = control
            self.tracked_controls.append(control.plus)
        self.weight_summary = tk.Label(draw, text="", bg=PANEL, fg=MUTED,
                                       font=SMALL, anchor="w", wraplength=420)
        self.weight_summary.pack(fill="x", padx=13, pady=(0, 10))
        self.update_weight_summary()
        self.feature_row(economy, "paid_free", "Num 6")
        self.feature_row(economy, "free_wave", numeric=True, suffix="次")
        self.feature_row(economy, "paid_max", numeric=True, suffix="次")
        self.feature_row(economy, "refund", numeric=True, suffix="金币")
        self.feature_row(economy, "reward", "Num 8", numeric=True, suffix="倍")

    def build_units(self, parent):
        columns = tk.Frame(parent, bg=BG)
        columns.pack(fill="x", padx=4, pady=10)
        columns.columnconfigure(0, weight=1, uniform="units")
        columns.columnconfigure(1, weight=1, uniform="units")
        quality = self.section(columns, "招募与成长")
        quality.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        power = self.section(columns, "备战部队属性")
        power.grid(row=0, column=1, sticky="nsew", padx=(7, 0))
        self.feature_row(quality, "grade", "Num 7")
        line = tk.Frame(quality, bg=PANEL)
        line.pack(fill="x", padx=13, pady=(12, 5))
        tk.Label(line, text="快捷配比", font=SMALL, fg=MUTED, bg=PANEL).pack(side="left", padx=(0, 12))
        self.grade_preset_var = tk.StringVar(value="全部传说")
        self.combo(line, ("自定义", *GRADE_PRESETS), self.grade_preset_var,
                   self.grade_preset_changed, 12).pack(side="left")
        distribution = tk.Frame(quality, bg=PANEL)
        distribution.pack(fill="x", padx=13, pady=4)
        distribution.columnconfigure(0, weight=1)
        self.grade_inputs = {}
        for index, (label, grade) in enumerate(GRADES.items()):
            tk.Label(distribution, text=label, font=FONT, fg=FG, bg=PANEL).grid(
                row=index, column=0, sticky="w", pady=3)
            control = NumberInput(distribution, self.values["grade_" + grade], 0, 100, 1,
                                  command=lambda value, key=grade: self.grade_changed(key, value),
                                  error=self.show_error, width=6)
            control.grid(row=index, column=1, sticky="e", pady=3)
            tk.Label(distribution, text="%", bg=PANEL, fg=MUTED, font=SMALL).grid(
                row=index, column=2, sticky="e", padx=(6, 0))
            self.grade_inputs[grade] = control
            self.tracked_controls.append(control.plus)
        self.grade_summary = tk.Label(quality, text="", font=SMALL, fg=MUTED,
                                      bg=PANEL, anchor="w", justify="left", wraplength=380)
        self.grade_summary.pack(fill="x", padx=13, pady=(4, 5))
        ttk.Button(quality, text="按比例归一到 100%", command=self.normalize_grades).pack(
            anchor="w", padx=13, pady=(0, 8))
        self.update_grade_summary()
        tk.Label(quality, text="用于中奖后生成新单位的品质。盘面命中率在抽奖页设置；特殊奖励可能使用独立品质规则。",
                 font=SMALL, fg=MUTED, bg=PANEL, justify="left", wraplength=380).pack(
            fill="x", padx=13, pady=(0, 10))
        self.feature_row(quality, "absorb", numeric=True, suffix="倍")
        self.feature_row(quality, "battle_speed", "Num 9", numeric=True, suffix="倍")
        for key in ("attack", "health", "speed"):
            self.feature_row(power, key, numeric=True, suffix="倍")
        tk.Label(power, text="应用到已招募部队，下次出战使用新属性。\n新单位会自动加入；关闭后移除本修改器的倍率。",
                 fg=MUTED, bg=PANEL, font=SMALL, justify="left",
                 wraplength=420).pack(anchor="w", padx=13, pady=16)
        tk.Label(parent, text="连败保底沿用游戏前期保护规则，从第 4 波进入连续付费抽奖保底。",
                 bg=BG, fg=MUTED, font=SMALL, wraplength=900,
                 justify="left").pack(anchor="w", padx=7, pady=8)

    def build_save(self, parent):
        area = self.section(parent, "局外进度")
        area.pack(fill="x", padx=4, pady=10)
        tk.Label(area, text="关闭游戏后应用，写入前自动保留存档备份。",
                 bg=PANEL, fg=MUTED, font=SMALL).pack(anchor="w", padx=13, pady=(0, 10))
        self.save_inputs = {}
        specifications = (("originite", "局外源质", 1000), ("runs", "累计局数", 0),
                          ("bosses", "累计 BOSS", 0), ("played", "游戏时长（秒）", 0))
        for name, title, value in specifications:
            row = tk.Frame(area, bg=PANEL)
            row.pack(fill="x", padx=13, pady=5)
            tk.Label(row, text=title, bg=PANEL, fg=FG, font=FONT,
                     width=18, anchor="w").pack(side="left")
            control = NumberInput(row, value, integer=True, error=self.show_error, width=10)
            control.pack(side="left")
            self.save_inputs[name] = control
            if name == "originite":
                ttk.Button(row, text="设定源质",
                           command=lambda: self.save_apply("originite")).pack(side="left", padx=15)
        actions = tk.Frame(area, bg=PANEL)
        actions.pack(fill="x", padx=13, pady=14)
        for title, key in (("解锁关卡", "unlock_stages"), ("解锁难度", "unlock_diff"),
                           ("标记通关", "clear_diff"), ("应用成就统计", "stats")):
            ttk.Button(actions, text=title,
                       command=lambda k=key: self.save_apply(k)).pack(side="left", padx=(0, 8))
        bottom = tk.Frame(area, bg=PANEL)
        bottom.pack(fill="x", padx=13, pady=(0, 13))
        ttk.Button(bottom, text="重新读取",
                   command=lambda: self.worker.submit("save_read")).pack(side="left")
        ttk.Button(bottom, text="恢复首次修改前存档",
                   command=lambda: self.worker.submit("save_restore")).pack(side="left", padx=8)
        self.save_summary = tk.Label(area, text="读取存档后显示当前值",
                                     bg=PANEL, fg=FG, font=FONT, anchor="w")
        self.save_summary.pack(fill="x", padx=13, pady=(0, 9))
        self.save_path = tk.Label(area, text="", bg=PANEL, fg=MUTED, font=SMALL,
                                  anchor="w", wraplength=900, justify="left")
        self.save_path.pack(fill="x", padx=13, pady=(0, 13))

    def build_advanced(self, parent):
        parameters = self.section(parent, "全部运行时参数")
        parameters.pack(fill="x", padx=4, pady=10)
        table_frame = tk.Frame(parameters, bg=PANEL)
        table_frame.pack(fill="x", padx=13)
        self.parameter_tree = ttk.Treeview(table_frame, columns=("name", "path", "current", "target"),
                                           show="tree headings", height=10, selectmode="browse")
        self.parameter_tree.column("#0", width=round(30 * self.ui_scale), minwidth=round(26 * self.ui_scale), stretch=False)
        for key, title, width in (("name", "项目", 250), ("path", "参数路径", 360),
                                   ("current", "当前值", 100), ("target", "手动覆盖", 100)):
            self.parameter_tree.heading(key, text=title)
            self.parameter_tree.column(key, width=round(width * self.ui_scale),
                                       minwidth=round(65 * self.ui_scale), stretch=key in {"name", "path"})
        bar = ttk.Scrollbar(table_frame, command=self.parameter_tree.yview)
        self.parameter_tree.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        self.parameter_tree.pack(fill="x", expand=True)
        for path, (original, description, group) in PARAMETERS.items():
            icon = self.symbol_small_images.get(path.rsplit(".", 1)[-1], "") if path.startswith("slot.symbolWeights.") else ""
            self.parameter_tree.insert("", "end", iid=path,
                                       image=icon, values=(description, path, "—", "—"))
        edit = tk.Frame(parameters, bg=PANEL)
        edit.pack(fill="x", padx=13, pady=12)
        self.parameter_value = NumberInput(edit, 1, minimum=-1e6, maximum=1e6,
                                          step=.1, error=self.show_error, width=10)
        self.parameter_value.pack(side="left")
        ttk.Button(edit, text="覆盖所选", command=self.parameter_apply).pack(side="left", padx=8)
        ttk.Button(edit, text="恢复所选", command=self.parameter_restore).pack(side="left")
        self.parameter_tree.bind("<<TreeviewSelect>>", self.parameter_selected)
        self.parameter_tree.bind("<Double-1>", lambda event: self.parameter_value.entry.focus_set())
        scanner = self.section(parent, "数值扫描器")
        scanner.pack(fill="x", padx=4, pady=(0, 10))
        top = tk.Frame(scanner, bg=PANEL)
        top.pack(fill="x", padx=13, pady=(0, 8))
        tk.Label(top, text="当前数值", fg=MUTED, bg=PANEL, font=SMALL).pack(side="left")
        self.scan_value = NumberInput(top, 100, -1e12, 1e12, error=self.show_error)
        self.scan_value.pack(side="left", padx=8)
        ttk.Button(top, text="首次扫描",
                   command=lambda: self.scan("scan_first")).pack(side="left", padx=5)
        ttk.Button(top, text="再次筛选",
                   command=lambda: self.scan("scan_next")).pack(side="left")
        self.scan_count = tk.Label(top, text="尚未扫描", fg=MUTED, bg=PANEL, font=SMALL)
        self.scan_count.pack(side="right")
        self.scan_tree = ttk.Treeview(scanner, columns=("address", "value", "lock"),
                                      show="headings", height=4, selectmode="browse")
        for key, title in (("address", "候选地址"), ("value", "当前值"), ("lock", "锁定")):
            self.scan_tree.heading(key, text=title)
            self.scan_tree.column(key, width=180, stretch=True)
        self.scan_tree.pack(fill="x", padx=13)
        bottom = tk.Frame(scanner, bg=PANEL)
        bottom.pack(fill="x", padx=13, pady=12)
        self.scan_new = NumberInput(bottom, 999, -1e12, 1e12, error=self.show_error)
        self.scan_new.pack(side="left")
        self.scan_lock = tk.BooleanVar(value=False)
        tk.Checkbutton(bottom, text="持续锁定所选", variable=self.scan_lock, fg=FG, bg=PANEL,
                       selectcolor=FIELD, activebackground=PANEL, activeforeground=FG,
                       font=SMALL).pack(side="left", padx=7)
        ttk.Button(bottom, text="写入所选", command=self.scan_write).pack(side="left", padx=7)
        ttk.Button(bottom, text="停止全部锁定",
                   command=lambda: self.worker.submit("scan_unlock")).pack(side="left")

    def set_value(self, key, value):
        self.values[key] = value
        self.schedule_config()

    def grade_changed(self, grade, value):
        self.values["grade_" + grade] = value
        self.grade_preset_var.set("自定义")
        self.update_grade_summary()
        self.schedule_config()

    def set_grade_percentages(self, percentages, preset="自定义"):
        for grade, percent in percentages.items():
            self.values["grade_" + grade] = percent
            self.grade_inputs[grade].set(percent)
        self.grade_preset_var.set(preset)
        self.update_grade_summary()
        self.schedule_config()

    def grade_preset_changed(self, name):
        if name in GRADE_PRESETS:
            self.set_grade_percentages(GRADE_PRESETS[name], name)

    def normalize_grades(self):
        try:
            draft = dict(self.values)
            for grade, control in self.grade_inputs.items():
                draft["grade_" + grade] = control.get()
            percentages = normalize_grade_percentages(draft)
        except ValueError as error:
            self.show_error(str(error))
            return
        self.set_grade_percentages(percentages)

    def update_grade_summary(self):
        percentages = grade_percentages(self.values)
        total = sum(percentages.values())
        valid = abs(total - 100) <= 1e-7
        hint = "可应用 · 下一次生成新单位生效" if valid else "需调整至 100% · 保留上次已应用配比"
        self.grade_summary.configure(text="合计 %g%% · %s" % (total, hint), fg=MUTED if valid else RED)
        if not valid and "grade" in self.enabled:
            self.rows["grade"]["status"].configure(text="待调整", fg=ORANGE)

    def toggle_feature(self, key, on):
        if on:
            self.enabled.add(key)
        else:
            self.enabled.discard(key)
        self.rows[key]["toggle"].set(on)
        self.schedule_config()

    def schedule_config(self):
        if self.config_job:
            self.after_cancel(self.config_job)
        self.config_job = self.after(100, self.configure_runtime)

    def configure_runtime(self):
        self.config_job = None
        if "grade" in self.enabled:
            try:
                for control in self.grade_inputs.values():
                    number(control.get(), 0, 100)
                grade_distribution(self.values)
            except ValueError as error:
                self.rows["grade"]["status"].configure(text="待调整", fg=ORANGE)
                self.show_error(str(error) + "；游戏保留上次已应用配置")
                return
        try:
            compose_edits(self.enabled, self.values, self.weights, self.custom,
                          lambda key: PARAMETERS[key[1]][0], in_run=True)
        except ValueError as error:
            self.show_error(str(error))
            return
        for key in self.enabled:
            self.rows[key]["status"].configure(text="待写入", fg=ORANGE)
        self.worker.submit("configure", enabled=list(self.enabled), values=dict(self.values),
                           weights=dict(self.weights), custom=dict(self.custom),
                           keep_across_runs=self.keep_runs.get())

    def choose_game_directory(self):
        current = None
        try:
            current = find_game_directory()
        except (OSError, ValueError) as error:
            self.log_message(str(error))
        try:
            chosen = filedialog.askdirectory(parent=self, title="选择 Slotbound 游戏目录",
                                             initialdir=str(current) if current else None)
            if chosen:
                path = save_directory(chosen)
                self.log_message("游戏目录已保存：" + str(path))
        except (OSError, ValueError) as error:
            self.show_error(str(error))

    def mode_changed(self, mode, request=True):
        self.values["draw_mode"] = mode
        for child in self.draw_options.winfo_children():
            child.destroy()
        hints = {"每抽必中": "在下次抽奖生成盘面时强制中奖。",
                 "提高中奖": "设置额外强制中奖概率，游戏原有自然中奖继续计算。",
                 "连败保底": "第 4 波起，连续未中时第 N 次付费抽奖保底。",
                 "同符号满盘": "使用所选符号覆盖转轴权重，在下次生成盘面时生效。"}
        self.mode_hint.configure(text=hints[mode])
        if mode in {"提高中奖", "连败保底"}:
            key = "chance" if mode == "提高中奖" else "pity"
            tk.Label(self.draw_options, text="强制概率（%）" if key == "chance" else "保底阈值",
                     font=SMALL, fg=MUTED, bg=PANEL).pack(side="left", padx=(0, 10))
            control = NumberInput(self.draw_options, self.values[key],
                                  0 if key == "chance" else 1, 100,
                                  5 if key == "chance" else 1, key == "pity",
                                  command=lambda value: self.set_value(key, value),
                                  error=self.show_error)
            control.pack(side="left")
        elif mode == "同符号满盘":
            self.symbol_var = tk.StringVar(value=self.values["symbol"])
            tk.Label(self.draw_options, text="目标符号", bg=PANEL, fg=MUTED,
                     font=SMALL).pack(anchor="w", pady=(0, 4))
            choices = tk.Frame(self.draw_options, bg=PANEL)
            choices.pack(fill="x")
            self.symbol_buttons = {}
            for index, (symbol, name) in enumerate(SYMBOLS.items()):
                choices.columnconfigure(index, weight=1, uniform="symbol_choices")
                button = tk.Radiobutton(choices, text=name, image=self.symbol_images[symbol],
                    compound="top", variable=self.symbol_var, value=symbol, indicatoron=False,
                    command=lambda key=symbol: self.select_symbol(key), font=SMALL,
                    bg=FIELD, fg=FG, selectcolor="#4c3a27", activebackground=LINE,
                    activeforeground=ORANGE, relief="flat", offrelief="flat", borderwidth=2,
                    padx=3, pady=4, highlightthickness=0)
                button.grid(row=0, column=index, sticky="nsew", padx=2)
                self.symbol_buttons[symbol] = button
        if request:
            self.schedule_config()

    def weight_changed(self, symbol, value):
        self.weights[symbol] = value
        self.update_weight_summary()
        self.schedule_config()

    def update_weight_summary(self):
        total = sum(self.weights.values())
        for symbol, value in self.weights.items():
            self.weight_share_labels[symbol].configure(text="%g%%" % round(value / total * 100, 1) if total else "—")
        self.weight_summary.configure(text="按图标下方的百分比抽取 · 权重总和 %g" % total if total else "权重总和需要大于零")

    def select_symbol(self, symbol):
        self.symbol_var.set(symbol)
        self.set_value("symbol", symbol)

    def quick_resource(self, resource, amount):
        self.worker.submit("resource", resource=resource, operation="add", value=amount)

    def quick_profile(self, name):
        if name in {"rare", "legendary"}:
            preset = "稀有优先" if name == "rare" else "全部传说"
            self.set_grade_percentages(GRADE_PRESETS[preset], preset)
            self.toggle_feature("grade", True)
        elif name == "troops":
            for key in ("attack", "health", "speed"):
                self.values[key] = 2
                self.inputs[key].set(2)
                self.enabled.add(key)
                self.rows[key]["toggle"].set(True)
            self.schedule_config()

    def resource(self, key, operation):
        try:
            value = self.resources[key].get()
            self.worker.submit("resource", resource=key, value=value, operation=operation)
        except ValueError as error:
            self.show_error(str(error))

    def save_apply(self, key):
        if key == "originite":
            names = ["originite"]
        elif key == "stats":
            names = ["runs", "bosses", "played"]
        else:
            names = []
        try:
            values = {name: self.save_inputs[name].get() for name in names}
            self.worker.submit("save_apply", key=key, values=values)
        except ValueError as error:
            self.show_error(str(error))

    def parameter_selected(self, event=None):
        selected = self.parameter_tree.selection()
        if selected:
            path = selected[0]
            displayed = self.parameter_tree.set(path, "current")
            value = self.custom.get(path, PARAMETERS[path][0] if displayed == "—" else float(displayed))
            self.parameter_value.set(value)

    def parameter_apply(self):
        selected = self.parameter_tree.selection()
        if not selected:
            self.show_error("请先选择参数")
            return
        try:
            path = selected[0]
            if path in GRADE_PATHS and "grade" in self.enabled:
                self.show_error("该概率正由单位页控制，请在单位页调整，或先关闭抽卡品质概率。")
                return
            self.custom[path] = validate_parameter(path, self.parameter_value.get())
            self.parameter_tree.set(path, "target", "%g" % self.custom[path])
            self.schedule_config()
        except ValueError as error:
            self.show_error(str(error))

    def parameter_restore(self):
        for path in self.parameter_tree.selection():
            self.custom.pop(path, None)
            self.parameter_tree.set(path, "target", "—")
        self.schedule_config()

    def scan(self, action):
        try:
            self.worker.submit(action, value=self.scan_value.get())
            self.scan_count.configure(text="扫描中…")
        except ValueError as error:
            self.show_error(str(error))

    def scan_write(self):
        selected = self.scan_tree.selection()
        if not selected:
            self.show_error("请选择要写入的候选地址")
            return
        try:
            self.worker.submit("scan_write", address=int(selected[0]), value=self.scan_new.get(),
                               lock=self.scan_lock.get())
        except ValueError as error:
            self.show_error(str(error))

    def restore(self):
        if self.config_job:
            self.after_cancel(self.config_job)
            self.config_job = None
        self.enabled.clear()
        self.custom.clear()
        for row in self.rows.values():
            row["toggle"].set(False)
        self.worker.submit("restore")
        self.feedback.configure(text="正在恢复本次覆盖…", fg=ORANGE)

    def pause_hotkeys(self):
        self.hotkeys.paused = not self.hotkeys.paused
        self.pause_button.configure(text="恢复热键" if self.hotkeys.paused else "暂停热键")

    def hotkey_action(self, action):
        if action == "pause":
            self.pause_hotkeys()
            return
        if self.hotkeys.foreground_pid == os.getpid() and isinstance(self.focus_get(), (tk.Entry, ttk.Entry, ttk.Combobox)):
            return
        if action in self.rows:
            self.toggle_feature(action, action not in self.enabled)
        elif action == "once_win":
            self.worker.submit("once_win")
        elif action == "add_spins":
            self.resource("spins", "add")
        elif action == "add_coins":
            self.resource("coins", "add")
        elif action == "base_hp":
            self.resource("base_hp", "set")
        elif action == "quick_supply":
            self.worker.submit("quick_supply")

    def tab_changed(self, event=None):
        selected = self.notebook.select()
        self.worker.submit("show_parameters", show=selected == str(self.pages["advanced"]))
        if selected == str(self.pages["save"]):
            self.worker.submit("save_read")

    def wheel(self, event):
        if isinstance(event.widget, (ttk.Treeview, tk.Text, ttk.Combobox)):
            return
        selected = self.notebook.select()
        for page in self.pages.values():
            if str(page) == selected:
                if page.body.winfo_height() > page.canvas.winfo_height():
                    page.canvas.yview_scroll(int(-event.delta / 120), "units")
                break

    def toggle_log(self):
        if self.log_frame.winfo_ismapped():
            self.log_frame.pack_forget()
        else:
            self.log_frame.pack(fill="x", padx=23, before=self.notebook)

    def show_error(self, message):
        self.feedback.configure(text=message, fg=RED)
        self.log_message(message)

    def log_message(self, message):
        logging.info(message)
        line = time.strftime("%H:%M:%S") + "  " + message
        self.log_lines.append(line)
        self.log_lines = self.log_lines[-200:]
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.insert("end", "\n".join(self.log_lines))
        self.log.see("end")
        self.log.configure(state="disabled")

    def handle_event(self, event, data):
        if event == "connection":
            self.connection.configure(text="●  已连接 · PID %d" % data["pid"], fg=GREEN)
            self.hotkeys.game_pid = data["pid"]
        elif event == "state":
            for key in ("coins", "base_hp"):
                value = data.get(key)
                displayed = "%g" % value if value is not None and data["in_run"] else "—"
                self.stats[key].configure(text=displayed)
            total = data.get("spins") or 0
            free = data.get("free_spins") or 0
            self.stats["spins"].configure(text="%g · 免费 %g" % (total, free) if data["in_run"] else "—")
            self.stats["wave"].configure(text="%g" % data["wave"] if data["in_run"] else "菜单")
        elif event == "applied":
            active = set(data["enabled"])
            for key, row in self.rows.items():
                label = "已写入" if key in active else "关闭"
                if key in {"battle_speed", "free_wave", "paid_max", "paid_free", "attack", "health", "speed"} and key in active and not data["in_run"]:
                    label = "待进入本局"
                elif key in {"attack", "health", "speed"} and key in active and not data.get("unit_count"):
                    label = "等待招募单位"
                elif key in {"free_wave", "paid_max"} and key in active:
                    label = "每波补充"
                elif key == "grade" and key in active:
                    try:
                        grade_distribution(self.values)
                    except ValueError:
                        label = "待调整"
                row["status"].configure(text=label, fg=GREEN if key in active else MUTED)
            self.feedback.configure(text="已验证 %d 项覆盖 · %d 项发生变化" %
                                    (data["count"], data["changed"]), fg=MUTED)
        elif event == "error":
            waiting = "未运行" in data["text"] or "等待" in data["text"]
            self.feedback.configure(text=data["text"], fg=ORANGE if waiting else RED)
            for key in self.enabled:
                self.rows[key]["status"].configure(text="等待游戏" if waiting else "应用失败",
                                                   fg=ORANGE if waiting else RED)
            if waiting:
                self.connection.configure(text="●  等待游戏", fg=ORANGE)
                self.hotkeys.game_pid = None
                for label in self.stats.values():
                    label.configure(text="—")
            if data.get("action"):
                self.log_message(data["text"])
            if self.closing:
                self.closing = False
                self.show_error("退出前恢复出现问题，请查看日志后重试。" + data["text"])
        elif event == "log":
            self.log_message(data["text"])
            self.feedback.configure(text=data["text"], fg=GREEN)
        elif event == "once":
            self.once_state.configure(text=data["text"], fg=ORANGE)
            self.log_message(data["text"])
        elif event == "cleared":
            self.enabled.clear()
            self.custom.clear()
            for row in self.rows.values():
                row["toggle"].set(False)
                row["status"].configure(text="关闭", fg=MUTED)
            for path in PARAMETERS:
                self.parameter_tree.set(path, "target", "—")
            self.feedback.configure(text=data["text"], fg=GREEN)
            self.log_message(data["text"])
            if self.closing:
                self.finish_close()
        elif event == "scan":
            selected = self.scan_tree.selection()
            self.scan_tree.delete(*self.scan_tree.get_children())
            for address, value in data["rows"]:
                displayed = "%g" % value if value is not None else "失效"
                self.scan_tree.insert("", "end", iid=str(address),
                                      values=(hex(address), displayed,
                                              "是" if address in data["locks"] else ""))
            if selected and self.scan_tree.exists(selected[0]):
                self.scan_tree.selection_set(selected[0])
            self.scan_count.configure(text="%d 个候选，显示前 150 个" % data["count"])
        elif event == "parameters":
            for path, value in data["rows"].items():
                self.parameter_tree.set(path, "current", "%g" % value if value is not None else "—")
                if path in GRADE_PATHS:
                    target = "单位页控制" if "grade" in self.enabled else (
                        "%g" % self.custom[path] if path in self.custom else "—")
                    self.parameter_tree.set(path, "target", target)
        elif event == "save":
            values = data["values"]
            self.save_summary.configure(text="源质 %s    局数 %s    BOSS %s    秒数 %s" %
                                       (values["originite"], values["runs"],
                                        values["bosses"], values["played"]))
            self.save_path.configure(text=data["path"] + "\n备份  " + data["backup"])
            for name in ("runs", "bosses", "played"):
                if self.focus_get() != self.save_inputs[name].entry:
                    self.save_inputs[name].set(float(values[name]))

    def poll(self):
        while True:
            try:
                event, data = self.worker.events.get_nowait()
            except queue.Empty:
                break
            self.handle_event(event, data)
            if self.shutdown_complete:
                return
        while True:
            try:
                event, data = self.hotkeys.events.get_nowait()
            except queue.Empty:
                break
            if event == "action":
                self.hotkey_action(data)
            else:
                active, paused, failed = data
                if paused:
                    text = "热键已暂停"
                elif active:
                    text = "热键可用 · 小键盘 1–9"
                else:
                    text = "热键待游戏前台"
                if failed:
                    text = "热键冲突  " + ", ".join("总开关" if key == 100 else "Num %d" % key for key in failed)
                self.hotkey_label.configure(text=text, fg=ORANGE if failed or paused else MUTED)
        self.after(100, self.poll)

    def callback_error(self, kind, value, trace):
        logging.error("UI callback", exc_info=(kind, value, trace))
        self.show_error(str(value))

    def close(self):
        if not self.started:
            self.destroy()
            return
        if not self.closing:
            self.closing = True
            self.restore()

    def finish_close(self):
        self.shutdown_complete = True
        self.worker.stop_requested.set()
        self.hotkeys.stop_requested.set()
        self.worker.join(timeout=2)
        self.hotkeys.join(timeout=1)
        self.destroy()


def setup_logging():
    directory = Path(os.environ.get("LOCALAPPDATA", ".")) / "SlotboundModifier"
    directory.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(directory / "trainer.log", maxBytes=2_000_000,
                                  backupCount=1, encoding="utf-8")
    logging.basicConfig(handlers=[handler], level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")






def main():
    parser = argparse.ArgumentParser(description="Ansel Slotbound Trainer")
    parser.add_argument("--verify-runtime", "--selftest", dest="verify_runtime", action="store_true",
                        help="Run windowless checks; no game data is bundled")
    parser.add_argument("--doctor", action="store_true", help="Write a windowless installation diagnostic")
    parser.add_argument("--game-dir", help="Use this game directory for this process")
    parser.add_argument("--result", default=str(Path(os.environ.get("TEMP", ".")) / "ansel-slotbound-checks.json"))
    args = parser.parse_args()
    if args.game_dir:
        os.environ["SLOTBOUND_GAME_DIR"] = args.game_dir
    if args.verify_runtime or args.doctor:
        import json
        report = Path(args.result).resolve()
        report.parent.mkdir(parents=True, exist_ok=True)
        try:
            if args.verify_runtime:
                from runtime_verify import verify
                return verify(report)
            from symbol_assets import asset_manifest
            directory = find_game_directory()
            result = {"version": VERSION, "passed": True, "game_directory": str(directory) if directory else None,
                      "game_required_for_startup": False, "asset_count": len(asset_manifest()["symbols"]),
                      "windows_created": False, "game_files_loaded": False}
            report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            return 0
        except Exception:
            import traceback
            report.write_text(json.dumps({"version": VERSION, "passed": False,
                                          "startup_error": traceback.format_exc()},
                                         ensure_ascii=False, indent=2), encoding="utf-8")
            return 1
    setup_logging()
    app = Trainer()
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
