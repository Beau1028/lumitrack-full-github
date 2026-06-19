from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import tkinter as tk
import urllib.request
import webbrowser
from datetime import date, datetime, timedelta
from pathlib import Path
from tkinter import messagebox, ttk
from zoneinfo import ZoneInfo

APP_TITLE = "LumiTrack"
APP_FOLDER_NAME = "EscapeRoomMonitor"


def catalog_version(path: Path) -> str:
    if not path.exists():
        return ""
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[:5]:
        if line.startswith("catalog_version:"):
            return line.partition(":")[2].strip()
    return ""


def resource_path(name: str) -> Path:
    bundle_dir = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return bundle_dir / name


def app_home() -> Path:
    local_app_data = os.getenv("LOCALAPPDATA")
    base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    return base / APP_FOLDER_NAME


def prepare_app_home() -> Path:
    home = app_home()
    (home / "data").mkdir(parents=True, exist_ok=True)
    (home / "logs").mkdir(parents=True, exist_ok=True)

    bundled_stores = resource_path("stores.yaml")
    default_stores = home / "stores.default.yaml"
    shutil.copy2(bundled_stores, default_stores)

    stores = home / "stores.yaml"
    replace_sample_config = False
    if stores.exists():
        current_text = stores.read_text(encoding="utf-8", errors="replace")
        replace_sample_config = (
            "sample_store" in current_text and "play33_konkuk" not in current_text
        )
    bundled_version = catalog_version(bundled_stores)
    installed_version = catalog_version(stores)
    bundled_catalog_update = (
        stores.exists()
        and bundled_version
        and installed_version
        and bundled_version != installed_version
        and "play33_konkuk" in current_text
    )
    if bundled_catalog_update:
        backup = home / f"stores.backup-{datetime.now():%Y%m%d-%H%M%S}.yaml"
        shutil.copy2(stores, backup)
    if not stores.exists() or replace_sample_config or bundled_catalog_update:
        shutil.copy2(bundled_stores, stores)

    bundled_locations = resource_path("store_locations.yaml")
    if bundled_locations.exists():
        default_locations = home / "store_locations.default.yaml"
        shutil.copy2(bundled_locations, default_locations)
        locations = home / "store_locations.yaml"
        bundled_location_version = catalog_version(bundled_locations)
        installed_location_version = catalog_version(locations)
        location_catalog_update = (
            locations.exists()
            and bundled_location_version
            and installed_location_version
            and bundled_location_version != installed_location_version
        )
        if location_catalog_update:
            backup = home / (
                f"store_locations.backup-{datetime.now():%Y%m%d-%H%M%S}.yaml"
            )
            shutil.copy2(locations, backup)
        if not locations.exists() or (
            bundled_location_version != installed_location_version
        ):
            shutil.copy2(bundled_locations, locations)

    sample_html = home / "sample_booking.html"
    if not sample_html.exists():
        shutil.copy2(resource_path("sample_booking.html"), sample_html)

    bundled_manual = resource_path("manual_estimates.yaml")
    manual_default = home / "manual_estimates.default.yaml"
    shutil.copy2(bundled_manual, manual_default)
    manual = home / "manual_estimates.yaml"
    bundled_manual_version = catalog_version(bundled_manual)
    installed_manual_version = catalog_version(manual)
    if (
        manual.exists()
        and bundled_manual_version
        and installed_manual_version
        and bundled_manual_version != installed_manual_version
    ):
        backup = home / (
            f"manual_estimates.backup-{datetime.now():%Y%m%d-%H%M%S}.yaml"
        )
        shutil.copy2(manual, backup)
    if not manual.exists() or bundled_manual_version != installed_manual_version:
        shutil.copy2(bundled_manual, manual)

    database = home / "data" / "escape_room.db"
    seed_database = resource_path("assets/sample_escape_room.db")
    if not database.exists() and seed_database.exists():
        shutil.copy2(seed_database, database)

    os.environ["ESCAPE_ROOM_MONITOR_HOME"] = str(home)
    os.environ["ESCAPE_ROOM_MONITOR_USE_EDGE"] = "1"
    os.environ["STREAMLIT_BROWSER_GATHER_USAGE_STATS"] = "false"
    return home


