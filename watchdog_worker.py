"""RQ Worker 保活看门狗。每 30 秒检查 customer_eval Worker 是否存活，死了就自动重启。"""

import os
import subprocess
import sys
import time

REDIS_URL = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0")
QUEUE = "customer_eval:default"
RQ_EXE = os.path.join(os.path.dirname(sys.executable), "Scripts", "rq.exe")
CHECK_INTERVAL = 30


def worker_alive():
    """Check if at least one worker is registered and alive for the target queue."""
    try:
        from redis import Redis
        r = Redis.from_url(REDIS_URL)
        workers_key = f"rq:workers:{QUEUE}"
        workers = r.smembers(workers_key)
        for wk in workers:
            data = r.hgetall(wk)
            if data and data.get(b"state") == b"idle":
                return True
        return False
    except Exception:
        return False


def start_worker():
    """Start an RQ SimpleWorker for the target queue."""
    env = os.environ.copy()
    # Make sure the project root is in PYTHONPATH
    project_root = os.path.dirname(os.path.abspath(__file__))
    current_path = env.get("PYTHONPATH", "")
    if str(project_root) not in current_path:
        env["PYTHONPATH"] = project_root
    try:
        proc = subprocess.Popen(
            [RQ_EXE, "worker", "-u", REDIS_URL, QUEUE, "--worker-class", "rq.SimpleWorker"],
            cwd=project_root,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        print(f"[{time.strftime('%H:%M:%S')}] Worker restarted (PID {proc.pid})")
        return proc
    except Exception as e:
        print(f"[{time.strftime('%H:%M:%S')}] Failed to start worker: {e}")
        return None


if __name__ == "__main__":
    print(f"[{time.strftime('%H:%M:%S')}] Watchdog started, checking every {CHECK_INTERVAL}s")
    worker_proc = None

    # Start initial worker
    if not worker_alive():
        print(f"[{time.strftime('%H:%M:%S')}] No worker found, starting...")
        worker_proc = start_worker()
    else:
        print(f"[{time.strftime('%H:%M:%S')}] Worker already alive")

    while True:
        time.sleep(CHECK_INTERVAL)
        if not worker_alive():
            print(f"[{time.strftime('%H:%M:%S')}] Worker DEAD, restarting...")
            # Clean up old zombie registrations
            try:
                from redis import Redis
                r = Redis.from_url(REDIS_URL)
                for wk in list(r.smembers(f"rq:workers:{QUEUE}")):
                    if not r.hgetall(wk):
                        r.srem(f"rq:workers:{QUEUE}", wk)
                        r.delete(wk)
            except Exception:
                pass
            worker_proc = start_worker()
