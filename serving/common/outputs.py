"""출력 파일 만들기 — 요청받은 variant 그대로 (백엔드 전달서 7절).

  IMAGE         무손실 webp, RGB
  CUTOUT        무손실 webp, RGBA (누끼)
  THUMBNAIL     긴 변 320px, 비율 유지, webp 품질 80
  SUBJECT_MASK  image/png — 8비트 단일 채널 흑백 PNG. 배경 0, 피사체 1~255 (누끼 알파). 파일명 subject-mask.png. 백엔드 확정 2026-09-08

누끼 함수는 자리마다 다르다 — GPU 서비스는 rembg CUDA(steps.cutout), EC2 는 cutout-cpu 컨테이너 HTTP.
그래서 cutout_fn 을 주입받는다: BGR ndarray -> BGRA ndarray.
"""
from __future__ import annotations

import base64
import hashlib
import io
import pathlib
import sys
from typing import Callable

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from serving.common.item import VARIANTS   # noqa: E402
from steps.cutout import thumbnail, to_webp   # noqa: E402

THUMB_LONG_SIDE = 320
THUMB_QUALITY = 80


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_b64(data: bytes) -> str:
    """S3 native checksum(ChecksumSHA256) 은 raw digest 의 Base64."""
    return base64.b64encode(hashlib.sha256(data).digest()).decode()


def _png(img: np.ndarray) -> bytes:
    from PIL import Image
    pil = Image.fromarray(img) if img.ndim == 2 else Image.fromarray(img[..., ::-1])
    buf = io.BytesIO(); pil.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def build(img_bgr: np.ndarray, wanted: list[str], cutout_fn: Callable[[np.ndarray], np.ndarray] | None) -> dict[str, tuple[bytes, int, int]]:
    """variant -> (bytes, width, height). wanted 에 없는 것은 만들지 않는다. 누끼는 한 번만 계산해 CUTOUT·SUBJECT_MASK 가 공유."""
    out: dict[str, tuple[bytes, int, int]] = {}
    h, w = img_bgr.shape[:2]
    bgra = None
    if any(v in wanted for v in ("CUTOUT", "SUBJECT_MASK")):
        if cutout_fn is None:
            raise RuntimeError("CUTOUT/SUBJECT_MASK 요청이 있는데 누끼 함수가 없다")
        bgra = cutout_fn(img_bgr)
    if "IMAGE" in wanted:
        out["IMAGE"] = (to_webp(img_bgr, lossless=True), w, h)
    if "CUTOUT" in wanted:
        out["CUTOUT"] = (to_webp(bgra, lossless=True), bgra.shape[1], bgra.shape[0])
    if "SUBJECT_MASK" in wanted:
        mask = bgra[..., 3]
        out["SUBJECT_MASK"] = (_png(mask), mask.shape[1], mask.shape[0])
    if "THUMBNAIL" in wanted:
        th = thumbnail(img_bgr, THUMB_LONG_SIDE)
        out["THUMBNAIL"] = (to_webp(th, lossless=False, quality=THUMB_QUALITY), th.shape[1], th.shape[0])
    return out


def describe(variant: str, data: bytes, width: int, height: int, object_key: str, bucket_type: str) -> dict:
    """GPU 서비스 응답 outputs[] 한 항목 (width/height 포함). /complete 로 보낼 때는 to_callback() 으로 줄인다."""
    return {"variant": variant, "bucketType": bucket_type, "objectKey": object_key,
            "contentType": VARIANTS[variant], "fileSize": len(data), "checksumSha256": sha256_hex(data),
            "width": int(width), "height": int(height)}


CALLBACK_FIELDS = ("variant", "bucketType", "objectKey", "contentType", "fileSize", "checksumSha256")


def to_callback(outputs: list[dict]) -> list[dict]:
    """백엔드 /complete DTO 필드만 (전달서 5.3절). width/height·metrics 는 보내지 않는다."""
    return [{k: o[k] for k in CALLBACK_FIELDS} for o in outputs]
