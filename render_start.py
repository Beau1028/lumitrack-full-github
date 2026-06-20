from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent
APP_HOME = Path(os.getenv("ESCAPE_ROOM_MONITOR_HOME", "/var/data"))


def seed_database() -> None:
    """Copy the bundled SQLite DB to persistent storage on first Render boot."""
    target_dir = APP_HOME / "data"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "escape_room.db"
    source = PROJECT_DIR / "data" / "escape_room.db"
    if target.exists() or not source.exists():
        return
    shutil.copy2(source, target)


def main() -> int:
    seed_database()
    port = os.getenv("PORT", "8501")
    runtime = os.getenv("LUMITRACK_RUNTIME", "web").strip().lower()
    if runtime == "web":
        print(f"Starting LumiTrack web app on port {port}", flush=True)
        command = [
            sys.executable,
            "-m",
            "uvicorn",
            "web_app:app",
            "--host",
            "0.0.0.0",
            "--port",
            port,
            "--proxy-headers",
        ]
        return subprocess.call(command)

    entrypoint = os.getenv("LUMITRACK_STREAMLIT_ENTRYPOINT", "streamlit_app.py")
    print(f"Starting LumiTrack with {entrypoint} on port {port}", flush=True)
    command = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        entrypoint,
        "--server.address",
        "0.0.0.0",
        "--server.port",
        port,
        "--server.headless",
        "true",
        "--browser.gatherUsageStats",
        "false",
    ]
    return subprocess.call(command)


if __name__ == "__main__":
    raise SystemExit(main())
