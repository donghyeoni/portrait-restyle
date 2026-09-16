"""피부 리터치 (얼굴 파싱 마스크 안에서만 부드럽게).

5.weather 는 Kontext 가 입력 얼굴을 그대로 옮기는데, 원본의 약한 잡티를
움푹한 흉터처럼 과장한다 (man/002, outputs/_wx4_sheet.png). 프롬프트로
"clear smooth skin" 을 넣어도 안 지워진다. 그래서 Kontext 에 넣기 전에
피부(skin=1, nose=10)만 양방향 필터로 고르게 하고 눈·눈썹·입·머리·안경은 건드리지 않는다.
결과가 스티커처럼 보이지 않게 얼굴 크기에 비례한 반경과 가장자리 페더를 쓴다.
"""
from __future__ import annotations

import cv2
import numpy as np

from steps.glasses import _net

SKIN_LABELS = (1, 10)          # skin, nose (neck 14 는 옷 경계가 섞여 제외)


FACE_LABELS = (1, 2, 3, 4, 5, 6, 10, 11, 12, 13)   # 피부+눈썹+눈+안경+코+입 (머리·귀·목 제외)


def skin_mask(img, face, device="cpu", pad=1.35, labels=SKIN_LABELS):
    """BiSeNet 파싱 마스크 (0~1, 원본 크기). 기본은 피부만. glasses_ratio 와 같은 크롭 방식."""
    import torch
    H, W = img.shape[:2]
    x1, y1, x2, y2 = face.bbox
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    half = max(x2 - x1, y2 - y1) * pad
    a, b = int(cx - half), int(cy - half)
    c, d = int(cx + half), int(cy + half)
    crop = np.zeros((d - b, c - a, 3), np.uint8)
    sa, sb, sc, sd = max(a, 0), max(b, 0), min(c, W), min(d, H)
    crop[sb - b:sd - b, sa - a:sc - a] = img[sb:sd, sa:sc]
    t = cv2.resize(crop, (512, 512), interpolation=cv2.INTER_AREA)
    t = cv2.cvtColor(t, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
    t = torch.from_numpy((t - 0.5) / 0.5).permute(2, 0, 1)[None].to(device)
    with torch.no_grad():
        seg = _net(device)(t)[0].argmax(1)[0].cpu().numpy()
    m = np.isin(seg, labels).astype(np.float32)
    m = cv2.resize(m, (c - a, d - b), interpolation=cv2.INTER_LINEAR)
    full = np.zeros((H, W), np.float32)
    full[sb:sd, sa:sc] = m[sb - b:sd - b, sa - a:sc - a]
    return full


def smooth_skin(img, face, strength: float = 0.7, device: str = "cpu"):
    """피부 영역만 양방향 필터로 고르게 한다. strength 0 이면 원본."""
    if strength <= 0:
        return img
    fh = float(face.bbox[3] - face.bbox[1])
    m = skin_mask(img, face, device)
    # 파싱 경계를 조금 안으로 들이고 페더 -> 머리·눈 경계에 번짐이 없다
    k = max(3, int(fh * 0.02) | 1)
    m = cv2.erode(m, np.ones((k, k), np.uint8))
    m = cv2.GaussianBlur(m, (0, 0), max(1.0, fh * 0.02))
    # 양방향 필터: 색이 비슷한 이웃만 평균 -> 잡티는 지우고 윤곽은 남긴다
    sigma_s = max(3.0, fh * 0.03)
    base = cv2.bilateralFilter(img, d=0, sigmaColor=22, sigmaSpace=sigma_s)
    base = cv2.bilateralFilter(base, d=0, sigmaColor=16, sigmaSpace=sigma_s * 0.6)
    # 아주 고운 결(1px)은 되살려 플라스틱 느낌을 피한다
    fine = img.astype(np.float32) - cv2.GaussianBlur(img, (0, 0), 1.0).astype(np.float32)
    base = np.clip(base.astype(np.float32) + fine * 0.5, 0, 255)
    w = (m * strength)[..., None]
    return (img.astype(np.float32) * (1 - w) + base * w).astype(np.uint8)
