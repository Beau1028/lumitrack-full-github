#!/usr/bin/env bash
set -euo pipefail

echo "== Latest render error =="
docker compose exec -T lumitrack sh -lc '
  file=/var/data/logs/render_error_latest.txt
  if [ -f "$file" ]; then
    tail -n 220 "$file"
  else
    echo "No render_error_latest.txt yet."
  fi
'

echo
echo "== Recent stdout traceback =="
docker compose logs --tail=220 lumitrack \
  | grep -i -A 30 -B 5 -E "LumiTrack failed|Traceback|TypeError|ValueError|RuntimeError|AttributeError" \
  || true
