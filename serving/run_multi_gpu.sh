#!/bin/bash
# L40S 4장에 ComfyUI 인스턴스를 하나씩 띄운다 (명세서 6-2).
#
# 각 인스턴스는 CUDA_VISIBLE_DEVICES 로 GPU 를 하나만 보게 하고,
# 포트를 8188~8191 로 분리한다. 앞단의 FastAPI(8000)가 라운드로빈 분배한다.
#
# 명세서 다이어그램에는 9190/9191 로 적혀 있으나 스크립트는 8190/8191 이다.
# 8188~8191 로 통일한다(연속 포트가 방화벽/관리에 편하다).
set -u
ROOT="${ROOT:-$HOME/portrait-restyle}"
VENV="$ROOT/.venv/bin/activate"
LOGDIR="${LOGDIR:-$ROOT/logs}"
mkdir -p "$LOGDIR"

# shellcheck disable=SC1090
source "$VENV"
cd "$ROOT/ComfyUI" || exit 1

# 우리에게 할당된 GPU 는 device 1 하나다. 다른 장치는 관리자 허락을 받았을 때만
#   GPUS="0 1 2 3" bash serving/run_multi_gpu.sh
# 로 띄우고, 작업이 끝나면 반드시 내린다 (bash serving/stop_extra_gpus.sh).
GPUS="${GPUS:-1}"
echo "Starting ComfyUI on GPU(s): $GPUS ..."
for i in $GPUS; do
  port=$((8188 + i))
  pidfile="$LOGDIR/comfy_gpu$i.pid"
  if [ -f "$pidfile" ] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then
    echo "  GPU $i (port $port): already running (pid $(cat "$pidfile"))"
    continue
  fi
  CUDA_VISIBLE_DEVICES=$i setsid nohup python3 main.py --port "$port" --listen 127.0.0.1 \
    > "$LOGDIR/comfy_gpu$i.log" 2>&1 &
  echo $! > "$pidfile"
  echo "  GPU $i (port $port): started pid $!"
done

echo "Waiting for readiness ..."
for i in $GPUS; do
  port=$((8188 + i))
  for _ in $(seq 1 90); do
    if curl -s -o /dev/null --max-time 2 "http://127.0.0.1:$port/system_stats"; then
      echo "  port $port ready"; break
    fi
    sleep 2
  done
done
echo "All instances up."
