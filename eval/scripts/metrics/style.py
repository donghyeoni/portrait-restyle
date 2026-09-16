"""스타일 충실도 지표 — CLIP 유사도.

두 방식을 지원한다.

- 텍스트 기준: 화풍을 서술한 프롬프트와 결과 이미지의 코사인 유사도.
  참조 이미지가 없어도 즉시 쓸 수 있다. 할로윈처럼 '진품 셋'이
  존재하지 않는 화풍에는 이 방식만 쓴다.
- 이미지 기준: eval/refs/<style>/ 의 진품 셋과의 평균 유사도.
  참조가 확보되면 이쪽이 더 정확하다 (고흐).

CLIP 유사도의 절대값은 의미가 약하다 (보통 0.1~0.35 범위).
조합 간 상대 비교에만 쓴다.
"""
from __future__ import annotations
import pathlib

import numpy as np
import torch
from PIL import Image

ROOT = pathlib.Path(__file__).resolve().parents[3]
CLIP_DIR = ROOT / "models" / "clip" / "clip-vit-large-patch14"


class StyleMetric:
    def __init__(self, device: str | None = None):
        from transformers import CLIPModel, CLIPProcessor
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.model = CLIPModel.from_pretrained(str(CLIP_DIR)).to(self.device).eval()
        self.proc = CLIPProcessor.from_pretrained(str(CLIP_DIR))
        self._img_cache: dict[str, np.ndarray] = {}
        self._ref_cache: dict[str, np.ndarray] = {}

    @staticmethod
    def _as_tensor(out) -> torch.Tensor:
        """transformers 버전에 따라 get_*_features 가 텐서 대신
        ModelOutput 을 반환한다(5.x). 어느 쪽이든 텐서로 정규화한다."""
        if torch.is_tensor(out):
            return out
        for attr in ("image_embeds", "text_embeds", "pooler_output", "last_hidden_state"):
            v = getattr(out, attr, None)
            if torch.is_tensor(v):
                return v
        raise TypeError(f"텐서를 추출할 수 없음: {type(out)}")

    @torch.no_grad()
    def embed_image(self, path: str | pathlib.Path) -> np.ndarray:
        key = str(path)
        if key in self._img_cache:
            return self._img_cache[key]
        im = Image.open(key).convert("RGB")
        inp = self.proc(images=im, return_tensors="pt").to(self.device)
        # 투영 경로를 명시해 버전 간 동작 차이를 없앤다.
        # ViT-L/14 -> visual_projection 출력 768차원.
        pooled = self._as_tensor(self.model.vision_model(**inp)).squeeze(0)
        if pooled.ndim > 1:                      # last_hidden_state 로 떨어진 경우
            pooled = pooled[0]                   # CLS 토큰
        f = self.model.visual_projection(pooled)
        f = torch.nn.functional.normalize(f, dim=-1).cpu().numpy()
        self._img_cache[key] = f
        return f

    @torch.no_grad()
    def embed_text(self, text: str) -> np.ndarray:
        inp = self.proc(text=[text], return_tensors="pt",
                        padding=True, truncation=True).to(self.device)
        pooled = self._as_tensor(self.model.text_model(**inp)).squeeze(0)
        if pooled.ndim > 1:
            # EOS 토큰 위치의 hidden state 가 CLIP 텍스트 표현이다
            eos = inp["input_ids"].squeeze(0).argmax().item()                 if "input_ids" in inp else -1
            pooled = pooled[eos]
        f = self.model.text_projection(pooled)
        return torch.nn.functional.normalize(f, dim=-1).cpu().numpy()

    def score_text(self, image: str | pathlib.Path, prompt: str) -> float:
        return float(np.dot(self.embed_image(image), self.embed_text(prompt)))

    def _ref_bank(self, ref_dir: pathlib.Path) -> np.ndarray:
        key = str(ref_dir)
        if key not in self._ref_cache:
            files = [p for p in sorted(ref_dir.iterdir())
                     if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}]
            if not files:
                raise FileNotFoundError(f"참조 이미지 없음: {ref_dir}")
            self._ref_cache[key] = np.stack([self.embed_image(f) for f in files])
        return self._ref_cache[key]

    def score_refs(self, image: str | pathlib.Path, ref_dir: str | pathlib.Path) -> float:
        """참조 진품 셋과의 평균 코사인 유사도."""
        bank = self._ref_bank(pathlib.Path(ref_dir))
        return float(np.mean(bank @ self.embed_image(image)))
