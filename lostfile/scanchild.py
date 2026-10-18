"""扫描子进程：把目录扫描隔离出主 GUI 进程。

背景：Windows 上对部分目录（云同步挂载、杀毒过滤驱动、特殊重解析点等）
执行文件系统枚举时，可能触发进程级原生崩溃（access violation）。这种崩溃
无法用 try/except 捕获。把扫描放入独立子进程后，崩溃只会结束子进程，
主程序不受影响，并能读取子进程留下的“最后目录”友好提示用户。

通信方式（全部为数据目录下的小文件，主程序轮询）：
- job 文件：<workdir>/scan_job.json   —— 任务描述（入口参数）
- 状态文件：<workdir>/scan_state.json —— 子进程高频覆写进度/当前目录
- 控制文件：<workdir>/scan_control.json —— 主程序写 cancel/pause 标志
- 结果文件：<workdir>/scan_result.json —— 子进程结束时写 done/stopped/error
"""
import json
import os
import sys
import threading
import time

from .db import Database
from .indexer import display_path, safe_text, scan_directory


def _write_json(path: str, obj: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        # ensure_ascii=True：孤立代理字符会转义为 \udXXX，避免编码崩溃
        json.dump(obj, f, ensure_ascii=True)
    os.replace(tmp, path)


def _read_json(path: str, default: dict) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


def run_child(job_path: str) -> int:
    try:
        with open(job_path, "r", encoding="utf-8") as f:
            job = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        sys.stderr.write(f"[scanchild] 无法读取任务文件：{e}\n")
        return 2

    workdir = job.get("workdir") or os.path.dirname(job_path)
    state_path = os.path.join(workdir, "scan_state.json")
    control_path = os.path.join(workdir, "scan_control.json")
    result_path = os.path.join(workdir, "scan_result.json")

    db = None
    try:
        db = Database(job["db_path"])
    except Exception as e:
        _write_json(result_path, {"state": "error",
                                  "message": f"无法打开索引数据库：{e}"})
        return 1

    cancel_event = threading.Event()
    pause_event = threading.Event()
    stop = threading.Event()

    def watchdog():
        while not stop.is_set():
            ctrl = _read_json(control_path, {})
            if ctrl.get("cancel"):
                cancel_event.set()
            if ctrl.get("pause"):
                pause_event.set()
            else:
                pause_event.clear()
            stop.wait(0.3)

    threading.Thread(target=watchdog, daemon=True).start()

    state = {
        "pid": os.getpid(), "state": "starting",
        "root": "", "dirs": 0, "files": 0, "errors": 0, "current_dir": "",
    }
    _write_json(state_path, state)
    last_write = 0.0

    def note_progress(name: str, dirs: int, files: int, errors: int):
        nonlocal last_write
        now = time.time()
        if now - last_write < 0.4:
            return
        last_write = now
        state.update(state="scanning", root=name, dirs=dirs,
                     files=files, errors=errors)
        _write_json(state_path, state)

    def note_dir(d: str):
        nonlocal last_write
        now = time.time()
        if now - last_write < 0.2:
            return
        last_write = now
        state["current_dir"] = safe_text(display_path(d))
        _write_json(state_path, state)

    results: list[dict] = []
    cancelled = False
    try:
        for root in job.get("roots", []):
            if cancel_event.is_set():
                cancelled = True
                break
            name = root.get("display_name") or root["path"]
            state.update(state="scanning", root=name,
                         current_dir=root["path"], dirs=0, files=0, errors=0)
            _write_json(state_path, state)
            res = scan_directory(
                root["path"], db,
                exclude_dirs=set(job.get("exclude_dirs") or ()),
                exclude_exts=set(job.get("exclude_exts") or ()),
                on_progress=lambda d, f, e, n=name: note_progress(n, d, f, e),
                on_dir=note_dir,
                cancel_event=cancel_event,
                pause_event=pause_event,
            )
            results.append({"id": root.get("id"), "path": root["path"],
                            "completed": res.completed, "errors": res.errors})
            if cancel_event.is_set():
                cancelled = True
        final_state = "stopped" if cancelled else "done"
        state["state"] = final_state
        _write_json(state_path, state)
        _write_json(result_path, {"state": final_state, "results": results,
                                  "message": ""})
        return 0
    except Exception as e:  # Python 层异常兜底；原生崩溃不经过这里
        _write_json(result_path, {"state": "error", "results": results,
                                  "message": str(e)})
        state["state"] = "error"
        _write_json(state_path, state)
        return 1
    finally:
        stop.set()
        try:
            db.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(run_child(sys.argv[1]))