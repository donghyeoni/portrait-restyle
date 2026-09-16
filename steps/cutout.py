"""누끼 — rembg + BiRefNet-portrait.

계약(docs/BACKEND_CONTRACT.md): 생성 직후 같은 워커가 잘라 CUTOUT 변형으로 올린다.
GPU 서버에서는 CUDAExecutionProvider(장당 0.7초), EC2 에서는 CPU(장당 3~6초).
EC2 쪽 컨테이너 구현은 serving/cutout-cpu 에 따로 있다.

BiRefNet 을 고른 근거: ISNet 은 머리 가장자리에 흰 테가 남았다 (outputs/_ani_w009_edges.png).
"""
from __future__ import annotations

import io
import os

import cv2
import numpy as np

MODEL = "birefnet-portrait"
# CUTOUT_PROVIDERS=cuda,cpu (기본) — CUDA 세션이 메모리 부족 등으로 실패하면 CPU 세션으로 한 번 더 시도한다.
# 공유 GPU(TLJH, ComfyUI 가 34GB 점유)에서 실제로 났던 오류: BFCArena Failed to allocate 822MB.
_PROVIDERS = [x.strip().lower() for x in os.environ.get("CUTOUT_PROVIDERS", "cuda,cpu").split(",") if x.strip()]
# CUTOUT_CUDA_DEVICE=<n>: 누끼 세션을 (CUDA_VISIBLE_DEVICES 기준) n번 장치에 올린다. 비우면 0번.
# 공유 서버에서 ComfyUI(FLUX)가 상주하는 장치와 분리해 BiRefNet CUDA OOM -> CPU 대체를 피하려는 용도.
_CUDA_DEVICE = os.environ.get("CUTOUT_CUDA_DEVICE", "").strip()
_S: dict = {}


def _new_session(provider: str):
    from rembg import new_session
    try:
        import torch  # noqa: F401  onnxruntime 보다 먼저 CUDA 라이브러리를 올린다
    except Exception:
        pass
    if provider != "cuda":
        return new_session(MODEL, providers=["CPUExecutionProvider"])
    if not _CUDA_DEVICE:
        return new_session(MODEL, providers=["CUDAExecutionProvider", "CPUExecutionProvider"])
    # rembg 2.0.83 은 (provider, options) 튜플을 문자열로 다뤄 경고 코드에서 죽는다. InferenceSession 에서만 옵션을 끼운다.
    import onnxruntime as ort
    real = ort.InferenceSession
    def with_device(path, *a, providers=None, **k):
        return real(path, *a, providers=[("CUDAExecutionProvider", {"device_id": int(_CUDA_DEVICE)}), "CPUExecutionProvider"], **k)
    ort.InferenceSession = with_device
    try:
        return new_session(MODEL, providers=["CUDAExecutionProvider", "CPUExecutionProvider"])
    finally:
        ort.InferenceSession = real


def session(provider: str | None = None):
    provider = provider or _PROVIDERS[0]
    if provider not in _S:
        _S[provider] = _new_session(provider)
    return _S[provider]


def cutout(img_bgr: np.ndarray) -> np.ndarray:
    """BGR -> BGRA (알파 = 인물 마스크). CUDA 실패 시 CPU 로 재시도."""
    from PIL import Image
    from rembg import remove
    pil = Image.fromarray(cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB))
    last = None
    for prov in _PROVIDERS:
        try:
            out = remove(pil, session=session(prov), post_process_mask=True)
            return cv2.cvtColor(np.array(out.convert("RGBA")), cv2.COLOR_RGBA2BGRA)
        except Exception as e:                    # noqa: BLE001
            last = e
            _S.pop(prov, None)
    raise RuntimeError(f"누끼 실패 ({', '.join(_PROVIDERS)}): {last}")


def to_webp(img: np.ndarray, lossless: bool = True, quality: int = 80) -> bytes:
    """BGR/BGRA -> webp 바이트. 누끼(알파)는 무손실로 저장한다."""
    from PIL import Image
    if img.shape[2] == 4:
        pil = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGRA2RGBA))
    else:
        pil = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
    buf = io.BytesIO()
    pil.save(buf, format="WEBP", lossless=lossless, quality=quality, method=4)
    return buf.getvalue()


def thumbnail(img: np.ndarray, long_side: int = 320) -> np.ndarray:
    h, w = img.shape[:2]
    s = long_side / max(h, w)
    return cv2.resize(img, (max(1, round(w * s)), max(1, round(h * s))), interpolation=cv2.INTER_AREA)
