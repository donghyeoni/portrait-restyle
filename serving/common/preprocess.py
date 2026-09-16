"""얼굴 전처리 — 검출·안경 판별. 한 원본에 아이템 23개가 오므로 결과를 JSON 으로 캐시한다 (규약 5절).

캐시 내용은 워커끼리만 쓰는 값이라 형식은 우리 마음대로: 얼굴 상자·5점 랜드마크·안경 여부·판별 점수.
임베딩(512차원)은 엔진마다 팩이 달라(antelopev2 / buffalo_l) 캐시하지 않고 매번 뽑는다 — 검출이 있으면 1초 안이다.
"""
from __future__ import annotations

import json
import pathlib
import sys

import cv2
import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from serving.common.item import ItemError   # noqa: E402

CACHE_VERSION = 1


def decode_image(data: bytes) -> np.ndarray:
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise ItemError("SOURCE_INVALID", "원본 이미지를 디코드할 수 없음")
    return img


def analyze(img: np.ndarray, ctx_id: int = -1) -> dict:
    """antelopev2 검출 + BiSeNet 안경 판별. 얼굴이 없으면 NO_FACE."""
    from steps.faces import biggest, face_app
    from steps.glasses import THRESH, glasses_ratio_ms
    fs = face_app("antelopev2", ctx_id=ctx_id).get(img)
    if not fs:
        raise ItemError("NO_FACE", "원본에서 얼굴을 찾지 못함")
    f = biggest(fs)
    ratio = float(glasses_ratio_ms(img, f))
    return {"version": CACHE_VERSION, "bbox": [float(x) for x in f.bbox], "kps": np.asarray(f.kps).tolist(),
            "det_score": float(f.det_score), "glasses": ratio > THRESH, "glasses_ratio": round(ratio, 4),
            "faces": len(fs), "image_size": [int(img.shape[1]), int(img.shape[0])]}


def to_json(info: dict) -> bytes:
    return json.dumps(info, ensure_ascii=False).encode("utf-8")


def from_json(data: bytes | None) -> dict | None:
    if not data:
        return None
    try:
        info = json.loads(data)
        return info if info.get("version") == CACHE_VERSION else None
    except Exception:
        return None
