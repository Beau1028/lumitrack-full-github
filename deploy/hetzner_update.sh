#!/usr/bin/env bash
set -euo pipefail

git pull --ff-only
docker compose up -d --build --force-recreate
docker compose ps
