"""얼굴 교체 결과에 원본 안경을 FLUX Kontext 로 그려 넣는다 (눈 영역만).

glasses_inpaint 와 같은 자리(눈 영역 타원)를 다시 그리지만, 안경을 **문장**으로
설명하는 대신 **원본 사진을 참조 이미지로 직접 보여준다**(ReferenceLatent).
그래서 테 모양·두께·색이 원본에 가깝다. 정체성은 마스크 밖 inswapper 얼굴이
그대로 남아 유지된다.

주의
  Kontext 에 코스튬 사진과 원본을 옆으로 붙여(ImageStitch) 참조로 주고, 작업
  잠재는 코스튬 사진을 인코드한 것에 눈 마스크를 씌운다. 마스크를 얼굴 전체로
  키우면 모델이 모자 같은 것을 지어내고 정체성이 떨어졸다 — 실측 0.69 vs 0.84.
  눈 영역으로 좁혀야 한다.

가중치
  models/unet/flux1-dev-kontext_fp8_scaled.safetensors (11.9GB, Comfy-Org 미러)
  라이선스는 FLUX.1-dev 와 같은 비상업.
"""
from __future__ import annotations

import json, pathlib, shutil, sys, time, uuid, urllib.request

import cv2
import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from steps.glasses_inpaint import eye_region_mask   # noqa: E402

UNET = "flux1-dev-kontext_fp8_scaled.safetensors"
GUIDANCE = 2.5
STEPS = 20
PROMPT = ("Edit the photo: the person is now wearing the same eyeglasses as the person in the "
          "reference photo — same frame shape, thickness and color. Change only the eye area. "
          "Keep the face, hair, costume, hat, pose, lighting and background exactly the same. "
          "Photorealistic.")
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
                raise RuntimeError(str(st.get("messages"))[:600])
            if h[pid].get("outputs"):
                im = list(h[pid]["outputs"].values())[0]["images"][0]
                url = "%s/view?filename=%s&subfolder=%s&type=output" % (
                    node, im["filename"], im["subfolder"])
                return urllib.request.urlopen(url, timeout=300).read()
        time.sleep(0.3)


