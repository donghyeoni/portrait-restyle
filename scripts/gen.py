"""사진 한 장을 골라 화풍을 입힌다. 로컬에서 실행한다.

  python scripts/gen.py 내사진.jpg --style vampire

서버(로드밸런서 8000)에 올리고 결과를 받아 저장한다.
"""
from __future__ import annotations

import argparse, os, pathlib, sys, time, urllib.parse

import requests

# Windows 기본 콘솔은 cp949 라 일부 기호에서 UnicodeEncodeError 가 난다
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

API = os.environ.get("PORTRAIT_API", "http://127.0.0.1:8000")


def main(a) -> int:
    src = pathlib.Path(a.image)
    if not src.exists():
        print(f"파일이 없습니다: {src}")
        return 1
    try:
        h = requests.get(f"{API}/health", timeout=10).json()
    except Exception as e:
        print(f"서버에 못 붙습니다 ({API}): {e}")
        print("서버가 내려갔거나 네트워크가 막힌 경우입니다.")
        return 1
    alive = [n for n in h["nodes"] if n["healthy"]]
    print(f"서버 {API}  노드 {len(alive)}/{len(h['nodes'])} 정상")

    # 화풍 목록은 서버가 알려준다. 클라이언트에 복사해 두면 어긋난다.
    styles = requests.get(f"{API}/styles", timeout=10).json()["styles"]
    if a.style not in styles:
        print(f"화풍은 {', '.join(styles)} 중 하나여야 합니다 (받은 값: {a.style})")
        return 1

    t0 = time.perf_counter()
    with open(src, "rb") as fh:
        r = requests.post(f"{API}/generate",
                          data={"style": a.style, "seed": str(a.seed),
                                "prompt": a.prompt, "gender": a.gender},
                          files={"image": (src.name, fh.read())},
                          timeout=a.timeout)
    if r.status_code != 200:
        try:
            msg = r.json().get("detail", r.text[:300])
        except Exception:
            msg = r.text[:300]
        print(msg)
        return 1
    j = r.json()
    img = j["images"][0]

    ir = requests.get(f"{API}/image", timeout=180, params={
        "node": j["node"], "filename": img["filename"], "subfolder": img["subfolder"]})
    ir.raise_for_status()

    out = pathlib.Path(a.out) if a.out else (
        pathlib.Path("outputs/manual") / f"{src.stem}__{a.style}.png")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(ir.content)
    print(f"{out}  ({time.perf_counter() - t0:.1f}초, {j['node'].rsplit(':', 1)[-1]}번 GPU)")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="증명사진에 화풍을 입힌다")
    p.add_argument("image", help="입력 사진 (정면·단색 배경·상반신 증명사진 권장)")
    p.add_argument("--style", default="vampire",
                   help="서버의 /styles 목록 중 하나 (틀리면 목록을 보여준다)")
    p.add_argument("--seed", type=int, default=0, help="0이면 매번 다른 결과")
    p.add_argument("--prompt", default="", help="추가 프롬프트 (비우면 화풍 프리셋만)")
    p.add_argument("--gender", required=True, choices=["male", "female", "auto"],
                   help="필수. auto 는 자동 판별을 쓰겠다는 명시적 선택이다 (표본 정확도 11/12)")
    p.add_argument("--out", default="", help="저장 경로 (기본 outputs/manual/)")
    p.add_argument("--timeout", type=int, default=600)
    raise SystemExit(main(p.parse_args()))
