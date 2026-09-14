"""Shared display sizing, icon, and railroad identity."""
import ctypes
import os
import tkinter as tk
from tkinter import ttk
from .runtime import ASSETS


def enable_dpi():
    if os.name == 'nt':
        try:
            ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        except (AttributeError, OSError):
            try: ctypes.windll.shcore.SetProcessDpiAwareness(1)
            except (AttributeError, OSError): pass


def work_area(window):
    if os.name == 'nt':
        from ctypes import wintypes
        class MonitorInfo(ctypes.Structure):
            _fields_ = [('cbSize', wintypes.DWORD), ('rcMonitor', wintypes.RECT), ('rcWork', wintypes.RECT), ('dwFlags', wintypes.DWORD)]
        try:
            user = ctypes.windll.user32
            user.MonitorFromWindow.restype = ctypes.c_void_p
            user.MonitorFromWindow.argtypes = [ctypes.c_void_p, wintypes.DWORD]
            user.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(MonitorInfo)]
            handle = user.MonitorFromWindow(window.winfo_id(), 2)
            info = MonitorInfo(); info.cbSize = ctypes.sizeof(info)
            if user.GetMonitorInfoW(handle, ctypes.byref(info)):
                rect = info.rcWork
                return rect.left, rect.top, rect.right-rect.left, rect.bottom-rect.top
        except (AttributeError, OSError): pass
    return 0, 0, window.winfo_screenwidth(), window.winfo_screenheight()


def center(window, width=None, height=None):
    window.update_idletasks()
    left, top, sw, sh = work_area(window)
    width = min(width or window.winfo_reqwidth()+20, sw-32)
    height = min(height or window.winfo_reqheight()+30, sh-64)
    window.minsize(min(360, width), min(260, height))
    window.geometry(f'{width}x{height}+{left+(sw-width)//2}+{top+(sh-height)//2}')


def apply_icon(window, diagnostics=False):
    try: window.iconbitmap(str(ASSETS/('raily-diagnostics.ico' if diagnostics else 'raily.ico')))
    except tk.TclError: pass


class ScrollFrame(ttk.Frame):
    def __init__(self, parent, **kwargs):
        super().__init__(parent, **kwargs)
        self.canvas = tk.Canvas(self, highlightthickness=0, background='#101820')
        bar = ttk.Scrollbar(self, command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=bar.set)
        bar.pack(side='right', fill='y'); self.canvas.pack(fill='both', expand=True)
        self.body = ttk.Frame(self.canvas)
        self.item = self.canvas.create_window(0, 0, window=self.body, anchor='nw')
        self.body.bind('<Configure>', lambda event: self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Configure>', lambda event: self.canvas.itemconfigure(self.item, width=event.width))
        self.body.bind('<FocusIn>', self.reveal)

    def reveal(self, event):
        self.update_idletasks()
        widget = event.widget
        y = widget.winfo_rooty()-self.body.winfo_rooty()
        total = max(1, self.body.winfo_height())
        visible = self.canvas.winfo_height()
        top = self.canvas.canvasy(0)
        if y < top: self.canvas.yview_moveto(y/total)
        elif y+widget.winfo_height() > top+visible:
            self.canvas.yview_moveto((y+widget.winfo_height()-visible)/total)


def avatar(parent):
    holder = ttk.Frame(parent)
    holder.pack(fill='x', padx=8, pady=6)
    badge = tk.Canvas(holder, width=44, height=44, highlightthickness=0, bg='#101820')
    badge.pack(side='left', padx=(0, 10))
    badge.create_polygon(22, 2, 41, 12, 38, 34, 22, 43, 6, 34, 3, 12, fill='#d58128', outline='')
    badge.create_text(22, 21, text='R', font=('Segoe UI', 20, 'bold'), fill='#101820')
    ttk.Label(holder, text='RAILY • Your dispatch partner\nHe keeps the paperwork on track.', justify='left').pack(side='left')
