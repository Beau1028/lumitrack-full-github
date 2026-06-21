#!/usr/bin/env bash
set -euo pipefail

docker compose exec -T lumitrack sh -lc '
  mkdir -p /var/data/jobs
  log="/var/data/jobs/manual_7day_$(date +%Y%m%d_%H%M%S).log"
  echo "Starting 7-day crawl. Log: $log"
  nohup python -m scraper.runner \
    --config /app/stores.yaml \
    --db /var/data/data/escape_room.db \
    --days 7 \
    --delay-min 5 \
    --delay-max 6 \
    --minimum-recrawl-minutes 30 \
    --parallel-origins 8 \
    --max-navigation-timeout-ms 10000 \
    > "$log" 2>&1 &
  echo "Started PID $!"
'
