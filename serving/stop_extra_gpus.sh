#!/usr/bin/env bash
# 임시로 빌린 GPU 0·2·3 의 ComfyUI 를 내린다. 우리 할당분(GPU 1, 8189)은 남긴다.
for i in 0 2 3; do
  pf="$HOME/portrait-restyle/logs/comfy_gpu$i.pid"
  if [ -f "$pf" ] && kill -0 "$(cat "$pf")" 2>/dev/null; then
    kill "$(cat "$pf")" && echo "GPU $i (port $((8188+i))) 종료"; rm -f "$pf"
  else
    pkill -u "$(whoami)" -f "main.py --port $((8188+i))" && echo "GPU $i 종료" || echo "GPU $i 이미 없음"
  fi
done
nvidia-smi --query-gpu=index,memory.used --format=csv,noheader
