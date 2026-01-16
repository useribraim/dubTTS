## Observability (Prometheus + Grafana)

This setup scrapes:
- API metrics: `http://host.docker.internal:8000/metrics`
- Worker metrics: `http://host.docker.internal:9108/metrics`

### Start services
1) Make sure the API and worker are running locally.
2) Start Prometheus + Grafana:

```bash
cd /Users/ibraimabduramanov/Documents/informatikBU/dub_mvp
docker compose -f docker-compose.observability.yml up -d
```

### Open Grafana
- URL: http://localhost:3000
- Login: `admin` / `admin`
- Dashboard: **Dub MVP Overview**

### Stop services
```bash
docker compose -f docker-compose.observability.yml down
```

Notes:
- `host.docker.internal` works on macOS/Windows Docker Desktop.
- If you run on Linux, replace with `localhost` or the Docker host IP.
