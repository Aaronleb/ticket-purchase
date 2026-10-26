#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""界面组件库：圆角卡片、药丸按钮、开关、分段控件等（纯 Tk，无第三方依赖）。"""

from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont

# ---------------- 设计令牌 ----------------
BG = "#f4f5f7"
SURFACE = "#ffffff"
SURFACE_2 = "#f7f8fa"
INK = "#0f1115"
INK2 = "#4b5261"
INK3 = "#8b93a1"
LINE = "#e4e7ee"
LINE_SOFT = "#eef0f4"
BRAND = "#c8102e"
BRAND_HOVER = "#a80c26"
BRAND_SOFT = "#fdecef"
BRAND_LINE = "#f4c6ce"
OK = "#0f7b6c"
OK_SOFT = "#e8f5f2"
WARN = "#a15c07"
WARN_SOFT = "#fdf3e4"
DARK = "#0e1117"

FONT = "PingFang SC"
MONO = "Menlo"

ACCOUNT_COLORS = ["#c8102e", "#1d4ed8", "#0f7b6c", "#a15c07", "#6d28d9", "#be185d"]


def font(size: int, bold: bool = False) -> tuple:
    return (FONT, size, "bold") if bold else (FONT, size)


def mono(size: int = 11) -> tuple:
    return (MONO, size)


