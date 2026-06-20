#!/usr/bin/env bash
set -euo pipefail

docker compose exec -T lumitrack sh -lc '
  log=/var/data/logs/escape_room_monitor.log
  if [ ! -f "$log" ]; then
    echo "No LumiTrack file log yet."
    exit 0
  fi
  echo "== Last errors only =="
  tail -n 500 "$log" \
    | grep -n -i -E "traceback|exception|error|failed|killed|memory|sqlite|runtimeerror|valueerror|typeerror" \
    | tail -n 120 || true
'

echo
echo "== Streamlit stdout errors =="
docker compose logs --tail=240 lumitrack \
  | grep -i -E "traceback|exception|error|failed|killed|memory|runtimeerror|valueerror|typeerror" \
  | tail -n 120 || true
