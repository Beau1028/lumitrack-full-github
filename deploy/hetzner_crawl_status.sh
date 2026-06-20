#!/usr/bin/env bash
set -euo pipefail

docker compose exec -T lumitrack sh -lc '
  echo "== Running crawl processes =="
  ps aux | grep "scraper.runner" | grep -v grep || true
  echo
  echo "== Latest manual crawl log =="
  latest=$(ls -t /var/data/jobs/manual_7day_*.log 2>/dev/null | head -n 1 || true)
  if [ -z "$latest" ]; then
    echo "No manual 7-day crawl log yet."
  else
    echo "$latest"
    tail -n 120 "$latest"
  fi
'
