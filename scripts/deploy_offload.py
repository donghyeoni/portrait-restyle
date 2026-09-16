"""누끼 오프로드 배포: GPU 서비스 재시작(그림만) + GPU 큐 워커 2개(GPU_IMAGE_ONLY=true). CPU 큐 워커 4개는 건드리지 않는다."""
import subprocess, os, pathlib, signal, time, json
R = pathlib.Path.home() / "portrait-restyle"
HOME = pathlib.Path.home()


def env_of(pid):
    e = {}
    for kv in pathlib.Path(f"/proc/{pid}/environ").read_bytes().split(b"\0"):
        if b"=" in kv:
            k, v = kv.split(b"=", 1); e[k.decode()] = v.decode(errors="replace")
    return e


def worker_pids():
    return [int(p) for p in subprocess.run(["pgrep", "-f", "serving.worker"], capture_output=True, text=True).stdout.split()]


# 1) GPU 큐 워커 식별 + 환경 확보
gpu_pids, cpu_pids, base_env, cwd, exe = [], [], None, str(R), str(R / ".venv/bin/python")
for p in worker_pids():
    e = env_of(p)
    q = e.get("RABBIT_QUEUE", "")
    if q.endswith(".gpu"):
        gpu_pids.append(p); base_env = e; cwd = os.readlink(f"/proc/{p}/cwd")
    elif q.endswith(".cpu"):
        cpu_pids.append(p)
print("CPU 큐 워커(건드리지 않음):", len(cpu_pids), " GPU 큐 워커(교체 대상):", gpu_pids)
if base_env is None:
    raise SystemExit("GPU 큐 워커를 못 찾음 — 중단 (수동 확인 필요)")

# 2) GPU 서비스 재시작 (keeper 가 새 코드로 6~60초 내 복구). 22GB 누끼 메모리도 이때 정리
print("\n[1] GPU 서비스 재시작")
subprocess.run(["bash", "-c", "pkill -f '[s]erving.gpu.service'"], capture_output=True)
for i in range(20):
    time.sleep(6)
    h = subprocess.run(["bash", "-c", "curl -s --max-time 3 http://127.0.0.1:8080/health"], capture_output=True, text=True).stdout
    if '"ok"' in h:
        print(f"  서비스 복귀 {6*(i+1)}s: {h}"); break
else:
    print("  경고: 서비스 미복귀 — keeper 로그 확인 필요"); print(subprocess.run(["bash","-c",f"tail -3 {R}/logs/gpu_keeper.log"],capture_output=True,text=True).stdout)

# 3) 기존 GPU 큐 워커 종료
print("\n[2] 기존 GPU 큐 워커 종료:", gpu_pids)
for p in gpu_pids:
    try: os.kill(p, signal.SIGTERM)
    except ProcessLookupError: pass
for _ in range(30):
    if not any(q.endswith(".gpu") for q in (env_of(p).get("RABBIT_QUEUE","") for p in worker_pids())):
        break
    time.sleep(1)

# 4) GPU 큐 워커 2개 기동 (새 코드 + GPU_IMAGE_ONLY=true)
print("\n[3] GPU 큐 워커 2개 기동")
started = []
for idx, port in ((1, 18096), (2, 18097)):
    env = dict(base_env)
    env["WORKER_ID"] = f"gpu-worker-gpu-{idx:02d}"
    env["HTTP_PORT"] = str(port)
    env["GPU_IMAGE_ONLY"] = "true"
    env["RABBIT_QUEUE"] = base_env.get("RABBIT_QUEUE", "motion.ai.generate.item.gpu")
    log = open(HOME / f"worker-gpu-{idx:02d}.log", "ab")
    subprocess.Popen([exe, "-m", "serving.worker"], cwd=cwd, env=env,
                     stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
    started.append((env["WORKER_ID"], port))
    time.sleep(2)
time.sleep(8)

# 5) 검증
print("\n[4] 검증")
now = worker_pids()
rows = []
for p in sorted(now):
    e = env_of(p)
    rows.append((p, e.get("WORKER_ID","?"), e.get("RABBIT_QUEUE","?"), e.get("GPU_IMAGE_ONLY","-"), e.get("HTTP_PORT","-")))
for r in rows:
    print(f"  pid {r[0]}  {r[1]:20s} queue={r[2]:32s} IMAGE_ONLY={r[3]} port={r[4]}")
print("\n  == GPU 워커 소비 시작 로그 ==")
print(subprocess.run(["bash","-c","for f in ~/worker-gpu-01.log ~/worker-gpu-02.log; do echo \"-- $f\"; grep -E '소비 시작|Error|Traceback|bind' $f 2>/dev/null | tail -2; done"],capture_output=True,text=True).stdout)
print("  == GPU device1 메모리 (누끼 22GB 빠졌는지) ==")
print(subprocess.run(["bash","-c","nvidia-smi --query-gpu=index,memory.used,memory.total --format=csv,noheader | sed -n '2p'; nvidia-smi --query-compute-apps=pid,used_memory --format=csv,noheader"],capture_output=True,text=True).stdout)
print("DONE")
