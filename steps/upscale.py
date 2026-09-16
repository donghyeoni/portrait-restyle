"""4x-UltraSharp 업스케일 (spandrel). GPU 가 없거나 실패하면 Lanczos 로 대체한다."""
from __future__ import annotations

import pathlib

import cv2
import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
MODEL = ROOT / "models" / "upscale_models" / "4x-UltraSharp.pth"
_UP: dict = {}


def upscale(img: np.ndarray, factor: float = 2.0) -> np.ndarray:
    h, w = img.shape[:2]
    target = (int(round(w * factor)), int(round(h * factor)))
    try:
        import torch
        from spandrel import ModelLoader
        if "m" not in _UP:
            _UP["m"] = ModelLoader().load_from_file(str(MODEL)).eval().cuda()
        t = torch.from_numpy(cv2.cvtColor(img, cv2.COLOR_BGR2RGB)).float().permute(2, 0, 1)[None].cuda() / 255.0
        with torch.no_grad():
            o = _UP["m"](t)
        o = (o[0].clamp(0, 1).permute(1, 2, 0).cpu().numpy() * 255).astype(np.uint8)
        return cv2.resize(cv2.cvtColor(o, cv2.COLOR_RGB2BGR), target, interpolation=cv2.INTER_AREA)
    except Exception:
        return cv2.resize(img, target, interpolation=cv2.INTER_LANCZOS4)
