"""GPU 큐 워커를 목표 개수까지 추가한다 (기존 GPU 워커 env 복제, 죽이지 않음).

  ./.venv/bin/python scripts/add_gpu_worker.py            # 3개가 되도록 부족분 추가
  ./.venv/bin/python scripts/add_gpu_worker.py --total 4

CPU 큐 워커·GPU 서비스는 건드리지 않는다. 누끼(CPU) 가 그림보다 길어 GPU 를 계속 그리게 하려면 GPU 큐 워커 3개 이상이 필요하다(측정 근거: docs/PERF_LOG.md 7절).
"""
from __future__ import annotations

import argparse
import os
import pathlib
import subprocess
import time


def env_of(pid: int) -> dict[str, str]:
    e = {}
    for kv in pathlib.Path(f"/proc/{pid}/environ").read_bytes().split(b"\0"):
        if b"=" in kv:
            k, v = kv.split(b"=", 1); e[k.decode()] = v.decode(errors="replace")
    return e


def gpu_workers() -> list[int]:
    pids = [int(p) for p in subprocess.run(["pgrep", "-f", "serving.worker"], capture_output=True, text=True).stdout.split()
            if int(p) != os.getpid()]
    return [p for p in pids if env_of(p).get("RABBIT_QUEUE", "").endswith(".gpu")]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--total", type=int, default=3)
    a = ap.parse_args()

    cur = gpu_workers()
    print(f"현재 GPU 큐 워커 {len(cur)}개:", [env_of(p).get("WORKER_ID") for p in cur])
    if not cur:
        print("기준 GPU 워커가 없다. deploy_offload.py 로 먼저 GPU 워커를 띄워라."); return 1
    need = a.total - len(cur)
    if need <= 0:
        print("이미 충분함."); return 0

    base = env_of(cur[0]); cwd = os.readlink(f"/proc/{cur[0]}/cwd"); exe = str(pathlib.Path(cwd) / ".venv/bin/python")
    used_ids = {env_of(p).get("WORKER_ID", "") for p in cur}
    used_ports = {int(env_of(p).get("HTTP_PORT", "0")) for p in cur}
    idx, port = 1, 18096
    started = []
    for _ in range(need):
        while f"gpu-worker-gpu-{idx:02d}" in used_ids:
            idx += 1
        while port in used_ports:
            port += 1
        env = dict(base)
        env["WORKER_ID"] = f"gpu-worker-gpu-{idx:02d}"
        env["HTTP_PORT"] = str(port)
        env["GPU_IMAGE_ONLY"] = "true"
        log = open(pathlib.Path.home() / f"worker-gpu-{idx:02d}.log", "ab")
        subprocess.Popen([exe, "-m", "serving.worker"], cwd=cwd, env=env, stdout=log,
                         stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
        used_ids.add(env["WORKER_ID"]); used_ports.add(port); started.append((env["WORKER_ID"], port))
        idx += 1; port += 1; time.sleep(2)

    time.sleep(6)
    now = gpu_workers()
    print("추가:", started)
    print(f"현재 GPU 큐 워커 {len(now)}개:", [(env_of(p).get('WORKER_ID'), env_of(p).get('HTTP_PORT')) for p in sorted(now)])
    print(subprocess.run(["bash", "-c", "for f in ~/worker-gpu-*.log; do echo \"-- $(basename $f)\"; grep -E '소비 시작|Error|bind' $f | tail -1; done"],
                         capture_output=True, text=True).stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
