"""실행 중인 워커(python -m serving.worker) 옆에 **같은 환경으로 추가 소비자**를 띄운다 (병렬 처리, GPU 서버).

  ./.venv/bin/python scripts/scale_workers.py --total 3     # 3개가 되도록 부족분만 추가
  ./.venv/bin/python scripts/scale_workers.py --total 1 --stop-extra   # 추가분 정리(기본 워커는 남김)
  ./.venv/bin/python scripts/scale_workers.py --status

워커는 경쟁 소비자다 — RabbitMQ 가 메시지를 하나의 워커에만 준다. GPU 아이템은 GPU 서비스(동시성 1)가 직렬화하고,
CPU 아이템(직업·12지신)은 워커 수만큼 병렬 처리된다. 각 워커는 고유 WORKER_ID 와 서로 다른 HTTP 포트를 갖는다
(카탈로그·헬스는 기본 워커의 8090 하나면 충분하고, 추가 워커의 8091+ 는 겹침 방지용이다).
환경변수 값은 출력하지 않는다. 로그는 ~/worker.log(기본), ~/worker-NN.log(추가).
"""
from __future__ import annotations

import argparse
import os
import pathlib
import re
import signal
import subprocess
import time

BASE_HTTP = 8090


def workers() -> list[int]:
    out = subprocess.run(["pgrep", "-u", os.environ.get("USER", ""), "-f", "python -m serving.worker"],
                         capture_output=True, text=True).stdout.split()
    return sorted(int(p) for p in out if int(p) != os.getpid())


def env_of(pid: int) -> tuple[dict[str, str], str, str]:
    raw = pathlib.Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
    env = {}
    for kv in raw:
        if b"=" in kv:
            k, v = kv.split(b"=", 1)
            env[k.decode()] = v.decode(errors="surrogateescape")
    cwd = os.readlink(f"/proc/{pid}/cwd")
    wid = env.get("WORKER_ID", "gpu-worker")
    return env, cwd, wid


def describe(pid: int) -> str:
    env, _, wid = env_of(pid)
    return f"pid {pid}  WORKER_ID={wid}  HTTP_PORT={env.get('HTTP_PORT', BASE_HTTP)}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--total", type=int, default=3, help="목표 워커 수 (기본 3)")
    ap.add_argument("--stop-extra", action="store_true", help="목표보다 많으면 추가분(WORKER_ID 가 -NN)만 종료")
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args()

    pids = workers()
    if a.status or (not a.stop_extra and len(pids) >= a.total):
        print(f"워커 {len(pids)}개:")
        for p in pids:
            print("  " + describe(p))
        if a.status:
            return 0

    if not pids:
        print("기준 워커가 없다. 원래 기동 명령으로 먼저 하나 띄워라 (환경을 물려받을 수 없음).")
        return 1

    base_env, cwd, base_wid = env_of(pids[0])
    exe = str(pathlib.Path(cwd) / ".venv/bin/python")
    stem = re.sub(r"-\d+$", "", base_wid)                # gpu-worker-01 -> gpu-worker

    if a.stop_extra:
        for p in pids:
            _, _, wid = env_of(p)
            if re.search(r"-(0?[2-9]|[1-9]\d)$", wid) and p != pids[0]:
                os.kill(p, signal.SIGTERM)
                print("종료:", describe(p))
        return 0

    need = a.total - len(pids)
    if need <= 0:
        print("이미 충분함.")
        return 0

    # 이미 쓰인 HTTP 포트/인덱스 피하기
    used_ports = {int(env_of(p)[0].get("HTTP_PORT", BASE_HTTP)) for p in pids}
    idx = 2
    started = []
    for _ in range(need):
        while (BASE_HTTP + idx - 1) in used_ports:
            idx += 1
        port = BASE_HTTP + idx - 1
        wid = f"{stem}-{idx:02d}"
        env = dict(base_env)
        env["WORKER_ID"] = wid
        env["HTTP_PORT"] = str(port)
        log = open(pathlib.Path.home() / f"worker-{idx:02d}.log", "ab")
        subprocess.Popen([exe, "-m", "serving.worker"], cwd=cwd, env=env, stdout=log,
                         stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
        used_ports.add(port)
        started.append((wid, port))
        idx += 1
        time.sleep(2)

    time.sleep(6)
    now = workers()
    print(f"추가 기동: {started}")
    print(f"현재 워커 {len(now)}개:")
    for p in now:
        print("  " + describe(p))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
