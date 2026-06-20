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
PY

echo
echo "== Recent app logs =="
docker compose logs --tail=180 lumitrack
