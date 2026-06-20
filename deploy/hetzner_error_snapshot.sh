#!/usr/bin/env bash
set -euo pipefail

echo "== Container =="
docker compose ps

echo
echo "== Streamlit stdout =="
docker compose logs --tail=120 lumitrack

echo
echo "== LumiTrack file log =="
docker compose exec -T lumitrack sh -lc 'tail -n 160 /var/data/logs/escape_room_monitor.log 2>/dev/null || true'

echo
echo "== Crawl status =="
docker compose exec -T lumitrack sh -lc 'cat /var/data/jobs/crawl_status.json 2>/dev/null || echo "no crawl_status.json"'

echo
echo "== Job files =="
docker compose exec -T lumitrack sh -lc 'ls -lt /var/data/jobs 2>/dev/null | head -n 20 || true'
