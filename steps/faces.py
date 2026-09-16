"""InsightFace 모델 로더. 여러 곳에서 쓰므로 한 군데 둔다.

**두 모델 팩을 쓴다. 섞으면 안 된다.**
  antelopev2  검출·성별·정체성 채점 (glintr100)
  buffalo_l   inswapper 전용 (w600k_r50)

inswapper_128 은 buffalo_l 임베딩 공간에 맞춰 학습됐다.
antelopev2 임베딩을 넣으면 얼굴이 아예 안 바뀐다 (실측 정체성 0.02).
"""
from __future__ import annotations

import pathlib
import threading

ROOT = pathlib.Path(__file__).resolve().parents[1]
MODELS = ROOT / "models" / "insightface"

_apps: dict[str, object] = {}
_lock = threading.Lock()


def face_app(pack: str = "antelopev2", ctx_id: int = -1, det_size: int = 640,
             modules: list[str] | None = None):
    """FaceAnalysis 를 팩 이름별로 하나씩만 만들어 재사용한다.

    ctx_id  -1 이면 CPU, 0 이상이면 해당 GPU
    modules 제한하면 로딩이 빨라진다 (예: ["detection", "genderage"])
    """
    key = f"{pack}:{ctx_id}:{det_size}:{','.join(modules or [])}"
    with _lock:
        if key not in _apps:
            from insightface.app import FaceAnalysis
            providers = (["CUDAExecutionProvider", "CPUExecutionProvider"]
                         if ctx_id >= 0 else ["CPUExecutionProvider"])
            kw = {"name": pack, "root": str(MODELS), "providers": providers}
            if modules:
                kw["allowed_modules"] = modules
            app = FaceAnalysis(**kw)
            app.prepare(ctx_id=ctx_id, det_size=(det_size, det_size))
            _apps[key] = app
    return _apps[key]


def biggest(faces):
    """가장 큰 얼굴. 여러 명이 잡히면 주피사체로 본다."""
    return max(faces, key=lambda f: (f.bbox[2]-f.bbox[0]) * (f.bbox[3]-f.bbox[1]))
