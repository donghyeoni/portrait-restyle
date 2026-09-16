"""EC2 CPU 경로: 직업·12지신 한 장을 inswapper 로 만든다. 누끼는 참고 이미지의 사전 마스크(steps/refmask.py)를 얹고, 없을 때만 cutout-cpu(8001) 를 부른다.

안경 착용자는 Kontext 가 필요해 여기서 처리하지 않는다 (consumer 가 Cloud Run 으로 보낸다).
"""
from __future__ import annotations

import io
import logging
import pathlib
import sys

import cv2
import numpy as np
import requests

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from serving.common.item import Item, ItemError   # noqa: E402
from serving.worker import config as C            # noqa: E402

log = logging.getLogger("worker.cpu")


def _cutout_headers() -> dict:
    tok = C.CUTOUT_TOKEN
    if not tok and C.CUTOUT_TOKEN_FILE:
        try:
            tok = pathlib.Path(C.CUTOUT_TOKEN_FILE).read_text().strip()
        except OSError:
            tok = ""
    return {"Authorization": f"Bearer {tok}"} if tok else {}


def cutout_via_service(img_bgr: np.ndarray) -> np.ndarray:
    """누끼 서비스 호출(EC2 cutout-cpu 컨테이너 또는 GPU 서비스 /cutout): PNG 업로드 -> 알파 PNG."""
    ok, buf = cv2.imencode(".png", img_bgr)
    try:
        r = requests.post(C.CUTOUT_URL, files={"file": ("in.png", buf.tobytes(), "image/png")}, headers=_cutout_headers(), timeout=180)
    except requests.RequestException as e:
        raise ItemError("CUTOUT_FAILED", f"누끼 서비스 연결 실패: {e}", retryable=True) from e
    if r.status_code != 200:
        raise ItemError("CUTOUT_FAILED", f"누끼 서비스 {r.status_code}", retryable=True)
    bgra = cv2.imdecode(np.frombuffer(r.content, np.uint8), cv2.IMREAD_UNCHANGED)
    if bgra is None or bgra.ndim != 3 or bgra.shape[2] != 4:
        raise ItemError("CUTOUT_FAILED", "누끼 응답이 RGBA 가 아님", retryable=True)
    return bgra


def cutout_downscaled(img_bgr: np.ndarray) -> np.ndarray:
    """큰 그림(웹툰 1792x2304)은 저해상도에서 누끼 뜨고 마스크만 확대한다.

    누끼 경계는 저해상도로도 충분하고, 4메가픽셀 CPU 누끼 약 15초 -> 약 2초로 준다. 작은 그림(컨셉 896x1152)은 원본 그대로.
    """
    h, w = img_bgr.shape[:2]
    if max(h, w) <= C.CUTOUT_MAX_SIDE:
        return cutout_via_service(img_bgr)
    scale = C.CUTOUT_MAX_SIDE / max(h, w)
    small = cv2.resize(img_bgr, (max(1, round(w * scale)), max(1, round(h * scale))), interpolation=cv2.INTER_AREA)
    bgra = cutout_via_service(small)
    mask = cv2.resize(bgra[..., 3], (w, h), interpolation=cv2.INTER_LINEAR)
    return np.dstack([img_bgr, mask])


def _center_crop(img: np.ndarray, ar: float) -> np.ndarray:
    """얼굴을 못 찾았을 때: 화면 중앙 기준으로 목표 비율(ar=W/H)만큼 잘라낸다."""
    h, w = img.shape[:2]
    if w / h > ar:                       # 원본이 더 넓다 → 좌우를 자른다
        cw = int(round(h * ar)); x0 = (w - cw) // 2
        return img[:, x0:x0 + cw]
    ch = int(round(w / ar)); y0 = (h - ch) // 2
    return img[y0:y0 + ch, :]


def original(item: Item, coll, preset: dict, src_img: np.ndarray) -> np.ndarray:
    """원본 사진을 카드 규격(896x1152)으로 인물 중심 크롭한다. AI 생성 없음 (N 등급 카드).

    직업·12지신 참고본과 같은 규격이 되도록 steps/crop.py 의 upper_body_box 를 그대로 쓴다.
    얼굴을 못 찾으면 중앙 크롭으로 대체한다.
    """
    from steps.crop import upper_body_box
    from steps.faces import biggest, face_app
    out = coll.get("output", {})
    W_out, H_out = int(out.get("width", 896)), int(out.get("height", 1152))
    ar = W_out / H_out
    H, W = src_img.shape[:2]
    faces = face_app("antelopev2", ctx_id=C.INSWAPPER_CTX, modules=["detection"]).get(src_img)
    if faces:
        b = biggest(faces).bbox
        left, top, right, bot = upper_body_box(W, H, b, float(preset.get("above", 1.0)),
                                               float(preset.get("below", 3.2)), ar=ar)
        crop = src_img[int(top):int(bot), int(left):int(right)]
    else:
        crop = _center_crop(src_img, ar)
    return cv2.resize(crop, (W_out, H_out), interpolation=cv2.INTER_LANCZOS4)


def swap(item: Item, coll, preset: dict, src_img: np.ndarray, src_path: pathlib.Path) -> tuple[np.ndarray, pathlib.Path]:
    """참고 이미지(<reference>/<gender>/<preset key>.*) 에 원본 얼굴을 넣는다. 안경은 off. (결과, 참고 이미지 경로)"""
    from engines.inswapper import prepare_source, swap_into
    ref_dir = coll.reference_dir() / item.gender
    ref = next((p for p in ref_dir.iterdir() if p.stem == preset["key"]), None)
    if ref is None:
        raise ItemError("MODEL_ERROR", f"참고 이미지 없음: {ref_dir}/{preset['key']}", retryable=False)
    fa, source_face = prepare_source(src_img, C.INSWAPPER_CTX)
    if source_face is None:
        raise ItemError("NO_FACE", "원본에서 얼굴을 찾지 못함 (buffalo_l)")
    final, note = swap_into(ref, src_img, src_path, source_face, fa, None, glasses_mode="off", gender=item.gender)
    if final is None:
        raise ItemError("MODEL_ERROR", f"참고 이미지 얼굴 검출 실패: {note}", retryable=False)
    return final, ref
