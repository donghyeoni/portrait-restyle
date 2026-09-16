"""FLUX+PuLID 매트릭스 결과 채점.

정체성(ArcFace) / 화풍(CLIP text) / 심미(LAION) 세 축을 각각 낸다.
가중합 하나로 뭉개지 않는다 — 축끼리 상충하기 때문에(정체성을 지킬수록 화풍이
약해진다) 합쳐 버리면 어느 쪽이 문제인지 안 보인다.
"""
from __future__ import annotations

import argparse, json, pathlib, statistics, sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[0]))

from metrics.identity import IdentityMetric
from metrics.style import StyleMetric
from metrics.aesthetic import AestheticMetric

ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC = ROOT / "data" / "faces" / "normalized"

# 화풍 판정용 텍스트. 생성 프롬프트를 그대로 쓰면 자기 자신을 채점하는 꼴이라
# 항상 높게 나온다. 화풍의 핵심만 짧게 적어 독립적인 기준으로 둔다.
STYLE_TEXT = {
    "vampire": "a gothic vampire portrait in a candlelit dark library",
    "fantasy_noble": "a fantasy noble portrait in a grand castle hall",
    "cyberpunk": "a cyberpunk portrait on a neon-lit rainy night street",
    "hanbok": "a portrait in traditional Korean hanbok at a hanok courtyard",
    "kimono": "a portrait in a traditional Japanese kimono at a temple garden",
    "hanbok_manggeon": "a portrait in traditional Korean hanbok at a hanok courtyard",
    "hanbok_ikseongwan": "a portrait in traditional Korean hanbok at a hanok courtyard",
    "qipao": "a portrait in traditional Chinese dress at a classical chinese garden",
}


def main(a):
    out_dir = pathlib.Path(a.dir)
    files = sorted(out_dir.glob("*__*.png"))
    if not files:
        print(f"{out_dir} 에 결과 없음")
        return 1

    ident, sm = IdentityMetric(), StyleMetric()
    aes = AestheticMetric(sm)

    rows = []
    for f in files:
        name, st = f.stem.split("__", 1)
        src = SRC / f"{name}.png"
        key = a.style_as or st
        if key not in STYLE_TEXT:
            print(f"경고: '{key}' 판정 문구가 없어 화풍 점수가 무의미하다. "
                  f"--style-as 로 지정하세요")
        ir = ident.compare(src, f)
        rows.append({
            "input": name, "style": st,
            "identity": round(ir.similarity, 4) if ir.ok else None,
            "identity_status": ir.status,
            "style_score": round(sm.score_text(f, STYLE_TEXT.get(key, key)), 4),
            "aesthetic": round(aes.score(f), 3),
            "file": f.name,
        })

    hdr = "{:<7} {:<14} {:>9} {:>8} {:>8}  {}".format(
        "입력", "화풍", "정체성", "화풍", "심미", "비고")
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        idv = "{:>9.4f}".format(r["identity"]) if r["identity"] is not None else "{:>9}".format("얼굴없음")
        print("{:<7} {:<14} {} {:>8.4f} {:>8.2f}  {}".format(
            r["input"], r["style"], idv, r["style_score"], r["aesthetic"],
            "" if r["identity_status"] == "ok" else r["identity_status"]))

    print()
    for st in sorted({r["style"] for r in rows}):
        g = [r for r in rows if r["style"] == st]
        ids = [r["identity"] for r in g if r["identity"] is not None]
        print("{:<14} 정체성 평균 {:.4f} (n={}/{})  화풍 {:.4f}  심미 {:.2f}".format(
            st, statistics.mean(ids) if ids else float("nan"), len(ids), len(g),
            statistics.mean(r["style_score"] for r in g),
            statistics.mean(r["aesthetic"] for r in g)))

    (out_dir / "scores.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n저장 {out_dir / 'scores.json'}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dir", default="outputs/style/matrix")
    p.add_argument("--style-as", default="",
                   help="파일명의 화풍 대신 이 화풍 문구로 채점 (스윕용)")
    raise SystemExit(main(p.parse_args()))
