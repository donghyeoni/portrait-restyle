"""코스튬 레퍼런스를 상반신으로 크롭한다.

전신 제품 사진은 얼굴이 전체 높이의 8~11% 밖에 안 된다.
그 상태로는 얼굴을 바꿔 넣어도 정체성이 실리지 않는다.
얼굴 bbox 를 기준으로 상반신만 잘라 얼굴 비중을 올린다.

출력 비율은 생성 캔버스와 같은 896:1152 로 맞춘다.
"""
from __future__ import annotations

import argparse, json, pathlib, sys

import cv2
import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from steps.faces import biggest, face_app   # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
DATA = ROOT / "data"

AR = 896 / 1152          # 생성 캔버스 비율

# 코스튬별 예외. --auto-above 실측값을 여기에 고정한다.
OVERRIDE: dict[str, dict[str, float]] = {}
WHITE = 244              # 제품 사진 배경 판정 임계값
HEAD_SPAN = 1.6          # 얼굴 폭의 몇 배까지를 "머리 위 구조물" 로 볼지

# 얼굴 높이의 배수로 크롭 범위를 잡는다. --above / --below 로 조절한다.
# 코스튬마다 머리 위 구조물(당나귀 귀, 판다 귀, 벌 더듬이, 해바라기 꽃잎,
# 원시인 올림머리)의 높이가 달라서 above 는 개별 조정이 필요할 수 있다.


def upper_body_box(W, H, face_bbox, above=1.0, below=3.2, ar=AR):
    """얼굴 bbox 기준 상반신 크롭 박스. crop_reference 와 face_swap 이 함께 쓴다.

    좌표를 _meta.json 파일로 주고받다가 파일이 없어 깨진 적이 있어
    계산 자체를 공유한다.
    """
    fx0, fy0, fx1, fy1 = face_bbox
    fh = fy1 - fy0
    cx = (fx0 + fx1) / 2
    top, bot = fy0 - above * fh, fy1 + below * fh
    ch = bot - top
    cw = ch * ar
    left, right = cx - cw / 2, cx + cw / 2
    if cw > W:
        cw = W; ch = cw / ar
        top = max(0, min(top, H - ch)); bot = top + ch
        left, right = 0, W
    else:
        if left < 0:   right -= left; left = 0
        if right > W:  left -= (right - W); right = W
    if ch > H:
        ch = H; cw = ch * ar
        top, bot = 0, H
        left = max(0, min(cx - cw / 2, W - cw)); right = left + cw
    else:
        if top < 0:   bot -= top; top = 0
        if bot > H:   top -= (bot - H); bot = H
    return int(left), int(top), int(right), int(bot)


def main(a) -> int:
    SRC = DATA / a.collection / a.set
    DST = pathlib.Path(a.out) if a.out else (DATA / a.collection / "crop" / a.set)
    DST.mkdir(parents=True, exist_ok=True)
    meta = {}
    print("{:<20}{:>12}{:>12}{:>10}".format("파일", "원본", "크롭", "얼굴비중"))
    print("-" * 56)
    for f in sorted(p for p in SRC.iterdir()
                    if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")):
        img = cv2.imdecode(np.frombuffer(f.read_bytes(), np.uint8), cv2.IMREAD_COLOR)
        H, W = img.shape[:2]
        faces = face_app("antelopev2").get(img)
        if not faces:
            print(f"{f.stem:<20} 얼굴 검출 실패 — 건너뜀")
            continue
        b = biggest(faces).bbox
        fx0, fy0, fx1, fy1 = b
        fh = fy1 - fy0
        cx = (fx0 + fx1) / 2

        above = OVERRIDE.get(f.stem, {}).get('above', a.above)
        if a.auto_above and f.stem not in OVERRIDE:
            # 흰 배경 위로 솟은 구조물(귀·더듬이·꽃잎·올림머리)의 실제 높이를 잰다.
            # 로고가 있는 경우가 있어 얼굴 주변 열만 본다.
            fw = fx1 - fx0
            x0 = max(0, int(cx - HEAD_SPAN * fw)); x1 = min(W, int(cx + HEAD_SPAN * fw))
            band = img[:int(fy0), x0:x1]
            nonwhite = (band < WHITE).any(axis=2).any(axis=1)
            idx = np.nonzero(nonwhite)[0]
            if len(idx):
                need = (fy0 - idx[0]) / fh + a.margin
                above = max(a.above, round(float(need), 2))
        below = OVERRIDE.get(f.stem, {}).get('below', a.below)
        left, top, right, bot = upper_body_box(W, H, b, above, below)
        crop = img[int(top):int(bot), int(left):int(right)]
        out = cv2.resize(crop, (a.width, a.height), interpolation=cv2.INTER_LANCZOS4)
        cv2.imwrite(str(DST / f"{f.stem}.png"), out)

        auto_note = f"  above={above}" if above != a.above else ""
        ratio = (fh / (bot - top)) * a.height
        meta[f.stem] = {"src_size": [int(W), int(H)],
                        "crop": [int(left), int(top), int(right), int(bot)],
                        "face_h_out_px": int(round(ratio)),
                        "face_ratio": round(float(fh) / float(bot - top), 3)}
        meta[f.stem]["above"] = float(above)
        print("{:<20}{:>12}{:>12}{:>10}{}".format(
            f.stem, f"{W}x{H}", f"{int(right-left)}x{int(bot-top)}",
            f"{ratio/a.height*100:.0f}%", auto_note))

    (DST / "_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n{len(meta)}장 -> {DST}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--collection", default="zodiac",
                   help="코스튬 모음 이름. data/<이름>/{male,female}/ 을 읽는다")
    p.add_argument("--set", default="male", choices=["male", "female"],
                   help="레퍼런스 세트. 같은 코스튬·포즈에 모델 성별만 다르다")
    p.add_argument("--out", default="",
                   help="출력 디렉터리 (기본: data/zodiac/crop/<세트>)")
    p.add_argument("--auto-above", action="store_true",
                   help="흰 배경 기준으로 머리 위 구조물 높이를 실측해 above 를 정한다")
    p.add_argument("--margin", type=float, default=0.15,
                   help="실측값에 더할 여유 (얼굴 높이 배수)")
    p.add_argument("--above", type=float, default=1.0,
                   help="얼굴 높이의 몇 배만큼 위로 (모자·귀·더듬이가 잘리면 키운다)")
    p.add_argument("--below", type=float, default=3.2,
                   help="얼굴 높이의 몇 배만큼 아래로 (가슴~허리)")
    p.add_argument("--width", type=int, default=896)
    p.add_argument("--height", type=int, default=1152)
    raise SystemExit(main(p.parse_args()))
