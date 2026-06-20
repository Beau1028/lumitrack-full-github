from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class CrawlJobAlreadyRunning(RuntimeError):
    """Raised when a crawl job is already active."""


FINAL_STATUSES = {"success", "partial_success", "failed", "stopped"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def job_dir(app_home: str | Path) -> Path:
    path = Path(app_home) / "jobs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def job_status_path(app_home: str | Path) -> Path:
    return job_dir(app_home) / "crawl_status.json"


def read_job_file(path: str | Path) -> dict[str, Any] | None:
    status_path = Path(path)
    if not status_path.exists():
        return None
    try:
        return json.loads(status_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def write_job_file(path: str | Path, payload: dict[str, Any]) -> None:
    status_path = Path(path)
    status_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = status_path.with_suffix(".tmp")
    temporary_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary_path.replace(status_path)


def update_job_file(path: str | Path, **updates: Any) -> dict[str, Any]:
    current = read_job_file(path) or {}
    current.update(updates)
    current["updated_at"] = utc_now()
    write_job_file(path, current)
    return current


def read_job_status(app_home: str | Path) -> dict[str, Any] | None:
    return read_job_file(job_status_path(app_home))


def clear_job_status(app_home: str | Path) -> None:
    status_path = job_status_path(app_home)
    try:
        status_path.unlink()
    except FileNotFoundError:
        return


def process_is_running(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    if os.name == "nt":
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                check=False,
                capture_output=True,
                text=True,
            )
        except OSError:
            return False
        return str(pid) in result.stdout
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def job_is_running(status: dict[str, Any] | None) -> bool:
    if not status or status.get("status") != "running":
        return False
    return process_is_running(int(status.get("pid") or 0))


def tail_job_log(status: dict[str, Any] | None, max_lines: int = 80) -> str:
    if not status:
        return ""
    log_path = Path(str(status.get("log_path", "")))
    if not log_path.exists():
        return ""
    try:
        with log_path.open("rb") as file:
            file.seek(0, os.SEEK_END)
            file_size = file.tell()
            file.seek(max(0, file_size - 64_000))
            chunk = file.read().decode("utf-8", errors="replace")
        lines = chunk.splitlines()
    except OSError:
        return ""
    return "\n".join(lines[-max_lines:])


def start_crawl_job(
    *,
    app_home: str | Path,
    project_dir: str | Path,
    label: str,
    days: int,
    config_path: str | Path,
    db_path: str | Path,
    store_ids: set[str] | None,
    delay_min_seconds: int,
    delay_max_seconds: int,
    max_parallel_origins: int,
    max_navigation_timeout_ms: int,
) -> dict[str, Any]:
    jobs_path = job_dir(app_home)
    status_path = job_status_path(app_home)
    current = read_job_file(status_path)
    if job_is_running(current):
        raise CrawlJobAlreadyRunning("이미 예약 수집 작업이 실행 중입니다.")

    if current and current.get("status") == "running":
        current["status"] = "stopped"
        current["finished_at"] = utc_now()
        current["error"] = "이전 수집 프로세스가 예기치 않게 종료되었습니다."
        write_job_file(status_path, current)

    job_id = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S") + "-" + uuid.uuid4().hex[:8]
    log_path = jobs_path / f"crawl_{job_id}.log"
    selected_store_ids = sorted(store_ids or [])
    payload: dict[str, Any] = {
        "job_id": job_id,
        "label": label,
        "status": "starting",
        "started_at": utc_now(),
        "updated_at": utc_now(),
        "finished_at": "",
        "pid": 0,
        "days": days,
        "store_ids": selected_store_ids,
        "log_path": str(log_path),
        "progress": {
            "phase": "starting",
            "completed": 0,
            "total": 1,
            "stores_completed": 0,
            "stores_total": 0,
            "current_store": "수집 프로세스 시작 중",
            "current_date": "",
            "success": 0,
            "failed": 0,
            "slots": 0,
        },
        "summary": {},
        "error": "",
    }
    write_job_file(status_path, payload)

    command = [
        sys.executable,
        "-m",
        "scraper.crawl_job_worker",
        "--job-file",
        str(status_path),
        "--label",
        label,
        "--config",
        str(config_path),
        "--db",
        str(db_path),
        "--days",
        str(days),
        "--delay-min",
        str(delay_min_seconds),
        "--delay-max",
        str(delay_max_seconds),
        "--parallel-origins",
        str(max_parallel_origins),
        "--max-navigation-timeout-ms",
        str(max_navigation_timeout_ms),
    ]
    for store_id in selected_store_ids:
        command.extend(["--store-id", store_id])

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    creationflags = 0
    start_new_session = os.name != "nt"
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

    try:
        with log_path.open("a", encoding="utf-8") as log_file:
            process = subprocess.Popen(
                command,
                cwd=str(project_dir),
                stdout=log_file,
                stderr=subprocess.STDOUT,
                env=env,
                start_new_session=start_new_session,
                creationflags=creationflags,
            )
    except Exception as exc:
        payload["status"] = "failed"
        payload["finished_at"] = utc_now()
        payload["error"] = f"{type(exc).__name__}: {exc}"
        write_job_file(status_path, payload)
        raise

    payload["status"] = "running"
    payload["pid"] = process.pid
    payload["command"] = command
    payload["updated_at"] = utc_now()
    write_job_file(status_path, payload)
    return payload
