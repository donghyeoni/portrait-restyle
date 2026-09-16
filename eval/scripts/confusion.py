"""화풍끼리 구별되는지 본다.

한복/기모노/치파오처럼 서로 인접한 화풍은 모델이 뭉갤 수 있다.
각 이미지를 모든 후보 문구에 대해 채점해서, 자기 화풍이 1위인지 확인한다.
자기 화풍이 지면 그 둘은 사실상 같은 화풍이라는 뜻이다.
"""
from __future__ import annotations

import argparse, pathlib, sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[0]))
from metrics.style import StyleMetric
from score_matrix import STYLE_TEXT


def main(a):
    d = pathlib.Path(a.dir)
    styles = a.styles.split(",")
    sm = StyleMetric()

    files = [f for f in sorted(d.glob("*__*.png")) if f.stem.split("__", 1)[1] in styles]
    if not files:
        print(f"{d} 에 대상 없음")
        return 1

    hdr = "{:<8}{:<12}".format("입력", "실제") + "".join("{:>13}".format(s[:12]) for s in styles) + "   판정"
    print(hdr); print("-" * len(hdr))
    win = 0
    per_style = {s: [0, 0] for s in styles}   # [맞음, 전체]
    for f in files:
        name, true = f.stem.split("__", 1)
        scores = {s: sm.score_text(f, STYLE_TEXT[s]) for s in styles}
        best = max(scores, key=scores.get)
        ok = best == true
        win += ok
        per_style[true][0] += ok; per_style[true][1] += 1
        cells = "".join(
            ("{:>12.4f}*" if s == best else "{:>13.4f}").format(scores[s]) for s in styles)
        print("{:<8}{:<12}".format(name, true) + cells + ("   O" if ok else f"   X ->{best}"))

    print()
    for s in styles:
        c, n = per_style[s]
        print("{:<12} 자기 화풍 1위 {}/{}".format(s, c, n))
    print("\n전체 {}/{} ({:.0f}%)".format(win, len(files), 100 * win / len(files)))
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dir", default="outputs/style/asian")
    p.add_argument("--styles", default="hanbok,kimono,qipao")
    raise SystemExit(main(p.parse_args()))
