#!/bin/sh
set -eu

if [ -n "${CLOUDFLARED_TUNNEL_TOKEN:-}" ]; then
    cloudflared tunnel --no-autoupdate run --token "${CLOUDFLARED_TUNNEL_TOKEN}" &
fi

exec uvicorn main:app --host "${UVICORN_HOST:-0.0.0.0}" --port "${UVICORN_PORT:-8124}"
