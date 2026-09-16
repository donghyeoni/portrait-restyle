"""얼굴 교체 결과에 안경을 FLUX 인페인트로 그려 넣는다.

왜 인페인트인가
  inswapper 는 정체성 임베딩으로 얼굴을 새로 합성하므로 안경이 사라진다.
  원본 안경 픽셀을 정렬해 붙이는 방식(common.glasses.paste_glasses)은 눈 주위에
  밝기·선예도가 다른 패치가 남아 스티커처럼 보였다. 인페인트는 눈 영역만
  다시 그리므로 조명과 이음새가 주변과 맞는다. 정체성은 PuLID 로 고정한다.

비용
  장당 약 11초 (FLUX 1회). 안경 쓴 입력에만 든다.

한계
  안경은 문장으로 전달되므로 "둥근 얇은 어두운 메탈테" 종류만 맞고 세부 형태는
  모델이 그린다. 원본과 픽셀 단위로 같지는 않다.
"""
from __future__ import annotations

import json, pathlib, shutil, sys, time, uuid, urllib.request

import cv2
import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from engines.pulid import FluxPulidConfig, build   # noqa: E402

# 스윕으로 정한 값. 낮은 denoise 는 작은 얼굴에서 안경을 못 만들고,
# 높으면 색이 금색으로 흐른다. 아래 DECISIONS.md 참조.
DENOISE = 0.80
PULID = 0.8
GUIDANCE = 6.0
GROW = 0.35          # 원본 안경 범위를 얼마나 키운 타원을 다시 그릴지
FEATHER = 31

# 안경을 문장 맨 앞에 두고 색을 강하게 못박는다. 화풍 모델에서 확인한 것 —
# "metal-framed" 만 쓰면 금테로 작게 나온다.
POSITIVE = ("close-up photo of a man wearing large perfectly round eyeglasses with a very thin "
            "dark gunmetal grey wire frame, black-grey metal rims clearly visible against the skin, "
            "the eyeglasses sit naturally on the nose with correct perspective, sharp focus, "
            "natural skin texture, lighting matches the surroundings")
POSITIVE_F = POSITIVE.replace("a man", "a woman")

COMFY_IN = ROOT / "ComfyUI" / "input"


def _submit(node: str, graph: dict) -> bytes:
    req = urllib.request.Request(
        node + "/prompt",
        data=json.dumps({"prompt": graph, "client_id": uuid.uuid4().hex}).encode(),
        headers={"Content-Type": "application/json"})
    pid = json.loads(urllib.request.urlopen(req, timeout=60).read())["prompt_id"]
    while True:
        h = json.loads(urllib.request.urlopen(node + "/history/" + pid, timeout=30).read())
        if pid in h:
            st = h[pid].get("status", {})
            if st.get("status_str") == "error":
                msg = [m for m in st.get("messages", []) if m[0] == "execution_error"]
                raise RuntimeError(str(msg)[:600])
            if h[pid].get("outputs"):
                im = list(h[pid]["outputs"].values())[0]["images"][0]
                url = "%s/view?filename=%s&subfolder=%s&type=output" % (
                    node, im["filename"], im["subfolder"])
                return urllib.request.urlopen(url, timeout=180).read()
        time.sleep(0.3)


def eye_region_mask(src_mask, src_kps, dst_kps, shape, grow=GROW, feather=FEATHER):
    """원본 안경 마스크의 범위를 감싸는 타원을 대상 좌표로 옮긴다. 0~255."""
    ys, xs = np.nonzero(src_mask > 40)
    if len(xs) == 0:
        return None
    x0, x1, y0, y1 = xs.min(), xs.max(), ys.min(), ys.max()
    m = np.zeros(src_mask.shape, np.uint8)
    cv2.ellipse(m, (int((x0 + x1) / 2), int((y0 + y1) / 2)),
                (int((x1 - x0) / 2 * (1 + grow)), int((y1 - y0) / 2 * (1 + grow * 2))),
                0, 0, 360, 255, -1)
    M, _ = cv2.estimateAffinePartial2D(np.asarray(src_kps, np.float32),
                                       np.asarray(dst_kps, np.float32), method=cv2.LMEDS)
    if M is None:
        return None
    w = cv2.warpAffine(m, M, (shape[1], shape[0]), flags=cv2.INTER_NEAREST)
    return cv2.GaussianBlur(w, (feather, feather), 0)


def inpaint_glasses(dst, dst_kps, src_path: pathlib.Path, src_kps, src_mask,
                    gender: str = "male", node: str = "http://127.0.0.1:8189",
                    seed: int = 1000, denoise: float = DENOISE, pulid: float = PULID,
                    guidance: float = GUIDANCE):
    """dst(최종 896x1152 코스튬 결과)의 눈 영역을 안경 쓴 모습으로 다시 그린다."""
    mask = eye_region_mask(src_mask, src_kps, dst_kps, dst.shape)
    if mask is None:
        return dst
    tag = uuid.uuid4().hex[:10]
    img_name, mask_name, src_name = f"_gi_{tag}.png", f"_gi_{tag}_mask.png", f"_gi_{tag}_src.png"
    cv2.imwrite(str(COMFY_IN / img_name), dst)
    cv2.imwrite(str(COMFY_IN / mask_name), mask)
    shutil.copy(src_path, COMFY_IN / src_name)
    try:
        cfg = FluxPulidConfig(face_image=src_name,
                              positive=POSITIVE_F if gender == "female" else POSITIVE,
                              negative="", seed=seed, pulid_weight=pulid, guidance=guidance,
                              filename_prefix="glasses_inpaint/" + tag)
        g = build(cfg)
        # 빈 잠재 대신 결과 사진을 인코드하고, 눈 영역만 노이즈 마스크로 다시 그린다
        g["img"] = {"class_type": "LoadImage", "inputs": {"image": img_name}}
        g["mask"] = {"class_type": "LoadImageMask", "inputs": {"image": mask_name, "channel": "red"}}
        g["enc"] = {"class_type": "VAEEncode", "inputs": {"pixels": ["img", 0], "vae": ["vae", 0]}}
        g["lat"] = {"class_type": "SetLatentNoiseMask", "inputs": {"samples": ["enc", 0], "mask": ["mask", 0]}}
        g["sampler"]["inputs"]["latent_image"] = ["lat", 0]
        g["sampler"]["inputs"]["denoise"] = denoise
        del g["latent"]
        data = _submit(node, g)
        res = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        if res is None or res.shape[:2] != dst.shape[:2]:
            return dst
        return res
    finally:
        for n in (img_name, mask_name, src_name):
            try:
                (COMFY_IN / n).unlink()
            except OSError:
                pass
