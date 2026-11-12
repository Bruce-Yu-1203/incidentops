#!/usr/bin/env bash
# One-command local demo for IncidentOps.
#
#   scripts/demo.sh up       start everything (idempotent)
#   scripts/demo.sh status   show what is running
#   scripts/demo.sh down     stop processes and containers (keeps data volumes)
#
# Each control-plane process is started through its make target, so it gets
# only its own credentials and database login. MODEL=... selects the
# investigation model; without INCIDENTOPS_MODEL_ID the no-key rules baseline
# is used.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUN_DIR="$ROOT/.run"
LOG_DIR="$RUN_DIR/logs"
PROCESSES=(api control executor verifier worker chat web)

cd "$ROOT"

target_for() {
  case "$1" in
    api) echo "api-run" ;;
    control) echo "control-run" ;;
    executor) echo "executor-run" ;;
    verifier) echo "verifier-run" ;;
    worker) echo "worker-run" ;;
    chat) echo "chat-run" ;;
    *) return 1 ;;
  esac
}

is_running() {
  local pid_file="$RUN_DIR/$1.pid"
  [[ -f "$pid_file" ]] && kill -0 "$(cat "$pid_file")" 2>/dev/null
}

start_process() {
  local name="$1"
  if is_running "$name"; then
    echo "  $name already running"
    return
  fi
  if [[ "$name" == "web" ]] && curl -fsS -m 1 http://127.0.0.1:3001 >/dev/null 2>&1; then
    echo "  web already served on :3001 (started elsewhere)"
    return
  fi
  if [[ "$name" == "web" ]]; then
    (cd apps/web && INCIDENTOPS_API_URL=http://127.0.0.1:8000 nohup pnpm dev \
      >"$LOG_DIR/web.log" 2>&1 & echo $! >"$RUN_DIR/web.pid")
  else
    nohup make "$(target_for "$name")" >"$LOG_DIR/$name.log" 2>&1 &
    echo $! >"$RUN_DIR/$name.pid"
  fi
  echo "  started $name (log: .run/logs/$name.log)"
}

wait_for() {
  local url="$1" seconds="$2"
  for _ in $(seq 1 "$seconds"); do
    curl -fsS -m 1 "$url" >/dev/null 2>&1 && return 0
    sleep 1
  done
  echo "timed out waiting for $url" >&2
  return 1
}

up() {
  mkdir -p "$LOG_DIR"
  [[ -f .env ]] || make env
  echo "==> infrastructure (log: .run/logs/infrastructure.log)"
  make db-up lab-up infra-up >"$LOG_DIR/infrastructure.log" 2>&1
  make seed-users >>"$LOG_DIR/infrastructure.log" 2>&1
  if [[ "${SKIP_WEB:-0}" != "1" && ! -d apps/web/node_modules ]]; then
    make web-install >>"$LOG_DIR/infrastructure.log" 2>&1
  fi
  echo "==> processes"
  start_process api
  wait_for http://127.0.0.1:8000/healthz 90
  for name in control executor verifier worker chat; do
    start_process "$name"
  done
  # SKIP_WEB=1 (e.g. in CI) runs the backend only.
  if [[ "${SKIP_WEB:-0}" != "1" ]]; then
    start_process web
    wait_for http://127.0.0.1:3001 120 || true
  fi
  cat <<'MSG'

IncidentOps is up.
  UI           http://127.0.0.1:3001   (sign in as approver or viewer; passwords in .env)
  API          http://127.0.0.1:8000/docs
  Grafana      http://127.0.0.1:3000
  Prometheus   http://127.0.0.1:9090

Inject a fault:   set -a && . ./.env && set +a && uv run python -m fault_lab.scripts.labctl pause
Reset the lab:    make lab-reset
Stop everything:  make demo-down
MSG
}

status() {
  for name in "${PROCESSES[@]}"; do
    if is_running "$name"; then
      echo "  $name: running (pid $(cat "$RUN_DIR/$name.pid"))"
    else
      echo "  $name: stopped"
    fi
  done
  docker ps --format '  {{.Names}}: {{.Status}}' | grep incidentops || true
}

stop_tree() {
  # Stop a process and its children (make -> uv -> python).
  local pid="$1" child
  for child in $(pgrep -P "$pid" 2>/dev/null || true); do
    stop_tree "$child"
  done
  kill "$pid" 2>/dev/null || true
}

down() {
  mkdir -p "$RUN_DIR"
  for name in "${PROCESSES[@]}"; do
    if is_running "$name"; then
      stop_tree "$(cat "$RUN_DIR/$name.pid")"
      echo "  stopped $name"
    fi
    : >"$RUN_DIR/$name.pid"
  done
  docker compose -f infra/compose.yaml --env-file .env stop >/dev/null 2>&1 || true
  docker compose -f fault-lab/compose.yaml --env-file .env stop >/dev/null 2>&1 || true
  echo "  containers stopped (data volumes kept; make lab-down infra-down deletes them)"
}

case "${1:-help}" in
  up) up ;;
  down) down ;;
  status) status ;;
  *) sed -n '2,11p' "$0" ;;
esac
