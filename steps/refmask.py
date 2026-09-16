"""고정 참고 이미지(직업·12지신)의 사전 계산 누끼 마스크.

inswapper 는 얼굴 상자 안쪽 픽셀만 바꾸고, 상반신 크롭 박스는 참고 이미지의 얼굴 위치로 정해진다.
그러므로 누끼 경계는 사용자가 누구든 같다 -> 참고 이미지마다 마스크를 한 번만 만들어 두면 런타임에 누끼 모델이 필요 없다
(측정: BiRefNet CPU 14.6초 -> 배열 합치기 0.01초). 안경 Kontext 보정도 눈 주변만 바꾸므로 같은 마스크를 쓴다.

파일 (참고 이미지 옆):
  <ref 이름>.mask.png   8비트 단일 채널, 최종 크롭 크기(896x1152). 0 배경, 255 인물
  <ref 이름>.mask.json  {"ref_sha256", "above", "box", "size", "model", "made"}
참고 이미지나 크롭 규칙(above)이 바뀌면 sha256/above 가 달라져 load() 가 None 을 돌려주고, 호출 쪽은 누끼 모델로 떨어진다.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import logging
import pathlib
from typing import Callable

import cv2
import numpy as np

log = logging.getLogger("refmask")
_SHA: dict[str, str] = {}
_WARNED: set[str] = set()


def mask_paths(ref_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    # with_suffix 를 쓰면 ".mask" 가 확장자로 취급돼 "<stem>.png" 가 되어 참고 이미지와 이름이 겹친다 -> with_name 으로 만든다
    return ref_path.with_name(ref_path.stem + ".mask.png"), ref_path.with_name(ref_path.stem + ".mask.json")


def ref_sha256(ref_path: pathlib.Path) -> str:
    key = str(ref_path)
    if key not in _SHA:
        _SHA[key] = hashlib.sha256(ref_path.read_bytes()).hexdigest()
    return _SHA[key]


def load(ref_path: pathlib.Path, shape: tuple[int, ...], above: float) -> np.ndarray | None:
    """검증된 마스크(H,W uint8) 또는 None. 없거나 원본·크롭 규칙·크기가 다르면 None."""
    png, meta_p = mask_paths(ref_path)
    if not (png.exists() and meta_p.exists()):
        return None
    try:
        meta = json.loads(meta_p.read_text(encoding="utf-8"))
    except Exception:                                  # noqa: BLE001
        return None
    if meta.get("ref_sha256") != ref_sha256(ref_path) or abs(float(meta.get("above", -1)) - above) > 1e-6:
        return None
    m = cv2.imread(str(png), cv2.IMREAD_UNCHANGED)
    if m is None or m.ndim != 2 or m.shape[:2] != tuple(shape[:2]):
        return None
    return m


def apply(img_bgr: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """BGR + 마스크 -> BGRA (outputs.build 의 cutout_fn 과 같은 계약)."""
    return np.dstack([img_bgr, mask])


def cutout_fn(ref_path: pathlib.Path, above: float,
              fallback: Callable[[np.ndarray], np.ndarray]) -> Callable[[np.ndarray], np.ndarray]:
    """마스크가 있으면 얹고, 없으면 fallback(누끼 모델) 을 부른다. 안전망이라 마스크 미비가 실패로 이어지지 않는다."""
    def fn(img_bgr: np.ndarray) -> np.ndarray:
        m = load(ref_path, img_bgr.shape, above)
        if m is not None:
            return apply(img_bgr, m)
        if str(ref_path) not in _WARNED:
            _WARNED.add(str(ref_path))
            log.warning("사전 마스크 없음/불일치 -> 누끼 모델 사용: %s (scripts/precompute_masks.py 실행)", ref_path)
        return fallback(img_bgr)
    return fn


def build(ref_path: pathlib.Path, fa, above: float, cutout: Callable[[np.ndarray], np.ndarray]) -> tuple[np.ndarray, dict]:
    """참고 이미지 한 장: 얼굴 검출 -> swap_into 와 같은 상반신 크롭 -> 누끼 -> 알파를 저장용으로 돌려준다."""
    from engines.inswapper import crop_upper
    from steps.faces import biggest
    tgt = cv2.imread(str(ref_path))
    if tgt is None:
        raise ValueError(f"읽을 수 없음: {ref_path}")
    faces = fa.get(tgt)
    if not faces:
        raise ValueError(f"참고 이미지에서 얼굴을 찾지 못함: {ref_path}")
    face = biggest(faces)
    final, box, _ = crop_upper(tgt, face.bbox, above)
    bgra = cutout(final)
    mask = bgra[..., 3]
    meta = {"ref_sha256": ref_sha256(ref_path), "above": above, "box": [int(v) for v in box],
            "size": [int(mask.shape[1]), int(mask.shape[0])], "model": "birefnet-portrait",
            "made": _dt.datetime.now().isoformat(timespec="seconds")}
    return mask, meta


def save(ref_path: pathlib.Path, mask: np.ndarray, meta: dict) -> pathlib.Path:
    png, meta_p = mask_paths(ref_path)
    cv2.imwrite(str(png), mask)
    meta_p.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return png
