#!/usr/bin/env bash
# 알파 테스트용 SSH 역터널: 개발 GPU 서버(인바운드 불가) -> EC2. EC2 의 127.0.0.1:18080 이 이 서버의 8080 으로 이어진다.
#
#   EC2 워커:  GPU_SERVICE_URL=http://127.0.0.1:18080  GPU_AUTH_MODE=bearer  GPU_AUTH_CREDENTIALS_FILE=/secrets/gpu-token
#   EC2 sshd:  터널 전용 사용자(셸 없음). authorized_keys 에
#              restrict,port-forwarding,permitlisten="127.0.0.1:18080" ssh-ed25519 AAAA...   (GatewayPorts 기본값 no)
#
#   TUNNEL_HOST=ec2-host TUNNEL_USER=aitunnel TUNNEL_KEY=~/.ssh/aitunnel bash serving/gpu/tunnel.sh start|status|stop
# autossh 가 없어도 되도록 bash 루프로 재접속한다. 22 아웃바운드는 이 서버에서 허용돼 있다.
set -u
LOG="${LOG:-$HOME/portrait-restyle/logs}"; mkdir -p "$LOG"
PIDFILE="$LOG/tunnel.pid"
: "${TUNNEL_HOST:?TUNNEL_HOST 필요}"; : "${TUNNEL_USER:=aitunnel}"; : "${TUNNEL_KEY:=$HOME/.ssh/aitunnel}"
REMOTE_PORT="${REMOTE_PORT:-18080}"; LOCAL_PORT="${LOCAL_PORT:-8080}"

loop() {
  while true; do
    echo "$(date '+%F %T') connect" >> "$LOG/tunnel.log"
    ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
        -o StrictHostKeyChecking=accept-new -i "$TUNNEL_KEY" \
        -R "127.0.0.1:${REMOTE_PORT}:127.0.0.1:${LOCAL_PORT}" "${TUNNEL_USER}@${TUNNEL_HOST}" >> "$LOG/tunnel.log" 2>&1
    echo "$(date '+%F %T') disconnected rc=$? — 10초 후 재접속" >> "$LOG/tunnel.log"
    sleep 10
  done
}

case "${1:-start}" in
  start)
    if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then echo "tunnel already running"; exit 0; fi
    setsid nohup bash "$0" _loop >> "$LOG/tunnel.log" 2>&1 &
    echo $! > "$PIDFILE"; echo "tunnel loop started pid $!" ;;
  _loop) loop ;;
  status) [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null && echo "tunnel loop up; $(pgrep -u "$USER" -af "[s]sh -N" | wc -l) ssh 세션" || echo "tunnel down" ;;
  stop) [ -f "$PIDFILE" ] && kill "$(cat "$PIDFILE")" 2>/dev/null; rm -f "$PIDFILE"; pkill -u "$USER" -f "[s]sh -N -o ExitOnForwardFailure" 2>/dev/null; echo stopped ;;
  *) echo "usage: $0 start|status|stop"; exit 1 ;;
esac
