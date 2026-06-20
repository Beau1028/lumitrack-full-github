#!/usr/bin/env bash
set -euo pipefail

git pull --ff-only
docker compose build --no-cache
docker compose up -d --force-recreate
docker compose ps
