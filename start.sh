#!/usr/bin/env bash
set -Eeuo pipefail

# PDT2.1 本地启动脚本：只启动后端 FastAPI 和前端 Vite，不启动/停止 Redis。
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
FRONT_DIR="$ROOT_DIR/front"
RUN_DIR="$ROOT_DIR/.run"
LOG_DIR="$ROOT_DIR/logs"

HOST="${HOST:-127.0.0.1}"
BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-${FRONT_PORT:-8088}}"

BACKEND_PID="$RUN_DIR/backend.pid"
FRONTEND_PID="$RUN_DIR/frontend.pid"

mkdir -p "$RUN_DIR" "$LOG_DIR"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

is_running() {
  local pid="${1:-}"
  [[ -n "$pid" ]] && kill -0 "$pid" >/dev/null 2>&1
}

pid_from_file() {
  local file="$1"
  if [[ -f "$file" ]]; then
    tr -d '[:space:]' < "$file"
  fi
}

listener_pids() {
  local port="$1"
  lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true
}

shell_quote() {
  printf "%q" "$1"
}

write_listener_pid() {
  local port="$1"
  local pid_file="$2"
  local pid

  pid="$(listener_pids "$port" | head -n 1)"
  if [[ -n "$pid" ]]; then
    echo "$pid" > "$pid_file"
  fi
}

can_start() {
  local name="$1"
  local port="$2"
  local pid_file="$3"
  local existing_pid pids compact

  existing_pid="$(pid_from_file "$pid_file")"
  if is_running "$existing_pid"; then
    echo "$name 已在运行，PID=$existing_pid"
    return 10
  fi

  pids="$(listener_pids "$port" | tr '\n' ' ')"
  compact="${pids//[[:space:]]/}"
  if [[ -n "$compact" ]]; then
    echo "$name 端口 $port 已被占用，监听进程：$pids"
    echo "请先运行 ./stop.sh，或手动释放端口后再启动。"
    return 20
  fi

  return 0
}

wait_url() {
  local name="$1"
  local url="$2"
  local log_file="$3"

  for _ in {1..30}; do
    if curl -fsS "$url" >/dev/null 2>&1; then
      echo "$name 已启动：$url"
      return 0
    fi
    sleep 1
  done

  echo "$name 启动超时，最近日志如下："
  tail -n 40 "$log_file" 2>/dev/null || true
  return 1
}

start_backend() {
  can_start "后端" "$BACKEND_PORT" "$BACKEND_PID" || {
    local status=$?
    [[ "$status" -eq 10 ]] && return 0
    return "$status"
  }

  if [[ ! -x "$BACKEND_DIR/.venv/bin/uvicorn" ]]; then
    echo "缺少 $BACKEND_DIR/.venv/bin/uvicorn"
    echo "请先在 backend 目录安装 Python 虚拟环境依赖。"
    return 1
  fi

  if command -v screen >/dev/null 2>&1; then
    local cmd
    cmd="cd $(shell_quote "$BACKEND_DIR") && exec .venv/bin/uvicorn app.main:app --host $(shell_quote "$HOST") --port $(shell_quote "$BACKEND_PORT") > $(shell_quote "$LOG_DIR/backend.log") 2>&1"
    screen -dmS pdt-backend bash -lc "$cmd"
  else
    (
      cd "$BACKEND_DIR"
      nohup .venv/bin/uvicorn app.main:app --host "$HOST" --port "$BACKEND_PORT" \
        > "$LOG_DIR/backend.log" 2>&1 &
      echo $! > "$BACKEND_PID"
    )
  fi

  wait_url "后端" "http://$HOST:$BACKEND_PORT/api/v1/health" "$LOG_DIR/backend.log"
  write_listener_pid "$BACKEND_PORT" "$BACKEND_PID"
}

start_frontend() {
  can_start "前端" "$FRONTEND_PORT" "$FRONTEND_PID" || {
    local status=$?
    [[ "$status" -eq 10 ]] && return 0
    return "$status"
  }

  if [[ ! -d "$FRONT_DIR/node_modules" ]]; then
    echo "缺少 $FRONT_DIR/node_modules"
    echo "请先在 front 目录运行 npm install。"
    return 1
  fi

  if command -v screen >/dev/null 2>&1; then
    local cmd
    cmd="cd $(shell_quote "$FRONT_DIR") && exec npm run dev -- --host $(shell_quote "$HOST") --port $(shell_quote "$FRONTEND_PORT") > $(shell_quote "$LOG_DIR/frontend.log") 2>&1"
    screen -dmS pdt-front bash -lc "$cmd"
  else
    (
      cd "$FRONT_DIR"
      nohup npm run dev -- --host "$HOST" --port "$FRONTEND_PORT" \
        > "$LOG_DIR/frontend.log" 2>&1 &
      echo $! > "$FRONTEND_PID"
    )
  fi

  wait_url "前端" "http://$HOST:$FRONTEND_PORT/" "$LOG_DIR/frontend.log"
  write_listener_pid "$FRONTEND_PORT" "$FRONTEND_PID"
}

start_backend
start_frontend

echo "启动完成。"
echo "后端日志：$LOG_DIR/backend.log"
echo "前端日志：$LOG_DIR/frontend.log"
