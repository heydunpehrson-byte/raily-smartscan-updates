import logging
import queue
import re
import shutil
import sqlite3
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from .runtime import ROOT, LOGS, health, managed_process, safe_restart, configure_logging, SingleInstance
from .ui import enable_dpi, center, apply_icon
from raily.version import VERSION, CHANNEL


def snapshot():
    brain = health()
    result = {'Brain': 'All Clear' if brain else 'Not connected',
              'Worker': ('Running' if brain.get('worker_alive') else 'Not reported / needs attention') if brain else 'Unavailable',
              'Workstation connection': 'Running locally' if managed_process('workstation') else 'No managed Workstation detected',
              'Version/build': VERSION+' • '+CHANNEL, 'Log location': str(LOGS)}
    try:
        from raily.engine.adapter import configure_tesseract
        command = configure_tesseract()
        if command:
            import subprocess
            check = subprocess.run([command, '--version'], capture_output=True, text=True, timeout=5, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            result['OCR / Tesseract'] = check.stdout.splitlines()[0] if check.returncode == 0 else 'Executable needs attention'
        else: result['OCR / Tesseract'] = 'Executable not found'
    except Exception:
        result['OCR / Tesseract'] = 'Unable to verify — see logs'
    path = ROOT/'Data'/'raily.db'
    if path.exists():
        try:
            with sqlite3.connect(path.as_uri()+'?mode=ro', uri=True, timeout=1) as conn:
                conn.execute('SELECT 1 FROM system_info LIMIT 1').fetchall()
            result['Database'] = 'Readable (read-only check)'
        except sqlite3.Error: result['Database'] = 'Temporarily unavailable or needs attention'
    else: result['Database'] = 'Not initialized'
    try: result['Free storage'] = f'{shutil.disk_usage(ROOT if ROOT.exists() else ROOT.parent).free / 1024**3:.1f} GB'
    except OSError: result['Free storage'] = 'Unavailable'
    recent = []
    paths = sorted(LOGS.glob('*.log'), key=lambda p: p.stat().st_mtime, reverse=True)[:6] if LOGS.exists() else []
    for path in paths:
        try:
            with path.open('rb') as stream:
                stream.seek(max(0, path.stat().st_size-16000))
                lines = stream.read().decode('utf-8', errors='replace').splitlines()
            for line in lines:
                if ('ERROR' in line or 'Exception' in line) and not re.search(r'token|password|authorization|argon2', line, re.I):
                    recent.append(f'{path.name}: {line[:400]}')
        except OSError: pass
    result['Recent errors'] = '\n'.join(recent[-12:]) or 'No recent errors found in available logs'
    return result


def main():
    enable_dpi()
    lock = SingleInstance('diagnostics')
    if not lock.acquired: lock.close(); return
    configure_logging('diagnostics')
    window = tk.Tk(); window.title('RAILY Diagnostics'); apply_icon(window, True)
    ttk.Label(window, text='RAILY Diagnostics • Dispatch Tower check', font=('Segoe UI', 18, 'bold'), padding=16).pack(fill='x')
    text = tk.Text(window, wrap='word', font=('Consolas', 11), height=22, width=85)
    text.pack(fill='both', expand=True, padx=16)
    bar = ttk.Frame(window, padding=16); bar.pack(fill='x')
    status = ttk.Label(window, text='Checking the tower…', padding=8); status.pack(fill='x')
    messages = queue.Queue(); busy = False
    buttons = []
    def run(operation):
        nonlocal busy
        if busy: return
        busy = True
        for button in buttons: button.state(['disabled'])
        status.configure(text='Checking the tower…')
        def work():
            try: messages.put((operation(), None))
            except Exception as exc:
                logging.exception('Diagnostic operation failed'); messages.put((None, str(exc)))
        threading.Thread(target=work, daemon=True).start()
    def restart(mode):
        prompt = 'Finish current reviews before restarting. RAILY will wait for active work; he will not force-stop a busy process. Continue?'
        if messagebox.askyesno('Safe restart', prompt, parent=window):
            def operation(): safe_restart(mode); return snapshot()
            run(operation)
    for label, action in [('Refresh', lambda: run(snapshot)), ('Safe Restart Brain', lambda: restart('brain')), ('Safe Restart Workstation', lambda: restart('workstation'))]:
        button = ttk.Button(bar, text=label, command=action, padding=8); button.pack(side='left', padx=4); buttons.append(button)
    def poll():
        nonlocal busy
        try:
            result, error = messages.get_nowait(); busy = False
            for button in buttons: button.state(['!disabled'])
            if error: messagebox.showerror('RAILY Diagnostics', error, parent=window)
            else:
                text.configure(state='normal'); text.delete('1.0', 'end')
                text.insert('end', '\n\n'.join(f'{key}\n{value}' for key, value in result.items())); text.configure(state='disabled')
            status.configure(text='Check complete. Detailed errors stay in the logs.')
        except queue.Empty: pass
        window.after(100, poll)
    center(window, 950, 780); run(snapshot); window.after(100, poll)
    try: window.mainloop()
    finally: lock.close()

if __name__ == '__main__': main()
