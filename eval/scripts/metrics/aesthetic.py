"""미적 품질 지표 — LAION improved-aesthetic-predictor.

CLIP ViT-L/14 임베딩 위에 얹는 MLP 회귀 헤드. 대략 1~10 점.
'잘 그려졌는가'를 거칠게 판별한다. 절대값보다 조합 간 상대 비교에 쓴다.

StyleMetric 과 CLIP 백본을 공유해 중복 로드를 피한다.
"""
from __future__ import annotations
import pathlib

import numpy as np
import torch
import torch.nn as nn

ROOT = pathlib.Path(__file__).resolve().parents[3]
HEAD = ROOT / "models" / "aesthetic" / "sac+logos+ava1-l14-linearMSE.pth"


class _MLP(nn.Module):
    """improved-aesthetic-predictor 의 구조. state_dict 키와 맞춰야 한다."""

    def __init__(self, dim: int = 768):
        super().__init__()
        self.layers = nn.Sequential(
            nn.Linear(dim, 1024), nn.Dropout(0.2),
            nn.Linear(1024, 128), nn.Dropout(0.2),
            nn.Linear(128, 64), nn.Dropout(0.1),
            nn.Linear(64, 16),
            nn.Linear(16, 1),
        )

    def forward(self, x):
        return self.layers(x)


class AestheticMetric:
    def __init__(self, style_metric, device: str | None = None):
        """style_metric: 이미 로드된 StyleMetric (CLIP 백본 공유)."""
        self.style = style_metric
        self.device = device or style_metric.device
        self.head = _MLP().to(self.device).eval()
        sd = torch.load(HEAD, map_location=self.device, weights_only=True)
        self.head.load_state_dict(sd)

    @torch.no_grad()
    def score(self, image: str | pathlib.Path) -> float:
        e = self.style.embed_image(image)          # L2 정규화된 CLIP 임베딩
        t = torch.from_numpy(e).float().unsqueeze(0).to(self.device)
        return float(self.head(t).squeeze())
