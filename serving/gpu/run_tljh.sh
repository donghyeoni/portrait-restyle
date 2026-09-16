#!/usr/bin/env bash
# 알파 테스트용: 개발 GPU 서버(TLJH, 도커 없음)에서 GPU 모델 서버를 상시로 띄운다.
#   - ComfyUI(8189) 가 죽어 있으면 serving/run_multi_gpu.sh 로 GPU 1 에 다시 올린다
#   - serving.gpu.service(8080, START_COMFY=false) 가 죽어 있으면 다시 올린다
#   - 60초마다 확인. setsid nohup 으로 분리해 Jupyter 커널·클라이언트와 무관하게 돈다
#
#   GPU_SERVICE_TOKEN=... bash serving/gpu/run_tljh.sh start     # 감시 루프 시작 (토큰은 ~/.config/portrait/gpu_token 에서 읽어도 됨)
#   bash serving/gpu/run_tljh.sh status | stop
set -u
ROOT="${ROOT:-$HOME/portrait-restyle}"
LOG="$ROOT/logs"; mkdir -p "$LOG"
PORT="${PORT:-8080}"
TOKEN_FILE="${TOKEN_FILE:-$HOME/.config/portrait/gpu_token}"
PIDFILE="$LOG/gpu_keeper.pid"

comfy_ok()   { curl -sf --max-time 3 http://127.0.0.1:8189/system_stats >/dev/null; }
service_ok() { curl -sf --max-time 3 "http://127.0.0.1:$PORT/health" >/dev/null; }

start_service() {
  local tok="${GPU_SERVICE_TOKEN:-}"
  [ -z "$tok" ] && [ -f "$TOKEN_FILE" ] && tok="$(cat "$TOKEN_FILE")"
  [ -z "$tok" ] && echo "경고: GPU_SERVICE_TOKEN 이 비어 있다 — /generate 인증 없이 뜬다" >&2
  cd "$ROOT" && START_COMFY=false PORT="$PORT" GPU_SERVICE_TOKEN="$tok" CUDA_VISIBLE_DEVICES=1 \
    setsid nohup .venv/bin/python -m serving.gpu.service >> "$LOG/gpu_service.log" 2>&1 &
  echo "$(date '+%F %T') gpu service started pid $!" >> "$LOG/gpu_keeper.log"
}

keeper() {
  while true; do
    if ! comfy_ok; then
      echo "$(date '+%F %T') comfy down -> restart" >> "$LOG/gpu_keeper.log"
      (cd "$ROOT" && GPUS=1 bash serving/run_multi_gpu.sh >> "$LOG/gpu_keeper.log" 2>&1)
    fi
    if ! service_ok; then
      pkill -u "$USER" -f "[s]erving.gpu.service" 2>/dev/null
      start_service
    fi
    sleep 60
  done
}

case "${1:-start}" in
  start)
    if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then echo "keeper already running (pid $(cat "$PIDFILE"))"; exit 0; fi
    setsid nohup bash "$0" _loop >> "$LOG/gpu_keeper.log" 2>&1 &
    echo $! > "$PIDFILE"; echo "keeper started pid $!" ;;
  _loop) keeper ;;
  status)
    echo "keeper: $([ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null && echo up || echo down)"
    echo "comfy 8189: $(comfy_ok && echo up || echo down)"
    echo "service $PORT: $(service_ok && curl -s http://127.0.0.1:$PORT/health || echo down)" ;;
  stop)
    [ -f "$PIDFILE" ] && kill "$(cat "$PIDFILE")" 2>/dev/null; rm -f "$PIDFILE"
    pkill -u "$USER" -f "[s]erving.gpu.service" 2>/dev/null; echo "keeper + service stopped (ComfyUI 는 그대로)" ;;
  *) echo "usage: $0 start|status|stop"; exit 1 ;;
esac
