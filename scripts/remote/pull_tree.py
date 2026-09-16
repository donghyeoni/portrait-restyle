"""서버 폴더를 하위까지 통째로 내려받는다 (pull.py 는 한 단계만 본다).

    JUP_TOKEN=... python scripts/remote/pull_tree.py data/output/0.jobs
"""
from __future__ import annotations
import sys as _sys
if hasattr(_sys.stdout, "reconfigure"):
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import argparse, base64, pathlib
from concurrent.futures import ThreadPoolExecutor
from pull import LOCAL, REMOTE_ROOT, get


def walk(path):
    for c in get(path, content=1).get("content", []):
        if c["type"] == "directory":
            yield from walk(c["path"])
        elif c["type"] == "file":
            yield c["path"]


DEST_MAP = {}   # 서버 상대경로 접두어 -> 로컬 경로 (로컬 폴더 이름이 다를 때)


def fetch(remote):
    d = get(remote)
    raw = base64.b64decode(d["content"]) if d["format"] == "base64" else d["content"].encode("utf-8")
    rel = remote[len(REMOTE_ROOT) + 1:]
    local = LOCAL / rel
    for src, dst in DEST_MAP.items():
        if rel.startswith(src):
            local = pathlib.Path(dst) / rel[len(src):].lstrip("/"); break
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_bytes(raw)
    return local


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("remote_dir")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--dest", default=None, help="로컬 저장 경로 (기본: 같은 상대경로). 로컬 폴더명이 다를 때 쓴다")
    a = ap.parse_args()
    if a.dest:
        DEST_MAP[a.remote_dir] = str((LOCAL / a.dest) if not pathlib.Path(a.dest).is_absolute() else a.dest)
    files = list(walk(f"{REMOTE_ROOT}/{a.remote_dir}"))
    print(f"{len(files)}개 -> {a.dest or (LOCAL / a.remote_dir)}")
    n = 0
    with ThreadPoolExecutor(a.workers) as ex:
        for _ in ex.map(fetch, files):
            n += 1
            if n % 50 == 0:
                print(f"  {n}/{len(files)}")
    print(f"완료 {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
