"""Card-format cutout: crop the subject to the card aspect and return RGBA at the card size.

The crop is driven by the alpha mask (no face detector needed):
- subject bounding box from alpha > threshold
- crop width = subject width * side margin, at least the width implied by the card aspect
- head sits a little below the top edge; the crop never extends outside the source image
"""
from __future__ import annotations

import io

import numpy as np
from PIL import Image

from .engine import cutout

CARD_W, CARD_H = 896, 1152
TOP_MARGIN = 0.06       # subject top sits 6% below the crop top
SIDE_MARGIN = 1.18      # crop width relative to subject width
ALPHA_THRESHOLD = 40


def _subject_bbox(alpha: np.ndarray):
    ys, xs = np.where(alpha > ALPHA_THRESHOLD)
    if len(xs) == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def card_crop_box(size: tuple[int, int], bbox, card_w: int = CARD_W, card_h: int = CARD_H):
    """Return (left, top, right, bottom) in source pixels for the card-aspect crop."""
    W, H = size
    aspect = card_w / card_h
    if bbox is None:
        # no subject found: centred crop of the whole image
        cw = min(W, int(H * aspect)); ch = int(cw / aspect)
        left = (W - cw) // 2; top = (H - ch) // 2
        return left, top, left + cw, top + ch
    x0, y0, x1, y1 = bbox
    sub_w, sub_h = x1 - x0, y1 - y0
    cx = (x0 + x1) / 2
    # crop tall enough to keep the subject from its top down to the bottom of the image
    ch = min(H, int(max(sub_w * SIDE_MARGIN / aspect, (H - y0) / (1 - TOP_MARGIN))))
    cw = int(ch * aspect)
    if cw > W:                          # source too narrow for that height: shrink to width
        cw = W; ch = int(cw / aspect)
    top = int(y0 - ch * TOP_MARGIN)
    top = max(0, min(top, H - ch))
    left = int(cx - cw / 2)
    left = max(0, min(left, W - cw))
    return left, top, left + cw, top + ch


def card_cutout(image: Image.Image, card_w: int = CARD_W, card_h: int = CARD_H) -> Image.Image:
    """Photo -> RGBA card image (card_w x card_h) with background removed and subject framed."""
    rgba = cutout(image)                                   # full-resolution cutout first
    alpha = np.asarray(rgba.getchannel("A"))
    box = card_crop_box(rgba.size, _subject_bbox(alpha), card_w, card_h)
    return rgba.crop(box).resize((card_w, card_h), Image.LANCZOS)


def card_cutout_bytes(data: bytes, fmt: str = "webp", card_w: int = CARD_W, card_h: int = CARD_H) -> tuple[bytes, str]:
    img = Image.open(io.BytesIO(data))
    out = card_cutout(img, card_w, card_h)
    buf = io.BytesIO()
    if fmt == "png":
        out.save(buf, format="PNG", optimize=True)
        return buf.getvalue(), "image/png"
    # lossless WebP: `quality` is the compression effort here (100 = slowest); 60/method 2 encodes in ~1s
    out.save(buf, format="WEBP", lossless=True, quality=60, method=2)
    return buf.getvalue(), "image/webp"
