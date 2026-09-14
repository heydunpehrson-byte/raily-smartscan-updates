"""Managed services stop cooperatively; never kill unrelated processes."""
import sys
import threading
import time
from .runtime import SingleInstance, STATE, configure_logging, mark_running, legacy_workstation


def main(mode):
    lock = SingleInstance(mode)
    if not lock.acquired:
        lock.close(); return
    try:
        if mode == 'workstation' and legacy_workstation():
            return
        configure_logging(mode)
        mark_running(mode)
        if mode == 'brain':
            import uvicorn
            server = uvicorn.Server(uvicorn.Config('raily.brain.app:app', host='127.0.0.1', port=8765, log_level='info', timeout_graceful_shutdown=40))
            def watch():
                while not server.should_exit:
                    if (STATE/'brain.stop').exists():
                        server.should_exit = True; return
                    time.sleep(.2)
            threading.Thread(target=watch, daemon=True).start()
            server.run()
        elif mode == 'workstation':
            from raily.desktop.ui import enable_dpi
            enable_dpi()
            from raily.client.workstation_gui import RailyWorkstation
            app = RailyWorkstation()
            def watch():
                if (STATE/'workstation.stop').exists():
                    app.logout(); app.destroy(); return
                app.after(300, watch)
            def ready():
                (STATE/'workstation.ready').write_text('ready', encoding='utf-8')
            app.after_idle(ready); app.after(300, watch)
            app.mainloop()
    finally:
        lock.close()

if __name__ == '__main__': main(sys.argv[1])