def find_available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_for_server(url: str, timeout_seconds: int = 40) -> bool:
    deadline = time.monotonic() + timeout_seconds
    health_url = f"{url}/_stcore/health"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(health_url, timeout=1) as response:
                if response.read().decode("utf-8").strip() == "ok":
                    return True
        except OSError:
            time.sleep(0.25)
    return False


def process_command(*arguments: str) -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, *arguments]
    return [sys.executable, str(Path(__file__).resolve()), *arguments]


def hidden_process_kwargs(log_path: Path) -> dict[str, object]:
    log_file = log_path.open("a", encoding="utf-8")
    kwargs: dict[str, object] = {
        "stdout": log_file,
        "stderr": subprocess.STDOUT,
        "env": os.environ.copy(),
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    return kwargs


def progress_writer(path: Path):
    """Write crawl progress atomically for the desktop window."""
    path.parent.mkdir(parents=True, exist_ok=True)

    def write(event: dict[str, object]) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(event, ensure_ascii=False),
            encoding="utf-8",
        )
        temporary.replace(path)

    return write


def terminate_process_tree(process: subprocess.Popen[bytes] | None) -> None:
    if process is None or process.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
            check=False,
        )
        return

    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()


def serve_dashboard(port: int) -> None:
    home = prepare_app_home()
    os.chdir(home)
    from streamlit.web import bootstrap

    streamlit_options = {
        "global_developmentMode": False,
        "server_headless": True,
        "server_address": "127.0.0.1",
        "server_port": port,
        "browser_gatherUsageStats": False,
    }
    bootstrap.load_config_options(streamlit_options)
    bootstrap.run(
        str(resource_path("app.py")),
        False,
        [],
        streamlit_options,
    )


def crawl_now(minimum_recrawl_minutes: int = 0) -> int:
    home = prepare_app_home()
    from scraper.config import load_stores
    from scraper.database import Database
    from scraper.logging_utils import configure_logging
    from scraper.runner import run_crawl

    configure_logging(home / "logs")
    stores = load_stores(home / "stores.yaml")
    summary = run_crawl(
        stores=stores,
        target_dates=[datetime.now(ZoneInfo("Asia/Seoul")).date()],
        database=Database(home / "data" / "escape_room.db"),
        delay_min_seconds=5,
        delay_max_seconds=15,
        minimum_recrawl_minutes=minimum_recrawl_minutes,
        max_parallel_origins=4,
        progress_callback=progress_writer(
            home / "logs" / "today_progress.json"
        ),
    )
    return 1 if summary["failed"] else 0


def crawl_week_now(minimum_recrawl_minutes: int = 12 * 60) -> int:
    home = prepare_app_home()
    from scraper.config import load_stores
    from scraper.database import Database
    from scraper.logging_utils import configure_logging
    from scraper.runner import run_crawl

    configure_logging(home / "logs")
    stores = load_stores(home / "stores.yaml")
    today = datetime.now(ZoneInfo("Asia/Seoul")).date()
    summary = run_crawl(
        stores=stores,
        target_dates=[today + timedelta(days=offset) for offset in range(7)],
        database=Database(home / "data" / "escape_room.db"),
        delay_min_seconds=5,
        delay_max_seconds=8,
        minimum_recrawl_minutes=minimum_recrawl_minutes,
        max_parallel_origins=8,
        progress_callback=progress_writer(
            home / "logs" / "weekly_progress.json"
        ),
    )
    return 1 if summary["failed"] else 0


def run_scheduler() -> None:
    home = prepare_app_home()
    from apscheduler.schedulers.blocking import BlockingScheduler

    scheduler = BlockingScheduler(timezone="Asia/Seoul")
    scheduler.add_job(
        crawl_now,
        trigger="interval",
        hours=2,
        kwargs={"minimum_recrawl_minutes": 90},
        id="desktop-public-booking-crawl",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,
        next_run_time=datetime.now(ZoneInfo("Asia/Seoul")) + timedelta(hours=2),
    )
    scheduler.add_job(
        crawl_week_now,
        trigger="cron",
        hour=3,
        minute=30,
        id="desktop-seven-day-booking-crawl",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=1800,
    )
    scheduler.start()


