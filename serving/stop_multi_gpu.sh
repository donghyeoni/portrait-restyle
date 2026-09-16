#!/bin/bash
# run_multi_gpu.sh 로 띄운 인스턴스를 정리한다.
#   stop_multi_gpu.sh          전부 종료
#   stop_multi_gpu.sh 0 2 3    지정한 GPU 만 종료 (나머지는 그대로 둔다)
set -u
ROOT="${ROOT:-$HOME/portrait-restyle}"
LOGDIR="${LOGDIR:-$ROOT/logs}"
GPUS="${*:-0 1 2 3}"
for i in $GPUS; do
  pidfile="$LOGDIR/comfy_gpu$i.pid"
  [ -f "$pidfile" ] || continue
  pid=$(cat "$pidfile")
  if kill -0 "$pid" 2>/dev/null; then
    kill "$pid" && echo "  GPU $i: stopped pid $pid"
  fi
  rm -f "$pidfile"
done
