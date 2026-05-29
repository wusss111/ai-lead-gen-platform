"""Customer Platform Launcher — tkinter GUI managing Redis + Workers + Web."""

from __future__ import annotations

import atexit
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

if getattr(sys, "frozen", False):
    APP_DIR = Path(sys.executable).resolve().parent
    MEIPASS = Path(sys._MEIPASS)
    REDIS_EXE = APP_DIR / "redis" / "redis-server.exe"
    DATA_DIR = Path(os.environ.get("PLATFORM_DATA_DIR", APP_DIR / "data"))
else:
    APP_DIR = Path(__file__).resolve().parents[1]   # packaging/ → repo root
    MEIPASS = APP_DIR
    REDIS_EXE = APP_DIR / "var" / "redis" / "redis-server.exe"
    DATA_DIR = Path(os.environ.get("PLATFORM_DATA_DIR", APP_DIR / "var" / "platform"))

DATA_DIR.mkdir(parents=True, exist_ok=True)
REDIS_URL = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0").strip()
WEB_PORT = os.environ.get("WEB_PORT", "8000").strip()

# Ensure MEIPASS is on sys.path so importlib-based agent discovery works
if str(MEIPASS) not in sys.path:
    sys.path.insert(0, str(MEIPASS))

# ═══════════════════════════════════════════════════════════════════
# Process management
# ═══════════════════════════════════════════════════════════════════

_processes: list[subprocess.Popen] = []
_shutting_down = False


def _kill_all():
    global _shutting_down
    _shutting_down = True
    for p in reversed(_processes):
        try:
            p.terminate()
        except Exception:
            pass
    time.sleep(0.3)
    for p in reversed(_processes):
        try:
            p.kill()
        except Exception:
            pass


atexit.register(_kill_all)

# ═══════════════════════════════════════════════════════════════════
# Service starters
# ═══════════════════════════════════════════════════════════════════


def start_redis(log_cb):
    """Start Redis server as a subprocess."""
    log_cb("Starting Redis server...")
    redis_dir = REDIS_EXE.parent
    if not REDIS_EXE.is_file():
        log_cb(f"ERROR: Redis not found at {REDIS_EXE}")
        return False

    conf = redis_dir / "redis.runtime.conf"
    conf.write_text(
        "bind 127.0.0.1\r\nport 6379\r\nloglevel warning\r\n"
        "save \"\"\r\nappendonly no\r\ndir ./\r\ndbfilename dump.rdb\r\n"
    )
    try:
        proc = subprocess.Popen(
            [str(REDIS_EXE), str(conf)],
            cwd=str(DATA_DIR),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as e:
        log_cb(f"ERROR: Redis failed to start: {e}")
        return False

    _processes.append(proc)
    time.sleep(1.5)
    if proc.poll() is not None:
        log_cb(f"ERROR: Redis exited immediately (code {proc.returncode})")
        return False
    log_cb("Redis OK (127.0.0.1:6379)")
    return True


def start_worker(queue_name, log_cb):
    """Start one RQ worker (SimpleWorker required on Windows)."""
    log_cb(f"Starting RQ Worker: {queue_name} ...")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(MEIPASS)
    try:
        proc = subprocess.Popen(
            [
                sys.executable, "-m", "rq", "worker",
                "-u", REDIS_URL, queue_name,
                "--worker-class", "rq.SimpleWorker",
            ],
            cwd=str(DATA_DIR),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as e:
        log_cb(f"ERROR: Worker {queue_name} failed to start: {e}")
        return False

    _processes.append(proc)
    time.sleep(0.8)
    if proc.poll() is not None:
        log_cb(f"WARNING: {queue_name} worker exited (code {proc.returncode})")
        return False
    log_cb(f"Worker '{queue_name}' OK")
    return True


def start_web(log_cb):
    """Start uvicorn web server."""
    log_cb(f"Starting web server (port {WEB_PORT})...")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(MEIPASS)
    if not os.environ.get("PLATFORM_DATA_DIR"):
        env["PLATFORM_DATA_DIR"] = str(DATA_DIR)
    try:
        proc = subprocess.Popen(
            [
                sys.executable, "-m", "uvicorn", "src.core.app:app",
                "--host", "127.0.0.1", "--port", WEB_PORT,
                "--log-level", "warning",
            ],
            cwd=str(MEIPASS),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as e:
        log_cb(f"ERROR: Web server failed to start: {e}")
        return False

    _processes.append(proc)
    time.sleep(3.0)
    if proc.poll() is not None:
        log_cb(f"ERROR: Web server exited (code {proc.returncode})")
        return False
    log_cb(f"Web server OK → http://127.0.0.1:{WEB_PORT}")
    return True


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
        self.root.title("外贸客户平台 - 启动器")
        self.root.geometry("550x440")
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        # Icon / branding
        try:
            self.root.iconbitmap(default=str(APP_DIR / "packaging" / "icon.ico"))
        except Exception:
            pass

        self.Label(
            self.root,
            text="外贸客户平台 v2.1.0",
            font=("Microsoft YaHei", 14, "bold"),
        ).pack(pady=(15, 2))

        self.Label(
            self.root,
            text="服务状态控制台",
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
            btn_frame, text="启动全部服务", width=16,
            command=self._start_all, bg="#007acc", fg="white",
            font=("Microsoft YaHei", 10),
        )
        self.start_btn.pack(side="left", padx=5)
        self.stop_btn = self.Button(
            btn_frame, text="停止全部", width=12,
            command=self._stop_all, state=self.DISABLED,
            font=("Microsoft YaHei", 10),
        )
        self.stop_btn.pack(side="left", padx=5)
        self.open_btn = self.Button(
            btn_frame, text="打开浏览器", width=14,
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
        self._log("=" * 45)
        self._log(f"Root: {MEIPASS}")
        self._log(f"Data: {DATA_DIR}")
        self._log("=" * 45)

        # 1. Redis
        if not start_redis(self._log):
            self._log("\nRedis 启动失败，请检查端口 6379 是否被占用。")
            self.root.after(0, lambda: self.start_btn.configure(state=self.NORMAL))
            return

        # 2. RQ Workers
        for q in ("customer_eval:default", "inquiry_mail:default", "inquiry_mail:send"):
            if _shutting_down:
                return
            start_worker(q, self._log)

        # 3. Web server
        if _shutting_down:
            return
        if not start_web(self._log):
            self._log("\nWeb 服务启动失败，请检查端口 8000 是否被占用。")
            self.root.after(0, lambda: self.start_btn.configure(state=self.NORMAL))
            return

        self._log("\n✓ 所有服务已启动！浏览器将自动打开 ...")
        webbrowser.open(f"http://127.0.0.1:{WEB_PORT}")
        self.root.after(0, self._on_ready)

    def _on_ready(self):
        self.open_btn.configure(state=self.NORMAL)
        self.stop_btn.configure(state=self.NORMAL)

    def _stop_all(self):
        self._log("\n正在停止所有服务 ...")
        _kill_all()
        self._log("✓ 所有服务已停止。")
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
