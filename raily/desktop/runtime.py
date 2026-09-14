import ctypes
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
import httpx
import psutil

PROJECT = Path(os.environ.get('RAILY_DESKTOP_HOME', Path(__file__).resolve().parents[2]))
ROOT = Path(os.environ.get('RAILY_BRAIN_ROOT', Path.home()/'Documents'/'RAILY-Brain'))
STATE = Path(os.environ.get('RAILY_DESKTOP_STATE', Path(os.environ.get('LOCALAPPDATA', Path.home()))/'RAILY'/'Desktop'))
LOGS = ROOT/'Logs'
URL = 'http://127.0.0.1:8765'
ASSETS = Path(getattr(sys, '_MEIPASS', PROJECT))/'assets'


def configure_logging(name):
    LOGS.mkdir(parents=True, exist_ok=True)
    from logging.handlers import RotatingFileHandler
    handler = RotatingFileHandler(LOGS/f'{name}.log', maxBytes=2_000_000, backupCount=3, encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(name)s: %(message)s'))
    logging.getLogger().addHandler(handler)
    logging.getLogger().setLevel(logging.INFO)


class SingleInstance:
    def __init__(self, name):
        self.handle = None
        self.acquired = True
        if os.name == 'nt':
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.CreateMutexW.restype = ctypes.c_void_p
            kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
            self.handle = kernel.CreateMutexW(None, False, 'Local\\RAILY.Desktop.'+name)
            if not self.handle:
                raise OSError('Cannot create RAILY instance lock')
            self.acquired = ctypes.get_last_error() != 183

    def close(self):
        if self.handle:
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.CloseHandle.argtypes = [ctypes.c_void_p]
            kernel.CloseHandle(self.handle)
            self.handle = None


def health():
    try:
        response = httpx.get(URL+'/health', timeout=1.5, trust_env=False)
        response.raise_for_status()
        data = response.json()
        return data if data.get('service') == 'RAILY Dispatch Brain' else None
    except Exception:
        return None


def mark_running(mode):
    STATE.mkdir(parents=True, exist_ok=True)
    (STATE/f'{mode}.stop').unlink(missing_ok=True)
    process = psutil.Process()
    (STATE/f'{mode}.json').write_text(json.dumps({'pid':process.pid, 'created':process.create_time(), 'exe':str(Path(sys.executable).resolve())}), encoding='utf-8')


def managed_process(mode):
    try:
        record = json.loads((STATE/f'{mode}.json').read_text(encoding='utf-8'))
        process = psutil.Process(record['pid'])
        if abs(process.create_time()-record['created']) > .01:
            return None
        if Path(process.exe()).resolve() != Path(record['exe']).resolve():
            return None
        command = process.cmdline()
        if 'raily.desktop.service' not in command or mode not in command:
            return None
        return process
    except (OSError, ValueError, KeyError, psutil.Error):
        return None


def legacy_workstation():
    for process in psutil.process_iter(['cmdline']):
        try:
            if 'raily.client.workstation_gui' in (process.info['cmdline'] or []):
                return True
        except psutil.Error:
            pass
    return False


def launch(mode):
    LOGS.mkdir(parents=True, exist_ok=True)
    executable = PROJECT/'.venv-brain'/'Scripts'/'pythonw.exe'
    if not executable.exists():
        raise RuntimeError('RAILY’s Python environment is missing. Open Diagnostics for help.')
    env = os.environ.copy()
    env.setdefault('RAILY_TESSERACT_CMD', r'C:\Program Files\Tesseract-OCR\tesseract.exe')
    with (LOGS/f'{mode}-console.log').open('a', encoding='utf-8') as log:
        return subprocess.Popen([str(executable), '-m', 'raily.desktop.service', mode], cwd=PROJECT, env=env, stdout=log, stderr=log, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


def ensure_brain(progress=lambda text: None):
    data = health()
    if data:
        if data.get('worker_alive') is False:
            raise RuntimeError('Dispatch Brain needs attention: its worker is offline. Use RAILY Diagnostics.')
        return data
    progress('Starting Dispatch Brain')
    child = launch('brain')
    for _ in range(80):
        data = health()
        if data:
            return data
        if child.poll() is not None:
            raise RuntimeError('Dispatch Brain could not start. Another program may be using its connection. Open RAILY Diagnostics.')
        time.sleep(.25)
    raise RuntimeError('Dispatch Brain is taking too long to connect. Open RAILY Diagnostics.')


def ensure_workstation(progress=lambda text: None):
    if managed_process('workstation') or legacy_workstation():
        return
    progress('Starting Workstation')
    STATE.mkdir(parents=True, exist_ok=True)
    ready = STATE/'workstation.ready'
    ready.unlink(missing_ok=True)
    child = launch('workstation')
    for _ in range(100):
        if ready.exists() and managed_process('workstation'):
            return
        if child.poll() is not None:
            raise RuntimeError('The Workstation could not open. See RAILY Diagnostics for details.')
        time.sleep(.1)
    raise RuntimeError('The Workstation is taking longer than expected. Open RAILY Diagnostics.')


def safe_restart(mode):
    process = managed_process(mode)
    if process is None:
        if mode == 'brain' and health() or mode == 'workstation' and legacy_workstation():
            raise RuntimeError('This instance was started outside the new launcher. Close it once using its own window, then use RAILY to start it safely.')
    else:
        (STATE/f'{mode}.stop').write_text('stop', encoding='utf-8')
        try:
            process.wait(timeout=40)
        except psutil.TimeoutExpired:
            raise RuntimeError('RAILY is finishing active work. Nothing was force-stopped; check Diagnostics again shortly.')
    if mode == 'brain':
        ensure_brain()
    else:
        ensure_brain(); ensure_workstation()