def rounded(canvas: tk.Canvas, x1, y1, x2, y2, r, **kwargs):
    """在 Canvas 上画圆角矩形（smooth 多边形近似）。"""
    r = max(0, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    points = [
        x1 + r, y1, x2 - r, y1, x2, y1,
        x2, y1 + r, x2, y2 - r, x2, y2,
        x2 - r, y2, x1 + r, y2, x1, y2,
        x1, y2 - r, x1, y1 + r, x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, splinesteps=24, **kwargs)


class Card(tk.Frame):
    """圆角白卡片：内部 body 用于放内容。"""

    def __init__(self, parent, padding=18, radius=14, fill=SURFACE, border=LINE, shadow=True, **kwargs):
        outer = parent.cget("bg")
        super().__init__(parent, bg=outer, highlightthickness=0, bd=0, **kwargs)
        self.padding = padding
        self.radius = radius
        self.fill = fill
        self.border = border
        self.shadow = shadow
        self.canvas = tk.Canvas(self, bg=outer, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self.body = tk.Frame(self.canvas, bg=fill)
        self._win = self.canvas.create_window(padding, padding, window=self.body, anchor="nw")
        self.canvas.bind("<Configure>", self._redraw)

    def _redraw(self, event=None) -> None:
        width = self.canvas.winfo_width()
        height = self.canvas.winfo_height()
        self.canvas.delete("shape")
        bottom = height - 0.5
        if self.shadow and width > 8 and height > 8:
            rounded(
                self.canvas,
                1.5,
                3.5,
                width - 1.5,
                height - 0.5,
                self.radius,
                fill="#e7eaf0",
                outline="",
                tags="shape",
            )
            bottom = height - 3.0
        rounded(
            self.canvas,
            0.5,
            0.5,
            width - 0.5,
            bottom,
            self.radius,
            fill=self.fill,
            outline=self.border,
            width=1,
            tags="shape",
        )
        self.canvas.tag_lower("shape")
        self.canvas.itemconfigure(
            self._win,
            width=max(1, width - self.padding * 2),
            height=max(1, height - self.padding * 2),
        )


class PillButton(tk.Canvas):
    """圆角药丸按钮，支持 primary / ghost / danger / subtle / dark。"""

    KINDS = {
        "primary": dict(fill=BRAND, hover=BRAND_HOVER, text="#ffffff", outline="", disabled_fill="#e8ebf0"),
        "ghost": dict(fill=SURFACE, hover=SURFACE_2, text=INK2, outline=LINE, disabled_fill="#fafbfc"),
        "danger": dict(fill=SURFACE, hover=BRAND_SOFT, text=BRAND, outline=BRAND_LINE, disabled_fill="#fafbfc"),
        "subtle": dict(fill=SURFACE_2, hover="#e9ecf1", text=INK2, outline="", disabled_fill="#f7f8fa"),
        "dark": dict(fill="#1b2130", hover="#242c3d", text="#e6ebf5", outline="", disabled_fill="#eef0f3"),
    }

    def __init__(self, parent, text, command=None, kind="primary", height=38,
                 padding=18, size=12, bold=True, radius=10, width=None):
        bg = parent.cget("bg")
        super().__init__(parent, bg=bg, highlightthickness=0, bd=0, height=height)
        self._cmd = command
        self._kind = kind
        self._state = "normal"
        self._hover = False
        self._pressed = False
        self._text = text
        self._font = tkfont.Font(family=FONT, size=size, weight="bold" if bold else "normal")
        self._height = height
        self._radius = radius
        self._width = width or int(self._font.measure(text) + padding * 2)
        self.configure(width=self._width)
        self.bind("<Configure>", lambda _e: self._draw())
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self._draw()

    def _draw(self) -> None:
        self.delete("all")
        style = self.KINDS[self._kind]
        w = self.winfo_width() or self._width
        h = self.winfo_height() or self._height
        if self._state == "disabled":
            fill = style["disabled_fill"]
            text = "#a3aab6" if self._kind in ("primary", "dark") else "#b3b9c4"
            outline = "" if self._kind in ("primary", "dark") else LINE
        else:
            if self._pressed:
                fill = style["hover"]
            elif self._hover:
                fill = style["hover"]
            else:
                fill = style["fill"]
            text = style["text"]
            outline = style["outline"]
        rounded(self, 0.5, 0.5, w - 0.5, h - 0.5, self._radius, fill=fill, outline=outline or fill, width=1)
        self.create_text(w / 2, h / 2 + 0.5, text=self._text, fill=text, font=self._font)
        self.configure(cursor="pointinghand" if self._state == "normal" else "arrow")

    def _on_enter(self, _event=None):
        if self._state == "normal":
            self._hover = True
            self._draw()

    def _on_leave(self, _event=None):
        self._hover = False
        self._draw()

    def _on_press(self, _event=None):
        if self._state == "normal" and self._cmd:
            self._pressed = True
            self._draw()

    def _on_release(self, event=None):
        if self._state != "normal":
            return
        was_pressed = self._pressed
        self._pressed = False
        self._draw()
        if was_pressed and self._cmd:
            self._cmd()

    def set_enabled(self, enabled: bool) -> None:
        self._state = "normal" if enabled else "disabled"
        self._hover = False
        self._draw()

    def configure(self, cnf=None, **kwargs):
        if "state" in kwargs:
            self.set_enabled(kwargs.pop("state") != "disabled")
        if "text" in kwargs:
            self._text = kwargs.pop("text")
            self._draw()
        if kwargs or cnf:
            return super().configure(cnf, **kwargs)

    config = configure


class IconButton(tk.Canvas):
    """只有图标的方形按钮（用于删除等次要操作）。"""

    def __init__(self, parent, text, command=None, size=30, kind="ghost", tooltip=""):
        bg = parent.cget("bg")
        super().__init__(parent, bg=bg, width=size, height=size, highlightthickness=0, bd=0)
        self._cmd = command
        self._size = size
        self._kind = kind
        self._hover = False
        self._pressed = False
        self._text = text
        self.bind("<Configure>", lambda _e: self._draw())
        self.bind("<Enter>", lambda _e: self._set_hover(True))
        self.bind("<Leave>", lambda _e: self._set_hover(False))
        self.bind("<Button-1>", lambda _e: self._cmd() if self._cmd else None)
        if tooltip:
            self._tip = tooltip
        self._draw()

    def _set_hover(self, value):
        self._hover = value
        self._draw()

    def _draw(self):
        self.delete("all")
        w = self.winfo_width() or self._size
        h = self.winfo_height() or self._size
        fill = SURFACE_2 if self._hover else self.cget("bg")
        if self._kind == "danger":
            fill = BRAND_SOFT if self._hover else self.cget("bg")
        rounded(self, 0.5, 0.5, w - 0.5, h - 0.5, 8, fill=fill, outline=fill)
        color = BRAND if (self._kind == "danger" and self._hover) else INK3
        self.create_text(w / 2, h / 2 + 0.5, text=self._text, fill=color, font=font(12))
        self.configure(cursor="pointinghand")


class Toggle(tk.Canvas):
    """iOS 风格开关。"""

    def __init__(self, parent, value=False, command=None, width=42, height=24):
        bg = parent.cget("bg")
        super().__init__(parent, bg=bg, width=width, height=height, highlightthickness=0, bd=0)
        self._value = bool(value)
        self._cmd = command
        self._track_w = width
        self._track_h = height
        self.bind("<Button-1>", self._toggle)
        self.bind("<Configure>", lambda _e: self._draw())
        self.configure(cursor="pointinghand")
        self._draw()

    def _draw(self):
        self.delete("all")
        w = self.winfo_width() or self._track_w
        h = self.winfo_height() or self._track_h
        track = BRAND if self._value else "#d6dae2"
        rounded(self, 1, 1, w - 1, h - 1, (h - 2) / 2, fill=track, outline=track)
        r = (h - 6) / 2
        cx = (w - r - 4) if self._value else (r + 4)
        self.create_oval(cx - r, h / 2 - r, cx + r, h / 2 + r, fill="#ffffff", outline="")

    def _toggle(self, _event=None):
        self._value = not self._value
        self._draw()
        if self._cmd:
            self._cmd(self._value)

    def set(self, value: bool) -> None:
        self._value = bool(value)
        self._draw()

    def get(self) -> bool:
        return self._value


class Segmented(tk.Canvas):
    """分段控件（替代 ttk.Notebook 的标签）。"""

    def __init__(self, parent, options, command=None, height=36, size=12):
        bg = parent.cget("bg")
        self._font = tkfont.Font(family=FONT, size=size, weight="bold")
        widths = [self._font.measure(o) + 34 for o in options]
        super().__init__(parent, bg=bg, width=sum(widths) + 6, height=height, highlightthickness=0, bd=0)
        self._labels = list(options)
        self._widths = widths
        self._command = command
        self._index = 0
        self._height = height
        self.bind("<Button-1>", self._click)
        self.bind("<Configure>", lambda _e: self._draw())
        self.configure(cursor="pointinghand")
        self._draw()

    def _draw(self):
        self.delete("all")
        h = self.winfo_height() or self._height
        total = sum(self._widths) + 6
        rounded(self, 1, 1, total - 1, h - 1, (h - 2) / 2, fill="#eef0f4", outline="#eef0f4")
        x = 3
        for index, (label, width) in enumerate(zip(self._labels, self._widths)):
            if index == self._index:
                rounded(self, x, 3, x + width, h - 3, (h - 6) / 2, fill=SURFACE, outline=LINE_SOFT)
                color = BRAND
            else:
                color = INK3
            self.create_text(x + width / 2, h / 2 + 0.5, text=label, fill=color, font=self._font)
            x += width

    def _click(self, event):
        x = 3
        for index, width in enumerate(self._widths):
            if x <= event.x <= x + width:
                self.select(index)
                break
            x += width

    def select(self, index: int) -> None:
        self._index = max(0, min(index, len(self._labels) - 1))
        self._draw()
        if self._command:
            self._command(self._index)

    def index(self) -> int:
        return self._index


class Chip(tk.Canvas):
    """圆角标签：选中为品牌红实心，未选中为浅灰。"""

    def __init__(self, parent, text, selected=False, command=None, height=26, size=10):
        bg = parent.cget("bg")
        self._font = tkfont.Font(family=FONT, size=size, weight="bold" if selected else "normal")
        label = ("✓ " if selected else "") + text
        width = int(self._font.measure(label) + 26)
        super().__init__(parent, bg=bg, width=width, height=height, highlightthickness=0, bd=0)
        self._text = label
        self._selected = bool(selected)
        self._cmd = command
        self._hover = False
        self._height = height
        self.bind("<Enter>", lambda _e: self._set_hover(True))
        self.bind("<Leave>", lambda _e: self._set_hover(False))
        self.bind("<Button-1>", lambda _e: self._cmd() if self._cmd else None)
        self.bind("<Configure>", lambda _e: self._draw())
        self.configure(cursor="pointinghand")
        self._draw()

    def _set_hover(self, value):
        self._hover = value
        self._draw()

    def _draw(self):
        self.delete("all")
        w = self.winfo_width() or self.winfo_reqwidth()
        h = self.winfo_height() or self._height
        if self._selected:
            fill = BRAND_HOVER if self._hover else BRAND
            text = "#ffffff"
        else:
            fill = "#e9ecf1" if self._hover else SURFACE_2
            text = INK2
        rounded(self, 0.5, 0.5, w - 0.5, h - 0.5, (h - 1) / 2, fill=fill, outline=fill)
        self.create_text(w / 2, h / 2 + 0.5, text=self._text, fill=text, font=self._font)


class Avatar(tk.Canvas):
    """圆形头像，显示账号名首字。"""

    def __init__(self, parent, text, size=34, color=BRAND):
        bg = parent.cget("bg")
        super().__init__(parent, bg=bg, width=size, height=size, highlightthickness=0, bd=0)
        self._text = text
        self._size = size
        self._color = color
        self.bind("<Configure>", lambda _e: self._draw())
        self._draw()

    def _draw(self):
        self.delete("all")
        s = self.winfo_width() or self._size
        self.create_oval(0.5, 0.5, s - 0.5, s - 0.5, fill=self._color, outline="")
        self.create_text(s / 2, s / 2 + 0.5, text=self._text[:1].upper(), fill="#ffffff", font=font(int(s / 2.6), True))


class Dot(tk.Canvas):
    def __init__(self, parent, color=OK, size=8):
        bg = parent.cget("bg")
        super().__init__(parent, bg=bg, width=size, height=size, highlightthickness=0, bd=0)
        self._color = color
        self._size = size
        self.create_oval(1, 1, size - 1, size - 1, fill=color, outline=color, tags="dot")

    def set_color(self, color: str) -> None:
        self._color = color
        self.itemconfigure("dot", fill=color, outline=color)


class Badge(tk.Label):
    def __init__(self, parent, text, fg=INK2, bg=SURFACE_2, size=10):
        super().__init__(parent, text=text, fg=fg, bg=bg, font=font(size), padx=8, pady=2)


def divider(parent, pad_bottom=0):
    line = tk.Frame(parent, bg=LINE, height=1)
    line.pack(fill="x", pady=(0, pad_bottom))
    return line
