"""로컬 자산을 L40S 서버로 올린다 (Jupyter Contents API).

모델 가중치는 서버가 직접 받는 편이 빠르므로 제외한다.
올리는 것은 코드·설정·입력 이미지·마스크 — 즉 '로컬에서 만든 자산'이다.

    JUP_TOKEN=... python scripts/remote/push.py
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
REMOTE_ROOT = "portrait-restyle"      # 서버 홈 기준 상대경로

TEXT_EXT = {".py", ".yaml", ".yml", ".md", ".txt", ".json", ".sh"}
BIN_EXT = {".png", ".jpg", ".jpeg", ".webp", ".avif", ".npy", ".pth"}

# (로컬 상대경로, 재귀 여부)
TARGETS = [
    ("engines", True),                    # 처리 방식 3종 (inswapper / pulid / kontext) + ComfyUI 공용
    ("steps", True),                      # 전처리·후처리 부품 (얼굴·안경·리터치·크롭·업스케일·누끼)
    ("manifests", True),                  # 컬렉션 = 설정 파일
    ("runner.py", False),
    ("models/registry.yaml", False),      # 가중치 목록 (가중치 자체는 서버가 직접 받는다)
    ("serving", True),                    # FastAPI · ComfyUI 기동 스크립트 · EC2 누끼 컨테이너
    ("eval/scripts", True),
    ("scripts", True),
    ("docs", True),
    ("README.md", False),
]
SKIP_DIRS = {"__pycache__", ".git", "raw", "curated", "paired", "_rejected", "remote",
             "backlog"}   # manifests/backlog 는 로컬 전용 (서버·저장소에 올리지 않는다)
SKIP_FILES = {"_metadata.jsonl"}


def put(rel: str, data: bytes, is_text: bool):
    body = {"type": "file", "path": rel,
            "format": "text" if is_text else "base64",
            "content": data.decode("utf-8") if is_text else base64.b64encode(data).decode()}
    req = urllib.request.Request(f"{BASE}/api/contents/{rel}",
                                 data=json.dumps(body).encode(), method="PUT",
                                 headers={"Authorization": f"token {TOKEN}",
                                          "Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=120)


def mkdir(rel: str):
    try:
        req = urllib.request.Request(f"{BASE}/api/contents/{rel}",
                                     data=json.dumps({"type": "directory"}).encode(),
                                     method="PUT",
                                     headers={"Authorization": f"token {TOKEN}",
                                              "Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=60)
    except Exception:
        pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if not TOKEN:
        print("JUP_TOKEN 없음"); return 1

    files: list[pathlib.Path] = []
    for t, _ in TARGETS:
        base = LOCAL / t
        if not base.exists():
            continue
        if base.is_file():
            # TARGETS 에 파일 경로도 올 수 있다. rglob 은 파일에 걸면 빈 결과라
            # 예전에 gender.json 이 조용히 안 올라갔다.
            files.append(base)
            continue
        for p in base.rglob("*"):
            if not p.is_file():
                continue
            if any(s in p.parts for s in SKIP_DIRS) or p.name in SKIP_FILES:
                continue
            if p.suffix.lower() not in TEXT_EXT | BIN_EXT:
                continue
            files.append(p)

    total = sum(f.stat().st_size for f in files)
    print(f"{len(files)}개 파일 / {total/1048576:.1f}MB")
    if a.dry_run:
        for f in files[:25]:
            print("  ", f.relative_to(LOCAL))
        print(f"  ... 외 {max(0,len(files)-25)}개")
        return 0

    made = set()
    ok = fail = 0
    for f in files:
        rel = f.relative_to(LOCAL).as_posix()
        remote = f"{REMOTE_ROOT}/{rel}"
        d = str(pathlib.PurePosixPath(remote).parent)
        parts = d.split("/")
        for i in range(1, len(parts) + 1):
            sub = "/".join(parts[:i])
            if sub not in made:
                mkdir(sub); made.add(sub)
        is_text = f.suffix.lower() in TEXT_EXT
        try:
            put(remote, f.read_bytes(), is_text)
            ok += 1
        except Exception as e:
            print(f"  실패 {rel}: {e}"); fail += 1
        if ok % 25 == 0:
            print(f"  {ok}/{len(files)}")
    print(f"\n완료 {ok} / 실패 {fail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
