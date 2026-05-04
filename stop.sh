#!/usr/bin/env bash
set -Eeuo pipefail

# PDT2.1 本地停止脚本：只停止本项目的后端 FastAPI 和前端 Vite。
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_DIR="$ROOT_DIR/.run"

BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-${FRONT_PORT:-5173}}"

BACKEND_PID="$RUN_DIR/backend.pid"
FRONTEND_PID="$RUN_DIR/frontend.pid"

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

stop_pid_file() {
  local name="$1"
  local pid_file="$2"
  local pid

  pid="$(pid_from_file "$pid_file")"
  if is_running "$pid"; then
    echo "停止${name}，PID=$pid"
    kill "$pid" >/dev/null 2>&1 || true
    for _ in {1..15}; do
      if ! is_running "$pid"; then
        break
      fi
      sleep 1
    done
    if is_running "$pid"; then
      echo "$name 未正常退出，强制停止 PID=$pid"
      kill -9 "$pid" >/dev/null 2>&1 || true
    fi
  else
    echo "$name PID 文件不存在或进程未运行"
  fi

  rm -f "$pid_file"
}

stop_owned_port_processes() {
  local name="$1"
  local port="$2"
  local pids pid cmd

  pids="$(listener_pids "$port")"
  if [[ -z "$pids" ]]; then
    return 0
  fi

  while IFS= read -r pid; do
    [[ -z "$pid" ]] && continue
    cmd="$(ps -p "$pid" -o command= 2>/dev/null || true)"
    if [[ "$cmd" == *"$ROOT_DIR"* || "$cmd" == *"uvicorn app.main:app"* || "$cmd" == *"vite"* ]]; then
      echo "按端口停止${name}，PID=$pid"
      kill "$pid" >/dev/null 2>&1 || true
    else
      echo "$name 端口 $port 仍被其他进程占用，未停止：PID=$pid $cmd"
    fi
  done <<< "$pids"
}

# 兼容之前手动用 screen 启动的同名会话，只关闭本项目约定的会话名。
if command -v screen >/dev/null 2>&1; then
  screen -S pdt-backend -X quit >/dev/null 2>&1 || true
  screen -S pdt-front -X quit >/dev/null 2>&1 || true
fi

stop_pid_file "前端" "$FRONTEND_PID"
stop_pid_file "后端" "$BACKEND_PID"

stop_owned_port_processes "前端" "$FRONTEND_PORT"
stop_owned_port_processes "后端" "$BACKEND_PORT"

sleep 1

if [[ -z "$(listener_pids "$FRONTEND_PORT")" ]]; then
  echo "前端已停止。"
fi

if [[ -z "$(listener_pids "$BACKEND_PORT")" ]]; then
  echo "后端已停止。"
fi
