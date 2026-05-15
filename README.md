# maintenance-log

Containerized equipment maintenance logging system built with FastAPI, SQLite, SQLAlchemy, Twilio, Jinja2, and Docker.

## Environment variables

The application supports the following environment variables.

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `MAINTENANCE_DB_PATH` | No | `maintenance.db` locally / `/app/data/maintenance.db` in Docker | SQLite database file path. |
| `UVICORN_HOST` | No | `0.0.0.0` | Host interface for the FastAPI server inside the container. |
| `UVICORN_PORT` | No | `8000` | Port used by Uvicorn inside the container. |
| `CLOUDFLARED_TUNNEL_TOKEN` | No | _unset_ | When set, the container starts `cloudflared tunnel run` alongside the FastAPI app so Twilio can reach the webhook through a Cloudflare Tunnel. |

## Local run

```bash
python -m pip install -r requirements.txt
uvicorn main:app --reload
```

## Docker run

```bash
docker build -t maintenance-log .
docker run --rm -p 8000:8000 \
  -e MAINTENANCE_DB_PATH=/app/data/maintenance.db \
  -v "$(pwd)/data:/app/data" \
  maintenance-log
```

## Docker run with cloudflared

```bash
docker run --rm -p 8000:8000 \
  -e MAINTENANCE_DB_PATH=/app/data/maintenance.db \
  -e CLOUDFLARED_TUNNEL_TOKEN=your-cloudflare-tunnel-token \
  -v "$(pwd)/data:/app/data" \
  maintenance-log
```

When `CLOUDFLARED_TUNNEL_TOKEN` is present, the container launches `cloudflared` in the background and then starts the FastAPI app.

## Twilio webhook

Point the Twilio webhook to your public `/sms` endpoint, for example:

```text
https://your-public-host.example.com/sms
```

Supported SMS commands:

- `LOG [Machine Name] - [Task]`
- `GET [Machine Name]`
