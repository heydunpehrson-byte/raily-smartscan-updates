import logging
import queue
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from PIL import Image, ImageTk, ImageOps
from .runtime import ASSETS, SingleInstance, configure_logging, ensure_brain, ensure_workstation
from .ui import enable_dpi, center, apply_icon


def main():
    enable_dpi()
    lock = SingleInstance('launcher')
    if not lock.acquired:
        lock.close(); return
    configure_logging('launcher')
    window = tk.Tk(); window.title('RAILY • Dispatch startup'); window.configure(bg='#101820')
    apply_icon(window)
    try:
        with Image.open(ASSETS/'splash.png') as artwork:
            photo = ImageTk.PhotoImage(ImageOps.fit(artwork.convert('RGB'), (720, 360)))
        tk.Label(window, image=photo, borderwidth=0).pack()
    except Exception:
        logging.getLogger(__name__).info('Splash artwork unavailable; using text fallback')
        tk.Label(window, text='RAILY', font=('Segoe UI', 40, 'bold'), bg='#101820', fg='#ef9a3e', padx=100, pady=50).pack()
    tk.Label(window, text='LOCAL DISPATCH CENTER', bg='#101820', fg='#d8e3e9', font=('Segoe UI', 14)).pack(pady=12)
    status = ttk.Label(window, text='Starting Dispatch Brain', padding=16); status.pack(fill='x')
    progress = ttk.Progressbar(window, mode='indeterminate'); progress.pack(fill='x', padx=16, pady=12); progress.start()
    center(window)
    events = queue.Queue()
    def start():
        try:
            ensure_brain(lambda text: events.put(('status', text)))
            events.put(('status', 'Connecting Dispatch Tower'))
            ensure_workstation(lambda text: events.put(('status', text)))
            events.put(('ready', 'RAILY Ready'))
        except Exception as exc:
            logging.exception('Startup failed')
            events.put(('error', str(exc)))
    def poll():
        try:
            while True:
                kind, text = events.get_nowait()
                status.configure(text=text)
                if kind == 'ready':
                    progress.stop(); window.after(700, window.destroy); return
                if kind == 'error':
                    progress.stop(); messagebox.showerror('RAILY needs attention', text, parent=window); window.destroy(); return
        except queue.Empty: pass
        window.after(75, poll)
    threading.Thread(target=start, daemon=True).start()
    window.after(75, poll)
    try: window.mainloop()
    finally: lock.close()

if __name__ == '__main__': main()
