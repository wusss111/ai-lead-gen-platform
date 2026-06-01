"""Customer Platform Launcher — tkinter GUI managing Redis + Workers + Web.

In frozen mode (PyInstaller), Python services run in-process via threads
because sys.executable is the bundled exe, not a Python interpreter.
"""

from __future__ import annotations

import atexit
import logging
import os
import signal
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

# ═══════════════════════════════════════════════════════════════════
# Path resolution (frozen-aware)
# ═══════════════════════════════════════════════════════════════════

FROZEN = getattr(sys, "frozen", False)

if FROZEN:
    APP_DIR = Path(sys.executable).resolve().parent
    MEIPASS = Path(sys._MEIPASS)
    REDIS_EXE = APP_DIR / "redis" / "redis-server.exe"
    DATA_DIR = Path(os.environ.get("PLATFORM_DATA_DIR", APP_DIR / "data"))
else:
    APP_DIR = Path(__file__).resolve().parents[1]
    MEIPASS = APP_DIR
    REDIS_EXE = APP_DIR / "var" / "redis" / "redis-server.exe"
    DATA_DIR = Path(os.environ.get("PLATFORM_DATA_DIR", APP_DIR / "var" / "platform"))

DATA_DIR.mkdir(parents=True, exist_ok=True)
REDIS_URL = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0").strip()
WEB_PORT = os.environ.get("WEB_PORT", "8000").strip()

if str(MEIPASS) not in sys.path:
    sys.path.insert(0, str(MEIPASS))

# ═══════════════════════════════════════════════════════════════════
# Process / thread management
# ═══════════════════════════════════════════════════════════════════

_redis_proc: subprocess.Popen | None = None
_worker_threads: list[threading.Thread] = []
_web_thread: threading.Thread | None = None
_shutting_down = False
_services_ok = 0  # bitmask: 1=redis, 2=workers, 4=web


def _kill_all():
    global _shutting_down
    _shutting_down = True
    # Stop Redis subprocess
    if _redis_proc is not None:
        try:
            _redis_proc.terminate()
            _redis_proc.wait(timeout=2)
        except Exception:
            try:
                _redis_proc.kill()
            except Exception:
                pass


atexit.register(_kill_all)


# ═══════════════════════════════════════════════════════════════════
# Service starters
# ═══════════════════════════════════════════════════════════════════


def _setup_env() -> dict:
    """Set up environment variables for platform services."""
    env = os.environ.copy()
    if not os.environ.get("PLATFORM_DATA_DIR"):
        env["PLATFORM_DATA_DIR"] = str(DATA_DIR)
    if not os.environ.get("REDIS_URL"):
        env["REDIS_URL"] = REDIS_URL
    env["PYTHONPATH"] = str(MEIPASS)
    return env


