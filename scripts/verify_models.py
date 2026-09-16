"""models/registry.yaml 의 sha256 과 실제 파일을 대조한다. 워커·GPU 이미지 빌드와 새 서버 준비에서 쓴다.

  python scripts/verify_models.py --root /app/models --only insightface-antelopev2 insightface-buffalo_l inswapper-128
  python scripts/verify_models.py                         # 저장소의 models/ 전체 (로컬·개발 서버)

폴더 항목의 sha256 은 'dir:' + (파일명 + 파일 sha256 을 정렬 순서로 이어 붙인 것의 sha256).
registry 에 sha256 이 없는(null) 항목은 존재만 확인하고, optional: true 항목은 없어도 넘어간다.
"""
from __future__ import annotations

import argparse, hashlib, os, pathlib, sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]


def sha_file(p: pathlib.Path, bufsize: int = 1 << 24) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(bufsize), b""):
            h.update(chunk)
    return h.hexdigest()


def sha_dir(p: pathlib.Path) -> str:
    h = hashlib.sha256()
    for x in sorted(f for f in p.rglob("*") if f.is_file()):
        h.update(x.name.encode()); h.update(sha_file(x).encode())
    return "dir:" + h.hexdigest()


def resolve(root: pathlib.Path, path: str) -> pathlib.Path:
    path = path.split("  ")[0].strip()
    if path.startswith("~"):
        return pathlib.Path(os.path.expanduser(path))
    if path.startswith("ComfyUI/"):
        return root.parent / path
    return root / path


def main(a) -> int:
    root = pathlib.Path(a.root)
    items = yaml.safe_load(pathlib.Path(a.registry).read_text(encoding="utf-8"))
    bad = 0
    for it in items:
        if a.only and it["id"] not in a.only:
            continue
        p = resolve(root, it["path"])
        if not p.exists():
            if it.get("optional"):
                print(f"skip     {it['id']:<28} (optional, 첫 실행에 받음)"); continue
            print(f"MISSING  {it['id']:<28} {p}"); bad += 1; continue
        want = it.get("sha256")
        if not want:
            print(f"present  {it['id']:<28} (registry 에 sha 없음)"); continue
        got = sha_dir(p) if p.is_dir() else sha_file(p)
        ok = got == want
        print(f"{'OK      ' if ok else 'MISMATCH'} {it['id']:<28} {got[:20]}…")
        bad += 0 if ok else 1
    print("all ok" if bad == 0 else f"{bad} problem(s)")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(ROOT / "models"))
    ap.add_argument("--registry", default=str(ROOT / "models" / "registry.yaml"))
    ap.add_argument("--only", nargs="*")
    raise SystemExit(main(ap.parse_args()))
