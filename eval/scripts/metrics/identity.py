"""정체성 유지 지표 — ArcFace 코사인 유사도.

원본 증명사진과 결과물에서 각각 512차원 얼굴 임베딩을 뽑아 코사인 유사도를 낸다.
모델은 Phase 0 에서 받은 antelopev2 (scrfd_10g_bnkps + glintr100).

얼굴 미검출은 0점으로 뭉개지 않고 별도 상태로 기록한다.
"닮지 않았다"와 "얼굴이 사라졌다"는 완전히 다른 실패이며,
후자는 워크플로우가 붕괴했다는 신호다.
"""
from __future__ import annotations
import pathlib
from dataclasses import dataclass

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[3]
INSIGHTFACE_ROOT = ROOT / "models" / "insightface"


@dataclass
class IdentityResult:
    similarity: float | None   # None = 비교 불가
    status: str                # "ok" | "no_face_src" | "no_face_dst" | "no_face_both"
    n_faces_src: int
    n_faces_dst: int

    @property
    def ok(self) -> bool:
        return self.status == "ok"


class IdentityMetric:
    """ArcFace 임베딩 기반 정체성 유사도.

    det_size 는 증명사진(정면 단일 인물) 기준으로 640 이면 충분하다.
    """

    def __init__(self, det_size: int = 640, providers: list[str] | None = None):
        from insightface.app import FaceAnalysis
        self.app = FaceAnalysis(
            name="antelopev2",
            root=str(INSIGHTFACE_ROOT),
            providers=providers or ["CPUExecutionProvider"],
            allowed_modules=["detection", "recognition"],
        )
        self.app.prepare(ctx_id=0, det_size=(det_size, det_size))
        self._cache: dict[str, tuple[np.ndarray | None, int]] = {}

    def _embed(self, path: str | pathlib.Path) -> tuple[np.ndarray | None, int]:
        key = str(path)
        if key in self._cache:
            return self._cache[key]
        import cv2
        img = cv2.imread(key)
        if img is None:
            raise FileNotFoundError(key)
        faces = self.app.get(img)
        if not faces:
            out = (None, 0)
        else:
            # 얼굴이 여럿이면 가장 큰 것을 쓴다 (증명사진은 보통 1개)
            f = max(faces, key=lambda x: (x.bbox[2]-x.bbox[0]) * (x.bbox[3]-x.bbox[1]))
            e = f.normed_embedding.astype(np.float32)
            out = (e, len(faces))
        self._cache[key] = out
        return out

    def compare(self, src: str | pathlib.Path, dst: str | pathlib.Path) -> IdentityResult:
        es, ns = self._embed(src)
        ed, nd = self._embed(dst)
        if es is None and ed is None:
            return IdentityResult(None, "no_face_both", ns, nd)
        if es is None:
            return IdentityResult(None, "no_face_src", ns, nd)
        if ed is None:
            return IdentityResult(None, "no_face_dst", ns, nd)
        # normed_embedding 이므로 내적 = 코사인
        return IdentityResult(float(np.dot(es, ed)), "ok", ns, nd)
