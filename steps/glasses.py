"""안경 착용 판별.

화풍 모델은 얼굴 임베딩 512차원만 가져가고 나머지를 새로 그린다.
안경은 그 임베딩에 담기지 않아서 결과에서 사라진다. 프롬프트로 넣어야 하는데,
안경을 안 쓴 사람에게 씌우면 안 되므로 입력마다 판별한다.

facexlib 의 BiSeNet 얼굴 파싱에 안경 클래스(eye_g = 6)가 있다.
안경 화소를 피부 화소로 나눈 비율로 판정한다. 얼굴 크기에 무관해진다.

실측 (data/faces/normalized 8명):
  안경 착용 1명 0.0904, 미착용 7명 전원 0.0000. 애매한 구간이 없다.
"""
from __future__ import annotations

import cv2
import numpy as np

EYE_G, SKIN = 6, 1
THRESH = 0.02
_NET: dict = {}


def _net(device="cpu"):
    if "n" not in _NET:
        from facexlib.parsing import init_parsing_model
        m = init_parsing_model(model_name="bisenet", device=device)
        m.eval()
        _NET["n"], _NET["d"] = m, device
    return _NET["n"]


def glasses_ratio(img, face, device="cpu", pad=1.35) -> float:
    """안경 화소 / 피부 화소. BiSeNet 은 얼굴에 맞춰 잘라 넣어야 제대로 나온다."""
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
    t = torch.from_numpy((t - 0.5) / 0.5).permute(2, 0, 1)[None].to(_NET.get("d", device))
    with torch.no_grad():
        seg = _net(device)(t)[0].argmax(1)[0].cpu().numpy()
    return float((seg == EYE_G).sum()) / max(int((seg == SKIN).sum()), 1)


def glasses_ratio_ms(img, face, device="cpu") -> float:
    """다중 스케일. 얼굴이 크면(500px+) 512 로 줄일 때 얇은 테가 사라져 0 이 나온다
    (man/012: 545px 얼굴, 둥근 얇은 메탈테 -> 0.0000). 얼굴을 더 크게 잡은 크롭도
    함께 보고 큰 값을 쓴다."""
    return max(glasses_ratio(img, face, device, pad=1.35),
               glasses_ratio(img, face, device, pad=0.85))


def has_glasses(img, face, device="cpu") -> bool:
    return glasses_ratio_ms(img, face, device) > THRESH

def glasses_mask(img, face, device="cpu", pad=1.35, feather=1):
    """안경 마스크. 큰 얼굴에서 단일 스케일이 비면 더 큰 크롭을 시도하고, 그래도 비면
    눈 랜드마크로 타원을 만든다 — 마스크가 비면 코스튬 안경 인페인트가 통째로
    건너뛰어져 man/012·woman/012 가 안경 없이 나갔다 (2026-09-03)."""
    m = _glasses_mask_one(img, face, device, pad, feather)
    if int((m > 40).sum()) < 50:
        m = _glasses_mask_one(img, face, device, 0.85, feather)
    if int((m > 40).sum()) < 50:
        k = np.asarray(face.kps, np.float32)          # 좌눈, 우눈, 코, 좌입, 우입
        le, re_ = k[0], k[1]; cx, cy = (le + re_) / 2
        d = float(np.linalg.norm(re_ - le))
        m = np.zeros(img.shape[:2], np.uint8)
        cv2.ellipse(m, (int(cx), int(cy)), (int(d * 1.15), int(d * 0.45)), 0, 0, 360, 255, -1)
    return m


def _glasses_mask_one(img, face, device="cpu", pad=1.35, feather=1):
    """안경 화소 마스크를 원본 이미지 좌표로 되돌려 반환한다. 0~255 단채널.

    얼굴 교체 결과에 실제 안경을 얹을 때 쓴다. inswapper 는 정체성 임베딩으로
    얼굴을 새로 합성하므로 안경이 사라진다 — 프롬프트가 없으니 픽셀로 옮긴다.
    """
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
    t = torch.from_numpy((t - 0.5) / 0.5).permute(2, 0, 1)[None].to(_NET.get("d", device))
    with torch.no_grad():
        seg = _net(device)(t)[0].argmax(1)[0].cpu().numpy()

    m = (seg == EYE_G).astype(np.uint8) * 255
    m = cv2.resize(m, (crop.shape[1], crop.shape[0]), interpolation=cv2.INTER_LINEAR)
    full = np.zeros((H, W), np.uint8)
    full[sb:sd, sa:sc] = m[sb - b:sd - b, sa - a:sc - a]
    return cv2.GaussianBlur(full, (feather * 2 + 1,) * 2, 0)


def paste_glasses(dst, dst_kps, src_img, src_kps, src_mask, grow=0.18, feather=0.22):
    """원본 안경을 대상 얼굴에 정렬해 얹는다.

    5점 랜드마크(좌눈·우눈·코·좌입·우입)로 상사변환을 구해 원본을 대상
    좌표로 옮긴다. 평면 내 회전·크기·이동은 맞지만 고개를 돌린 각도까지는
    보정하지 못한다 — 코스튬 레퍼런스는 대부분 정면이라 실용적으로 통한다.

    **크롭·확대가 끝난 최종 이미지에 붙여야 한다.** 레퍼런스의 얼굴은 80px
    남짓이라 교체 직후에 붙이면 안경테가 1픽셀 밑으로 줄어 흐려진다.

    테만 오려 붙이지 않는다. BiSeNet 이 512px 에서 얇은 테를 온전히 잡지 못해
    마스크가 조각나기 때문이다. 대신 마스크로 **위치만** 잡고, 그 영역을
    감싸는 타원을 부드럽게 blend 한다. 같은 인물이므로 눈까지 함께 와도 된다.
    """
    ys, xs = np.nonzero(src_mask > 40)
    if len(xs) == 0:
        return dst
    x0, x1 = int(xs.min()), int(xs.max())
    y0, y1 = int(ys.min()), int(ys.max())
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    rx, ry = (x1 - x0) / 2.0 * (1 + grow), (y1 - y0) / 2.0 * (1 + grow * 2)

    # 원본 좌표에서 타원 알파를 만든다. 경계는 반지름 비율로 부드럽게 흐린다.
    alpha = np.zeros(src_mask.shape, np.uint8)
    cv2.ellipse(alpha, (int(cx), int(cy)), (int(rx), int(ry)), 0, 0, 360, 255, -1)
    k = max(3, int(min(rx, ry) * feather) | 1)
    alpha = cv2.GaussianBlur(alpha, (k, k), 0)

    H, W = dst.shape[:2]
    M, _ = cv2.estimateAffinePartial2D(
        np.asarray(src_kps, np.float32), np.asarray(dst_kps, np.float32),
        method=cv2.LMEDS)
    if M is None:
        return dst
    warp = cv2.warpAffine(src_img, M, (W, H), flags=cv2.INTER_LANCZOS4)
    wa = cv2.warpAffine(alpha, M, (W, H), flags=cv2.INTER_LINEAR)
    a = (wa.astype(np.float32) / 255.0)[..., None]
    return (warp * a + dst * (1 - a)).astype(dst.dtype)