def start_redis(log_cb) -> bool:
    """Start Redis server as a subprocess (separate exe, always needed)."""
    global _redis_proc
    log_cb("Starting Redis server...")
    redis_dir = REDIS_EXE.parent
    if not REDIS_EXE.is_file():
        log_cb(f"ERROR: Redis not found at {REDIS_EXE}")
        return False

    conf = redis_dir / "redis.runtime.conf"
    conf.write_text(
        "bind 127.0.0.1\r\nport 6379\r\nloglevel warning\r\n"
        'save ""\r\nappendonly no\r\ndir ./\r\ndbfilename dump.rdb\r\n'
    )
    try:
        _redis_proc = subprocess.Popen(
            [str(REDIS_EXE), str(conf)],
            cwd=str(DATA_DIR),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as e:
        log_cb(f"ERROR: Redis failed to start: {e}")
        return False

    time.sleep(1.5)
    if _redis_proc.poll() is not None:
        log_cb(f"ERROR: Redis exited immediately (code {_redis_proc.returncode})")
        return False
    log_cb("Redis OK (127.0.0.1:6379)")
    return True


def _run_worker_thread(queue_name: str, log_cb):
    """Run an RQ SimpleWorker in this thread (blocking)."""
    try:
        from redis import Redis
        from rq import Queue, SimpleWorker

        conn = Redis.from_url(REDIS_URL)
        q = Queue(queue_name, connection=conn)
        w = SimpleWorker([q], connection=conn)
        log_cb(f"Worker '{queue_name}' started")
        w.work()  # blocking
    except Exception as e:
        if not _shutting_down:
            log_cb(f"Worker '{queue_name}' ERROR: {e}")


def start_workers(log_cb) -> bool:
    """Start RQ workers in background threads."""
    global _worker_threads
    queue_names = ["customer_eval:default", "inquiry_mail:default", "inquiry_mail:send"]
    ok_count = 0
    for q in queue_names:
        if _shutting_down:
            return False
        t = threading.Thread(
            target=_run_worker_thread, args=(q, log_cb),
            daemon=True, name=f"rq-{q.replace(':','-')}"
        )
        t.start()
        _worker_threads.append(t)
        time.sleep(0.5)
        if t.is_alive():
            ok_count += 1
            log_cb(f"Worker '{q}' OK")
        else:
            log_cb(f"WARNING: Worker '{q}' thread died")

    return ok_count > 0


def start_web(log_cb) -> bool:
    """Start uvicorn web server in a background thread."""
    global _web_thread
    log_cb(f"Starting web server (port {WEB_PORT})...")

    def _run_web():
        try:
            import uvicorn
            uvicorn.run(
                "src.core.app:app",
                host="127.0.0.1",
                port=int(WEB_PORT),
                log_level="warning",
            )
        except Exception as e:
            if not _shutting_down:
                log_cb(f"Web server ERROR: {e}")

    _web_thread = threading.Thread(
        target=_run_web, daemon=True, name="uvicorn"
    )
    _web_thread.start()
    time.sleep(3.0)
    if _web_thread.is_alive():
        log_cb(f"Web server OK -> http://127.0.0.1:{WEB_PORT}")
        return True
    log_cb(f"ERROR: Web server failed to start on port {WEB_PORT}")
    return False


# ═══════════════════════════════════════════════════════════════════
# GUI
# ═══════════════════════════════════════════════════════════════════

class LauncherGUI:
    def __init__(self):
        from tkinter import Tk, Frame, Label, Button, Text, Scrollbar, END, WORD, NORMAL, DISABLED

        self.Tk = Tk
        self.Frame = Frame
        self.Label = Label
        self.Button = Button
        self.Text = Text
        self.Scrollbar = Scrollbar
        self.END = END
        self.WORD = WORD
        self.NORMAL = NORMAL
        self.DISABLED = DISABLED

        self._build_ui()

    def _build_ui(self):
        self.root = self.Tk()
        ver = "2.1.0"
        self.root.title(f"Customer Platform - Launcher v{ver}")
        self.root.geometry("550x440")
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self.Label(
            self.root,
            text=f"Customer Platform v{ver}",
            font=("Microsoft YaHei", 14, "bold"),
        ).pack(pady=(15, 2))

        self.Label(
            self.root,
            text="Service Console",
            font=("Consolas", 9),
            fg="#888",
        ).pack(pady=(0, 5))

        # Log output area
        frm = self.Frame(self.root)
        frm.pack(fill="both", expand=True, padx=15, pady=(5, 8))
        self.log = self.Text(
            frm, height=16, width=62, wrap=self.WORD,
            bg="#1e1e1e", fg="#d4d4d4", insertbackground="white",
            font=("Consolas", 9), state=self.DISABLED,
        )
        scroll = self.Scrollbar(frm, command=self.log.yview)
        self.log.configure(yscrollcommand=scroll.set)
        self.log.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        # Buttons
        btn_frame = self.Frame(self.root)
        btn_frame.pack(pady=(0, 12))
        self.start_btn = self.Button(
            btn_frame, text="Start All", width=16,
            command=self._start_all, bg="#007acc", fg="white",
            font=("Microsoft YaHei", 10),
        )
        self.start_btn.pack(side="left", padx=5)
        self.stop_btn = self.Button(
            btn_frame, text="Stop All", width=12,
            command=self._stop_all, state=self.DISABLED,
            font=("Microsoft YaHei", 10),
        )
        self.stop_btn.pack(side="left", padx=5)
        self.open_btn = self.Button(
            btn_frame, text="Open Browser", width=14,
            command=lambda: webbrowser.open(f"http://127.0.0.1:{WEB_PORT}"),
            state=self.DISABLED, font=("Microsoft YaHei", 10),
        )
        self.open_btn.pack(side="left", padx=5)

        # Auto-start after a short delay
        self.root.after(500, self._start_all)

    def _log(self, msg: str):
        try:
            self.log.configure(state=self.NORMAL)
            self.log.insert(self.END, msg + "\n")
            self.log.see(self.END)
            self.log.configure(state=self.DISABLED)
        except Exception:
            pass

    def _start_all(self):
        self.start_btn.configure(state=self.DISABLED)
        threading.Thread(target=self._start_services, daemon=True).start()

    def _start_services(self):
        global _shutting_down
        _shutting_down = False

        self._log("=" * 45)
        self._log(f"Root: {MEIPASS}")
        self._log(f"Data: {DATA_DIR}")
        self._log(f"Frozen: {FROZEN}")
        self._log("=" * 45)

        # Set up env
        _setup_env()

        # 1. Redis
        if not start_redis(self._log):
            self._log("\nRedis failed. Check if port 6379 is in use.")
            self.root.after(0, lambda: self.start_btn.configure(state=self.NORMAL))
            return

        if _shutting_down:
            return

        # 2. RQ Workers (threads)
        if not start_workers(self._log):
            self._log("\nWorkers failed to start.")
            self.root.after(0, lambda: self.start_btn.configure(state=self.NORMAL))
            return

        if _shutting_down:
            return

        # 3. Web server (thread)
        if not start_web(self._log):
            self._log(f"\nWeb server failed. Check if port {WEB_PORT} is in use.")
            self.root.after(0, lambda: self.start_btn.configure(state=self.NORMAL))
            return

        self._log("\nAll services started! Opening browser...")
        webbrowser.open(f"http://127.0.0.1:{WEB_PORT}")
        self.root.after(0, self._on_ready)

    def _on_ready(self):
        self.open_btn.configure(state=self.NORMAL)
        self.stop_btn.configure(state=self.NORMAL)

    def _stop_all(self):
        self._log("\nStopping all services...")
        _kill_all()
        self._log("All services stopped.")
        self.stop_btn.configure(state=self.DISABLED)
        self.open_btn.configure(state=self.DISABLED)
        self.start_btn.configure(state=self.NORMAL)

    def _on_close(self):
        _kill_all()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    LauncherGUI().run()
