"""실행 중인 워커(python -m serving.worker)를 **같은 환경변수·같은 작업 폴더로** 다시 띄운다 (GPU 서버, 코드 반영용).

  ./.venv/bin/python scripts/restart_worker.py            # 현재 워커의 /proc/<pid>/environ 을 그대로 물려받아 재시작
  ./.venv/bin/python scripts/restart_worker.py --dry-run  # 무엇을 할지만 출력 (비밀값은 가린다)

워커가 처리 중이면 SIGTERM 뒤 최대 120초 기다린다(pika 소비 루프는 현재 메시지를 끝내고 나간다).
환경변수 값은 화면에 출력하지 않는다. 로그는 기존과 같이 ~/worker.log 에 이어 쓴다.
"""
from __future__ import annotations

import argparse
import os
import pathlib
import re
import signal
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
SECRET = re.compile(r"(SECRET|TOKEN|PASSWORD|KEY)", re.I)


def find_worker() -> list[int]:
    out = subprocess.run(["pgrep", "-u", os.environ.get("USER", ""), "-f", "python -m serving.worker"],
                         capture_output=True, text=True).stdout.split()
    return [int(p) for p in out if int(p) != os.getpid()]


def read_env(pid: int) -> dict[str, str]:
    raw = pathlib.Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
    env = {}
    for kv in raw:
        if b"=" in kv:
            k, v = kv.split(b"=", 1)
            env[k.decode()] = v.decode(errors="surrogateescape")
    return env


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--log", default=str(pathlib.Path.home() / "worker.log"))
    a = ap.parse_args()

    pids = find_worker()
    if not pids:
        print("실행 중인 워커가 없다. 환경변수를 물려받을 수 없으니 원래 기동 명령으로 띄워라.")
        return 1
    pid = pids[0]
    env = read_env(pid)
    cwd = os.readlink(f"/proc/{pid}/cwd")
    exe = pathlib.Path(cwd) / ".venv/bin/python"
    print(f"worker pid {pid}  cwd {cwd}")
    print("env:", ", ".join(f"{k}=<hidden>" if SECRET.search(k) or "amqp://" in v else f"{k}={v}"
                            for k, v in sorted(env.items()) if k.startswith(("AI_", "RABBIT_", "BACKEND_", "GPU_", "HTTP_", "WORKER_", "ORIGINAL_", "AWS_REGION", "CUTOUT_", "INSWAPPER_", "GLASSES_", "HEARTBEAT", "MAX_EXEC", "PREPROCESS"))))
    if a.dry_run:
        return 0

    os.kill(pid, signal.SIGTERM)
    for _ in range(120):
        if pid not in find_worker():
            break
        time.sleep(1)
    else:
        print("120초 안에 안 내려감 — SIGKILL")
        os.kill(pid, signal.SIGKILL)
        time.sleep(2)

    log = open(a.log, "ab")
    p = subprocess.Popen([str(exe), "-m", "serving.worker"], cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, start_new_session=True)
    time.sleep(8)
    alive = p.poll() is None
    print(f"새 워커 pid {p.pid}  {'실행 중' if alive else '즉시 종료됨 — 로그 확인'}")
    try:
        import urllib.request
        port = env.get("HTTP_PORT", "8090")
        print("health:", urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=5).read().decode())
    except Exception as e:                                # noqa: BLE001
        print("health 확인 실패:", e)
    return 0 if alive else 1


if __name__ == "__main__":
    sys.exit(main())
