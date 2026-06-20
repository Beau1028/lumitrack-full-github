#!/usr/bin/env bash
set -euo pipefail

echo "== LumiTrack container =="
docker compose ps

echo
echo "== Health check =="
docker compose exec -T lumitrack python - <<'PY'
from pathlib import Path
import json
import os

home = Path(os.environ.get("ESCAPE_ROOM_MONITOR_HOME", "/var/data"))
db = home / "data" / "escape_room.db"
status = home / "jobs" / "crawl_status.json"
print(f"home={home}")
print(f"db_exists={db.exists()} db_size={db.stat().st_size if db.exists() else 0}")
if status.exists():
    try:
        payload = json.loads(status.read_text(encoding="utf-8"))
        print("job_status=" + str(payload.get("status", "")))
        print("job_label=" + str(payload.get("label", "")))
        print("job_updated_at=" + str(payload.get("updated_at", "")))
    except Exception as exc:
        print(f"job_status_read_error={type(exc).__name__}: {exc}")
else:
    print("job_status=none")

print()
print("== Code markers ==")
for marker in [
    "LUMITRACK_STREAMLIT_ENTRYPOINT",
    "server_app.py",
    "escapeRoomShowLoading = () => {}",
    "멈춘 수집 상태 정리",
    "start_crawl_job(",
]:
    found = False
    for path in [
        Path("/app/docker-compose.yml"),
        Path("/app/render_start.py"),
        Path("/app/server_app.py"),
        Path("/app/app.py"),
        Path("/app/scraper/crawl_jobs.py"),
    ]:
        if path.exists() and marker in path.read_text(encoding="utf-8", errors="replace"):
            found = True
    print(f"{marker}: {'yes' if found else 'no'}")

print()
print("== Runtime env ==")
for key in [
    "LUMITRACK_STREAMLIT_ENTRYPOINT",
    "LUMITRACK_MAX_PARALLEL_ORIGINS",
    "LUMITRACK_NAVIGATION_TIMEOUT_MS",
]:
    print(f"{key}={os.environ.get(key, '')}")
PY

echo
echo "== Recent app logs =="
docker compose logs --tail=180 lumitrack

echo
echo "== Recent LumiTrack file logs =="
docker compose exec -T lumitrack sh -lc 'tail -n 180 /var/data/logs/escape_room_monitor.log 2>/dev/null || true'

echo
echo "== Crawl job files =="
docker compose exec -T lumitrack sh -lc 'ls -la /var/data/jobs 2>/dev/null || true'
