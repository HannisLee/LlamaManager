#!/usr/bin/env bash
set -e

cd "$(dirname "$0")"

mkdir -p logs

exec uvicorn app:app --host 0.0.0.0 --port 8082