class DesktopWindow:
    def __init__(self) -> None:
        self.home = prepare_app_home()
        self.port = find_available_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self.server_process: subprocess.Popen[bytes] | None = None
        self.scheduler_process: subprocess.Popen[bytes] | None = None
        self.crawl_process: subprocess.Popen[bytes] | None = None

        self.root = tk.Tk()
        self.root.title(APP_TITLE)
        self.root.geometry("500x445")
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        frame = tk.Frame(self.root, padx=28, pady=24)
        frame.pack(fill="both", expand=True)

        tk.Label(
            frame,
            text=APP_TITLE,
            font=("Malgun Gothic", 18, "bold"),
        ).pack(anchor="w")
        tk.Label(
            frame,
            text="방탈출 매출 인사이트",
            font=("Malgun Gothic", 10),
            fg="#555555",
        ).pack(anchor="w", pady=(4, 18))

        self.status_var = tk.StringVar(value="대시보드를 시작하는 중입니다...")
        tk.Label(
            frame,
            textvariable=self.status_var,
            font=("Malgun Gothic", 10),
            wraplength=400,
            justify="left",
        ).pack(anchor="w", pady=(0, 10))

        self.progress_var = tk.DoubleVar(value=0)
        self.progress_bar = ttk.Progressbar(
            frame,
            variable=self.progress_var,
            maximum=100,
            length=420,
        )
        self.progress_bar.pack(anchor="w", pady=(0, 5))
        self.progress_detail_var = tk.StringVar(
            value="수집 대기 중 · 진행률 0%"
        )
        tk.Label(
            frame,
            textvariable=self.progress_detail_var,
            font=("Malgun Gothic", 9),
            fg="#555555",
            wraplength=420,
            justify="left",
        ).pack(anchor="w", pady=(0, 12))
        self.active_progress_path: Path | None = None

        self.open_button = tk.Button(
            frame,
            text="대시보드 열기",
            font=("Malgun Gothic", 11, "bold"),
            width=18,
            state="disabled",
            command=self.open_dashboard,
        )
        self.open_button.pack(anchor="w", pady=3)

        self.crawl_button = tk.Button(
            frame,
            text="전국 오늘 데이터 수집",
            font=("Malgun Gothic", 10),
            width=18,
            command=self.start_crawl,
        )
        self.crawl_button.pack(anchor="w", pady=3)

        self.week_crawl_button = tk.Button(
            frame,
            text="전국 7일 데이터 수집",
            font=("Malgun Gothic", 10),
            width=18,
            command=self.start_week_crawl,
        )
        self.week_crawl_button.pack(anchor="w", pady=3)

        tk.Button(
            frame,
            text="설정 폴더 열기",
            font=("Malgun Gothic", 10),
            width=18,
            command=self.open_settings,
        ).pack(anchor="w", pady=3)

        tk.Button(
            frame,
            text="프로그램 종료",
            font=("Malgun Gothic", 10),
            width=18,
            command=self.close,
        ).pack(anchor="w", pady=3)

        self.start_background_services()

    def start_background_services(self) -> None:
        server_log = self.home / "logs" / "dashboard.log"
        scheduler_log = self.home / "logs" / "scheduler.log"
        self.server_process = subprocess.Popen(
            process_command("--serve", "--port", str(self.port)),
            **hidden_process_kwargs(server_log),
        )
        self.scheduler_process = subprocess.Popen(
            process_command("--scheduler"),
            **hidden_process_kwargs(scheduler_log),
        )
        threading.Thread(target=self.wait_and_open, daemon=True).start()

    def wait_and_open(self) -> None:
        if wait_for_server(self.url):
            self.root.after(0, self.dashboard_ready)
        else:
            self.root.after(0, self.dashboard_failed)

    def dashboard_ready(self) -> None:
        self.status_var.set(
            "실행 중입니다. 앱을 켜 둔 동안 2시간마다 자동 수집합니다."
        )
        self.open_button.config(state="normal")
        if os.getenv("ESCAPE_ROOM_MONITOR_NO_BROWSER") != "1":
            self.open_dashboard()

    def dashboard_failed(self) -> None:
        self.status_var.set(
            "대시보드를 시작하지 못했습니다. 설정 폴더의 logs를 확인하세요."
        )
        messagebox.showerror(APP_TITLE, "대시보드를 시작하지 못했습니다.")

    def open_dashboard(self) -> None:
        webbrowser.open(self.url)

    def open_settings(self) -> None:
        os.startfile(self.home)

    def start_crawl(self) -> None:
        if self.crawl_process and self.crawl_process.poll() is None:
            messagebox.showinfo(APP_TITLE, "이미 수집 중입니다.")
            return
        self.crawl_button.config(state="disabled")
        self.status_var.set(
            "전국 공개 예약표를 확인하는 중입니다. 몇 분 걸릴 수 있습니다..."
        )
        self.prepare_progress(
            self.home / "logs" / "today_progress.json",
            "오늘 수집",
        )
        self.crawl_process = subprocess.Popen(
            process_command("--crawl"),
            **hidden_process_kwargs(self.home / "logs" / "manual_crawl.log"),
        )
        self.root.after(1000, self.check_crawl)

    def start_week_crawl(self) -> None:
        if self.crawl_process and self.crawl_process.poll() is None:
            messagebox.showinfo(APP_TITLE, "이미 수집 중입니다.")
            return
        self.crawl_button.config(state="disabled")
        self.week_crawl_button.config(state="disabled")
        self.status_var.set(
            "전국 7일 공개 예약표를 병렬 확인하는 중입니다..."
        )
        self.prepare_progress(
            self.home / "logs" / "weekly_progress.json",
            "7일 수집",
        )
        self.crawl_process = subprocess.Popen(
            process_command("--crawl-week"),
            **hidden_process_kwargs(self.home / "logs" / "weekly_crawl.log"),
        )
        self.root.after(1000, self.check_crawl)

    def prepare_progress(self, path: Path, label: str) -> None:
        self.active_progress_path = path
        path.unlink(missing_ok=True)
        self.progress_var.set(0)
        self.progress_detail_var.set(f"{label} 준비 중 · 진행률 0%")

    def update_progress(self) -> None:
        path = self.active_progress_path
        if path is None or not path.exists():
            return
        try:
            event = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        completed = int(event.get("completed", 0) or 0)
        total = int(event.get("total", 0) or 0)
        stores_completed = int(event.get("stores_completed", 0) or 0)
        stores_total = int(event.get("stores_total", 0) or 0)
        percent = completed / total * 100 if total else 0
        self.progress_var.set(min(percent, 100))
        current_store = str(event.get("current_store", "") or "")
        current_date = str(event.get("current_date", "") or "")
        current = (
            f" · 현재 {current_store} {current_date}"
            if current_store else ""
        )
        self.progress_detail_var.set(
            f"진행률 {percent:.1f}% · 매장 {stores_completed}/{stores_total}곳 · "
            f"날짜 작업 {completed}/{total}건{current}"
        )

    def check_crawl(self) -> None:
        if self.crawl_process is None:
            return
        return_code = self.crawl_process.poll()
        if return_code is None:
            self.update_progress()
            self.root.after(500, self.check_crawl)
            return
        self.update_progress()
        self.crawl_button.config(state="normal")
        self.week_crawl_button.config(state="normal")
        if return_code == 0:
            self.progress_var.set(100)
            self.status_var.set(
                "수집이 완료됐습니다. 대시보드를 새로고침해 확인하세요."
            )
        else:
            self.status_var.set("일부 수집에 실패했습니다. logs를 확인하세요.")

    def close(self) -> None:
        for process in (
            self.crawl_process,
            self.scheduler_process,
            self.server_process,
        ):
            terminate_process_tree(process)
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--crawl", action="store_true")
    parser.add_argument("--crawl-week", action="store_true")
    parser.add_argument("--scheduler", action="store_true")
    parser.add_argument("--port", type=int, default=8501)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.serve:
        serve_dashboard(args.port)
        return 0
    if args.crawl:
        return crawl_now()
    if args.crawl_week:
        return crawl_week_now(minimum_recrawl_minutes=0)
    if args.scheduler:
        run_scheduler()
        return 0

    DesktopWindow().run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
