"""서버 결과물을 로컬로 내려받는다 (Jupyter Contents API).

    JUP_TOKEN=... python scripts/remote/pull.py outputs/halloween/photocard_v2
"""
from __future__ import annotations
import sys as _sys
if hasattr(_sys.stdout, "reconfigure"):
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import argparse, base64, json, os, pathlib, urllib.request

HOST = os.environ["JUP_HOST"]
USER = os.environ["JUP_USER"]
TOKEN = os.environ.get("JUP_TOKEN", "")
BASE = f"http://{HOST}/user/{USER}"
LOCAL = pathlib.Path(__file__).resolve().parents[2]
REMOTE_ROOT = "portrait-restyle"


def get(path: str, content: int = 1):
    req = urllib.request.Request(f"{BASE}/api/contents/{path}?content={content}",
                                 headers={"Authorization": f"token {TOKEN}"})
    return json.loads(urllib.request.urlopen(req, timeout=180).read())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("remote_dir", help="서버 프로젝트 기준 상대경로")
    ap.add_argument("--dest", default=None, help="로컬 저장 경로 (기본: 같은 상대경로)")
    a = ap.parse_args()
    if not TOKEN:
        print("JUP_TOKEN 없음"); return 1

    listing = get(f"{REMOTE_ROOT}/{a.remote_dir}")
    files = [c for c in listing.get("content", []) if c["type"] == "file"]
    dest = pathlib.Path(a.dest) if a.dest else LOCAL / a.remote_dir
    dest.mkdir(parents=True, exist_ok=True)
    print(f"{len(files)}개 -> {dest}")

    ok = 0
    for c in files:
        d = get(c["path"])
        raw = (base64.b64decode(d["content"]) if d["format"] == "base64"
               else d["content"].encode("utf-8"))
        (dest / c["name"]).write_bytes(raw)
        ok += 1
        if ok % 10 == 0:
            print(f"  {ok}/{len(files)}")
    print(f"완료 {ok}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