def _graph(cname, sname, mname, tag, guidance=GUIDANCE, steps=STEPS, seed=1000):
    return {
        "unet": {"class_type": "UNETLoader", "inputs": {"unet_name": UNET, "weight_dtype": "default"}},
        "clip": {"class_type": "DualCLIPLoader", "inputs": {"clip_name1": "t5xxl_fp16.safetensors",
                 "clip_name2": "clip_l.safetensors", "type": "flux", "device": "default"}},
        "vae": {"class_type": "VAELoader", "inputs": {"vae_name": "ae.safetensors"}},
        "imgA": {"class_type": "LoadImage", "inputs": {"image": cname}},
        "imgB": {"class_type": "LoadImage", "inputs": {"image": sname}},
        "mask": {"class_type": "LoadImageMask", "inputs": {"image": mname, "channel": "red"}},
        # 참조: 코스튬 결과 + 원본 얼굴을 옆으로 붙여 하나의 참조 잠재로
        "stitch": {"class_type": "ImageStitch", "inputs": {"image1": ["imgA", 0], "image2": ["imgB", 0],
                   "direction": "right", "match_image_size": True, "spacing_width": 0, "spacing_color": "white"}},
        "scale": {"class_type": "FluxKontextImageScale", "inputs": {"image": ["stitch", 0]}},
        "encref": {"class_type": "VAEEncode", "inputs": {"pixels": ["scale", 0], "vae": ["vae", 0]}},
        # 작업 잠재: 코스튬 결과 자체 + 눈 마스크. 마스크 밖은 잠재 단계에서 고정된다
        "encA": {"class_type": "VAEEncode", "inputs": {"pixels": ["imgA", 0], "vae": ["vae", 0]}},
        "lat": {"class_type": "SetLatentNoiseMask", "inputs": {"samples": ["encA", 0], "mask": ["mask", 0]}},
        "pos": {"class_type": "CLIPTextEncode", "inputs": {"text": PROMPT, "clip": ["clip", 0]}},
        "neg": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["clip", 0]}},
        "ref": {"class_type": "ReferenceLatent", "inputs": {"conditioning": ["pos", 0], "latent": ["encref", 0]}},
        "guid": {"class_type": "FluxGuidance", "inputs": {"conditioning": ["ref", 0], "guidance": guidance}},
        "sampler": {"class_type": "KSampler", "inputs": {"model": ["unet", 0], "positive": ["guid", 0],
                    "negative": ["neg", 0], "latent_image": ["lat", 0], "seed": seed, "steps": steps,
                    "cfg": 1.0, "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0}},
        "dec": {"class_type": "VAEDecode", "inputs": {"samples": ["sampler", 0], "vae": ["vae", 0]}},
        "save": {"class_type": "SaveImage", "inputs": {"images": ["dec", 0], "filename_prefix": "glasses_kontext/" + tag}},
    }


def kontext_glasses(dst, dst_kps, src_path: pathlib.Path, src_kps, src_mask,
                    node: str = "http://127.0.0.1:8189", seed: int = 1000):
    """dst(최종 896x1152 코스튬 결과)의 눈 영역에 원본 안경을 Kontext 로 그린다."""
    mask = eye_region_mask(src_mask, src_kps, dst_kps, dst.shape)
    if mask is None:
        return dst
    tag = uuid.uuid4().hex[:10]
    cname, mname, sname = f"_gk_{tag}.png", f"_gk_{tag}_mask.png", f"_gk_{tag}_src.png"
    cv2.imwrite(str(COMFY_IN / cname), dst)
    cv2.imwrite(str(COMFY_IN / mname), mask)
    shutil.copy(src_path, COMFY_IN / sname)
    try:
        data = _submit(node, _graph(cname, sname, mname, tag, seed=seed))
        res = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        if res is None:
            return dst
        if res.shape[:2] != dst.shape[:2]:
            res = cv2.resize(res, (dst.shape[1], dst.shape[0]), interpolation=cv2.INTER_LANCZOS4)
        return res
    finally:
        for n in (cname, mname, sname):
            try:
                (COMFY_IN / n).unlink()
            except OSError:
                pass

def _crop_box(kps, shape, aspect=512 / 384, half_w_mul=2.3):
    """눈 두 점을 중심으로 이마·코·양쪽 볼이 들어가는 상자. 모델이 피부색·빛 방향을 볼 수 있게 넉넉히."""
    kps = np.asarray(kps, np.float32)
    le, re = kps[0], kps[1]
    d = float(np.linalg.norm(re - le))
    cx, cy = float((le[0] + re[0]) / 2), float((le[1] + re[1]) / 2) + 0.15 * d
    hw = half_w_mul * d; hh = hw / aspect
    H, W = shape[:2]
    x0, x1 = int(max(0, cx - hw)), int(min(W, cx + hw))
    y0, y1 = int(max(0, cy - hh)), int(min(H, cy + hh))
    return x0, y0, x1, y1


def kontext_glasses_crop(dst, dst_kps, src_path: pathlib.Path, src_kps, src_mask,
                         node: str = "http://127.0.0.1:8189", seed: int = 1000,
                         steps: int = STEPS, size=(512, 384)):
    """눈 주변만 작은 캔버스에서 다시 그리고 되붙인다. 전체 캔버스 방식(장당 ~20초)의 속도 개선판.

    모델이 보는 것은 잘라낸 영역(이마~볼)이고, 바뀌는 곳은 그 안의 눈 타원 마스크만이다.
    참조도 원본 사진의 같은 영역을 잘라 붙여 안경이 크게 보이게 한다.
    """
    mask = eye_region_mask(src_mask, src_kps, dst_kps, dst.shape)
    if mask is None:
        return dst
    x0, y0, x1, y1 = _crop_box(dst_kps, dst.shape)
    ys, xs = np.nonzero(mask > 0)
    if len(xs):   # 마스크가 상자 밖으로 나가면 상자를 키운다
        x0, x1 = min(x0, int(xs.min()) - 8), max(x1, int(xs.max()) + 8)
        y0, y1 = min(y0, int(ys.min()) - 8), max(y1, int(ys.max()) + 8)
        x0, y0 = max(0, x0), max(0, y0); x1, y1 = min(dst.shape[1], x1), min(dst.shape[0], y1)
    cw, ch = x1 - x0, y1 - y0
    cropA = cv2.resize(dst[y0:y1, x0:x1], size, interpolation=cv2.INTER_LANCZOS4)
    cropM = cv2.resize(mask[y0:y1, x0:x1], size, interpolation=cv2.INTER_LINEAR)
    src = cv2.imread(str(src_path))
    sx0, sy0, sx1, sy1 = _crop_box(src_kps, src.shape)
    cropS = cv2.resize(src[sy0:sy1, sx0:sx1], size, interpolation=cv2.INTER_LANCZOS4)
    tag = uuid.uuid4().hex[:10]
    cname, mname, sname = f"_gc_{tag}.png", f"_gc_{tag}_mask.png", f"_gc_{tag}_src.png"
    cv2.imwrite(str(COMFY_IN / cname), cropA); cv2.imwrite(str(COMFY_IN / mname), cropM); cv2.imwrite(str(COMFY_IN / sname), cropS)
    try:
        data = _submit(node, _graph(cname, sname, mname, tag, steps=steps, seed=seed))
        res = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        if res is None:
            return dst
        res = cv2.resize(res, (cw, ch), interpolation=cv2.INTER_LANCZOS4)
        m = (cv2.resize(mask[y0:y1, x0:x1], (cw, ch), interpolation=cv2.INTER_LINEAR).astype(np.float32) / 255.0)[..., None]
        out = dst.copy()
        out[y0:y1, x0:x1] = (res.astype(np.float32) * m + dst[y0:y1, x0:x1].astype(np.float32) * (1 - m)).astype(np.uint8)
        return out
    finally:
        for n in (cname, mname, sname):
            try:
                (COMFY_IN / n).unlink()
            except OSError:
                pass
