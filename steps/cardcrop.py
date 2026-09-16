"""카드 규격 누끼 — 인물을 카드 비율(896x1152)로 잘라 RGBA 로 만든다.

알파 마스크의 경계 상자만 쓰므로 얼굴 검출이 필요 없다. serving/cutout-cpu/app/card.py 와 같은 규칙:
- 피사체 상단이 크롭 상단에서 6% 아래
- 크롭 폭은 피사체 폭의 1.18배 이상, 카드 비율을 유지하며 원본 밖으로 나가지 않는다
"""
from __future__ import annotations

import cv2
import numpy as np

CARD_W, CARD_H = 896, 1152
TOP_MARGIN = 0.06
SIDE_MARGIN = 1.18
ALPHA_THRESHOLD = 40


def subject_bbox(alpha: np.ndarray):
    ys, xs = np.where(alpha > ALPHA_THRESHOLD)
    if len(xs) == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def card_crop_box(size: tuple[int, int], bbox, card_w: int = CARD_W, card_h: int = CARD_H):
    """(left, top, right, bottom) in source pixels for the card-aspect crop."""
    W, H = size
    aspect = card_w / card_h
    if bbox is None:
        cw = min(W, int(H * aspect)); ch = int(cw / aspect)
        left = (W - cw) // 2; top = (H - ch) // 2
        return left, top, left + cw, top + ch
    x0, y0, x1, y1 = bbox
    sub_w = x1 - x0
    cx = (x0 + x1) / 2
    ch = min(H, int(max(sub_w * SIDE_MARGIN / aspect, (H - y0) / (1 - TOP_MARGIN))))
    cw = int(ch * aspect)
    if cw > W:
        cw = W; ch = int(cw / aspect)
    top = int(y0 - ch * TOP_MARGIN)
    top = max(0, min(top, H - ch))
    left = int(cx - cw / 2)
    left = max(0, min(left, W - cw))
    return left, top, left + cw, top + ch


def card_cutout_bgra(img_bgr: np.ndarray, card_w: int = CARD_W, card_h: int = CARD_H) -> np.ndarray:
    """사진(BGR) -> 배경 제거 + 카드 비율 크롭 + card_w x card_h BGRA."""
    from steps.cutout import cutout
    bgra = cutout(img_bgr)
    h, w = bgra.shape[:2]
    l, t, r, b = card_crop_box((w, h), subject_bbox(bgra[..., 3]), card_w, card_h)
    return cv2.resize(bgra[t:b, l:r], (card_w, card_h), interpolation=cv2.INTER_AREA if (r - l) > card_w else cv2.INTER_LANCZOS4)


def encode(bgra: np.ndarray, fmt: str = "webp") -> tuple[bytes, str]:
    """lossless webp(기본) 또는 png 바이트."""
    if fmt == "png":
        ok, buf = cv2.imencode(".png", bgra)
        return buf.tobytes(), "image/png"
    ok, buf = cv2.imencode(".webp", bgra, [cv2.IMWRITE_WEBP_QUALITY, 101])   # 101 = lossless
    return buf.tobytes(), "image/webp"
