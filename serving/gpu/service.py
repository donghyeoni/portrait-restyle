"""GPU 모델 서버 (전달서 6절, 업체 중립 HTTP 계약). 요청 1건 = 그림 1장, 동기 응답.

  GET  /health     서버 생존 (가중치 로딩과 무관하게 200). {"status","comfyReady","modelsLoaded"}
  POST /generate   payload + downloadUrl/uploadUrl -> 생성 -> 누끼 -> 썸네일 -> S3 PUT(프리사인) -> outputs[]
  GET  /collections  참고용 (백엔드는 EC2 워커의 것을 쓴다 — 이 서비스를 깨우지 않기 위해)
  POST /cutout       multipart "file" -> 배경 제거 RGBA PNG (원본 크기). SSR(Gemini)·SR 카드 누끼용
  POST /card-cutout  multipart "file" -> 카드 규격 896x1152 RGBA (lossless webp, ?fmt=png). 원본 사진 N 카드 누끼용

  누끼도 /generate 와 같은 `busy` 락 안에서 돈다: SR 생성과 누끼(SR·원본 N·SSR)는 이 프로세스에서 한 번에 하나만 처리된다.

컨테이너 시작 시 ComfyUI 를 자식 프로세스로 띄운다. 가중치는 MODELS_DIR(읽기 전용 볼륨)에서 읽으며 첫 요청에 로드된다.
업로드: 요청에 uploadUrl 이 있으면 프리사인 PUT, `returnBytes: true` 면 outputs[].data(base64) 로 돌려준다(EC2 가 SDK 업로드).
세 엔진을 모두 갖는다: pulid(컨셉) · kontext(웹툰) · inswapper(안경 착용자의 직업·12지신 — Kontext 안경 단계가 필요해 EC2 가 넘김).

  MODELS_DIR=/models COMFY_DIR=/app/ComfyUI PORT=8080 python -m serving.gpu.service
  알파(개발 GPU 서버): START_COMFY=false GPU_SERVICE_TOKEN=... PORT=8080 → serving/gpu/run_tljh.sh + tunnel.sh (역터널)
"""
from __future__ import annotations

import base64
import logging
import os
import pathlib
import subprocess
import sys
import tempfile
import threading
import time

import cv2
import uvicorn
from fastapi import FastAPI, File, Query, Request, UploadFile
from fastapi.responses import JSONResponse, Response

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from manifests import MODEL_VERSION, catalog_payload                     # noqa: E402
from serving.common import outputs as O, preprocess as P, s3io           # noqa: E402
from serving.common.item import Item, ItemError, from_payload, resolve   # noqa: E402

log = logging.getLogger("gpu.service")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

MODELS_DIR = pathlib.Path(os.environ.get("MODELS_DIR", str(ROOT / "models")))
COMFY_DIR = pathlib.Path(os.environ.get("COMFY_DIR", str(ROOT / "ComfyUI")))
COMFY_PORT = int(os.environ.get("COMFY_PORT", "8189"))
NODE = f"http://127.0.0.1:{COMFY_PORT}"
START_COMFY = os.environ.get("START_COMFY", "true").lower() == "true"    # TLJH 서버 검증 때는 이미 떠 있는 것을 쓴다
COMFY_READY_TIMEOUT = float(os.environ.get("COMFY_READY_TIMEOUT", "240"))
SERVICE_TOKEN = os.environ.get("GPU_SERVICE_TOKEN", "")        # 설정돼 있으면 /generate 는 Authorization: Bearer 필수 (워커 GPU_AUTH_MODE=bearer)

app = FastAPI(title="portrait-restyle gpu", version=MODEL_VERSION)
_state = {"comfy": None, "models_loaded": False, "busy": threading.Lock()}


# ------------------------------------------------------------------ ComfyUI ----
def _write_extra_paths() -> pathlib.Path:
    cfg = COMFY_DIR / "extra_model_paths.yaml"
    cfg.write_text(f"""portrait_restyle:
    base_path: {MODELS_DIR}/
    is_default: true
    checkpoints: checkpoints
    loras: loras
    vae: vae
    unet: unet
    clip: clip
    upscale_models: upscale_models
    pulid: pulid
    insightface: insightface
""", encoding="utf-8")
    return cfg


def start_comfy():
    if not START_COMFY:
        return
    cfg = _write_extra_paths()
    log_path = pathlib.Path(tempfile.gettempdir()) / "comfy.log"
    _state["comfy"] = subprocess.Popen(
        [sys.executable, "main.py", "--port", str(COMFY_PORT), "--listen", "127.0.0.1",
         "--extra-model-paths-config", str(cfg), "--disable-auto-launch"],
        cwd=str(COMFY_DIR), stdout=open(log_path, "ab"), stderr=subprocess.STDOUT)
    log.info("ComfyUI 시작 pid=%s log=%s", _state["comfy"].pid, log_path)


def comfy_ready() -> bool:
    from engines.comfy import alive
    return alive(NODE)


def wait_comfy(timeout: float = COMFY_READY_TIMEOUT):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if comfy_ready():
            return
        if _state["comfy"] is not None and _state["comfy"].poll() is not None:
            raise ItemError("MODEL_ERROR", "ComfyUI 프로세스가 종료됨", retryable=True, http_status=503)
        time.sleep(2)
    raise ItemError("MODEL_TIMEOUT", "ComfyUI 기동 대기 초과", retryable=True, http_status=503)


@app.on_event("startup")
def _startup():
    start_comfy()


# ---------------------------------------------------------------- 엔진 호출 ----
def run_pulid(item: Item, coll, preset: dict, src_path: pathlib.Path, info: dict):
    from engines.comfy import decode, stage, submit, unstage
    from engines.pulid import build_graph
    name = stage(src_path, "_svc")
    try:
        g = build_graph(name, style=preset["key"], gender=item.gender, seed=coll.get("pulid", {}).get("seed", 1000),
                        glasses=bool(info.get("glasses")), filename_prefix=f"svc/{item.item_id}")
        return decode(submit(NODE, g))
    finally:
        unstage(name)


def run_kontext(item: Item, coll, preset: dict, src_path: pathlib.Path, info: dict):
    from engines.kontext import edit
    from steps.upscale import upscale
    kx, lora, out = coll.get("kontext", {}), coll.get("lora") or {}, coll["output"]
    if "prompt" in preset:
        text = preset["prompt"]
    else:
        text = coll["prompt"][item.gender].format(scene=preset["scene"], outfit=preset[f"outfit_{item.gender}"])
    img = edit(src_path, text, width=out["width"], height=out["height"], guidance=kx.get("guidance", 2.5),
               steps=kx.get("steps", 20), seed=kx.get("seed", 1000), lora=lora.get("name"),
               lora_strength=lora.get("strength", 1.0), trigger=lora.get("trigger"), node=NODE, tag=f"svc_{item.item_id}")
    if out.get("upscale"):
        img = upscale(img, float(out["upscale"]))
    return img


def run_inswapper(item: Item, coll, preset: dict, src_path: pathlib.Path, info: dict):
    """안경 착용자의 코스튬. 얼굴 교체 + Kontext 안경 (매니페스트 glasses 모드)."""
    from engines.inswapper import prepare_source, swap_into
    from steps.glasses import glasses_mask
    img = cv2.imread(str(src_path))
    fa, source_face = prepare_source(img, 0)
    if source_face is None:
        raise ItemError("NO_FACE", "원본에서 얼굴을 찾지 못함 (buffalo_l)")
    ref_dir = coll.reference_dir() / item.gender
    ref = next((p for p in ref_dir.iterdir() if p.stem == preset["key"]), None)
    if ref is None:
        raise ItemError("MODEL_ERROR", f"참고 이미지 없음: {ref_dir}/{preset['key']}", retryable=False)
    mode = coll.get("glasses", "off") if info.get("glasses") else "off"
    gl_mask = glasses_mask(img, source_face) if mode != "off" else None
    final, note = swap_into(ref, img, src_path, source_face, fa, gl_mask, glasses_mode=mode, node=NODE, gender=item.gender)
    if final is None:
        raise ItemError("MODEL_ERROR", f"참고 이미지 얼굴 검출 실패: {note}", retryable=False)
    return final, ref                              # ref: 사전 누끼 마스크 조회용 (steps/refmask.py)


RUN = {"pulid": run_pulid, "kontext": run_kontext, "inswapper": run_inswapper}


def generate(item: Item, body_flags: dict | None = None) -> dict:
    body_flags = body_flags or {}
    t0 = time.time()
    coll, preset = resolve(item)
    if not item.source_download_url:
        raise ItemError("INVALID_MESSAGE", "source.downloadUrl 없음")
    if not body_flags.get("returnBytes") and any(not t.upload_url for t in item.targets):
        raise ItemError("INVALID_MESSAGE", "uploadUrl 이 없으면 returnBytes: true 로 요청해야 함")

    try:
        src_bytes = s3io.download(item.source_download_url)
    except Exception as e:
        raise ItemError("SOURCE_DOWNLOAD", str(e), retryable=True, http_status=502) from e
    if item.source_checksum and O.sha256_hex(src_bytes) != item.source_checksum.lower():
        raise ItemError("SOURCE_CHECKSUM", "원본 체크섬 불일치")
    img = P.decode_image(src_bytes)
    info = item.preprocess_hint if (item.preprocess_hint and "glasses" in item.preprocess_hint) else P.analyze(img, ctx_id=0)

    wait_comfy()
    t_model = time.time()
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        src_path = pathlib.Path(f.name)
    cv2.imwrite(str(src_path), img)
    try:
        with _state["busy"]:                       # 동시성 1 (Cloud Run 설정과 이중 안전장치)
            final = RUN[coll.engine](item, coll, preset, src_path, info)
            ref = None
            if isinstance(final, tuple):              # inswapper: (이미지, 참고 이미지 경로)
                final, ref = final
    except ItemError:
        raise
    except Exception as e:                         # noqa: BLE001
        log.exception("생성 실패 %s", item.item_id)
        raise ItemError("MODEL_ERROR", f"{type(e).__name__}: {str(e)[:200]}", retryable=True, http_status=500) from e
    finally:
        src_path.unlink(missing_ok=True)
    model_ms = int((time.time() - t_model) * 1000)
    _state["models_loaded"] = True

    if body_flags.get("imageOnly"):
        # GPU 는 그림만 돌려준다. 누끼·인코딩·업로드는 워커(CPU)가 한다 (단일 GPU 를 그림 전용으로)
        _ok, _png = cv2.imencode(".png", final)
        _data = _png.tobytes(); _h, _w = final.shape[:2]
        return {"itemId": item.item_id, "modelVersion": MODEL_VERSION, "promptTemplateVersion": coll.version,
                "seed": int(coll.get("pulid", coll.get("kontext", {})).get("seed", 1000)) if coll.engine != "inswapper" else 0,
                "image": {"data": base64.b64encode(_data).decode(), "width": int(_w), "height": int(_h),
                          "checksumSha256": O.sha256_hex(_data), "engine": coll.engine, "hasMask": ref is not None},
                "metrics": {"elapsedMs": int((time.time() - t0) * 1000), "modelLoadMs": model_ms, "engine": coll.engine}}

    from steps.cutout import cutout
    need_cut = any(v in item.wanted for v in ("CUTOUT", "SUBJECT_MASK"))
    cut = cutout
    if ref is not None:                                # 고정 참고 이미지: 사전 마스크, 없으면 BiRefNet
        from steps import refmask
        from steps.crop import OVERRIDE
        cut = refmask.cutout_fn(ref, OVERRIDE.get(ref.stem, {}).get("above", 1.0), cutout)
    made = O.build(final, item.wanted, cut if need_cut else None)
    relay = bool(body_flags.get("returnBytes")) or not all(t.upload_url for t in item.targets)
    descs = []
    for t in item.targets:
        data, w, h = made[t.variant]
        d = O.describe(t.variant, data, w, h, t.object_key, t.bucket_type)
        if relay:
            d["data"] = base64.b64encode(data).decode()       # EC2 워커가 SDK 로 native 체크섬 업로드 (GPU_UPLOAD_MODE=relay)
        else:
            try:
                s3io.upload(t.upload_url, data, t.content_type)
            except Exception as e:
                raise ItemError("UPLOAD_FAILED", f"{t.variant}: {e}", retryable=True, http_status=502) from e
        descs.append(d)
    seed = int(coll.get("pulid", coll.get("kontext", {})).get("seed", 1000)) if coll.engine != "inswapper" else 0
    return {"itemId": item.item_id, "modelVersion": MODEL_VERSION, "promptTemplateVersion": coll.version, "seed": seed,
            "outputs": descs, "metrics": {"elapsedMs": int((time.time() - t0) * 1000), "modelLoadMs": model_ms, "engine": coll.engine}}


# -------------------------------------------------------------------- HTTP ----
@app.get("/health")
def health():
    return {"status": "ok", "comfyReady": comfy_ready(), "modelsLoaded": _state["models_loaded"], "modelVersion": MODEL_VERSION,
            "cutout": {"model": "birefnet-portrait", "providers": os.environ.get("CUTOUT_PROVIDERS", "cuda,cpu"),
                       "cudaDevice": os.environ.get("CUTOUT_CUDA_DEVICE", "") or "0", "busy": _state["busy"].locked()}}


@app.get("/collections")
def collections():
    return catalog_payload()


@app.post("/generate")
async def generate_endpoint(req: Request):
    if SERVICE_TOKEN and req.headers.get("Authorization", "") != f"Bearer {SERVICE_TOKEN}":
        return JSONResponse({"error": {"code": "UNAUTHORIZED", "message": "Bearer token required", "retryable": False}}, status_code=401)
    try:
        body = await req.json()
        item = from_payload(body)
        import asyncio
        return await asyncio.to_thread(generate, item, {"returnBytes": body.get("returnBytes", False), "imageOnly": body.get("imageOnly", False)})
    except ItemError as e:
        return JSONResponse(e.to_dict(), status_code=e.http_status)
    except Exception as e:                         # noqa: BLE001
        log.exception("generate 예외")
        return JSONResponse(ItemError("MODEL_ERROR", str(e)[:200], retryable=True, http_status=500).to_dict(), status_code=500)


# ------------------------------------------------------------------ 누끼 ----
CUTOUT_MAX_BYTES = int(os.environ.get("CUTOUT_MAX_BYTES", str(25 * 1024 * 1024)))
CUTOUT_MAX_SIDE = int(os.environ.get("CUTOUT_MAX_SIDE", "2048"))     # 모델 입력은 1024 고정. 큰 사진은 줄여서 후처리 시간만 아낀다


def _authorized(req: Request) -> bool:
    return not SERVICE_TOKEN or req.headers.get("Authorization", "") == f"Bearer {SERVICE_TOKEN}"


def _decode_upload(data: bytes):
    if not data:
        raise ItemError("INVALID_MESSAGE", "empty file", http_status=400)
    if len(data) > CUTOUT_MAX_BYTES:
        raise ItemError("INVALID_MESSAGE", "file too large", http_status=413)
    img = P.decode_image(data)
    if img is None:
        raise ItemError("INVALID_MESSAGE", "cannot decode image", http_status=422)
    h, w = img.shape[:2]
    if max(h, w) > CUTOUT_MAX_SIDE:
        k = CUTOUT_MAX_SIDE / max(h, w)
        img = cv2.resize(img, (max(1, round(w * k)), max(1, round(h * k))), interpolation=cv2.INTER_AREA)
    return img


def _cutout_png(data: bytes) -> bytes:
    from steps.cutout import cutout
    img = _decode_upload(data)
    with _state["busy"]:                          # 생성과 같은 줄에서 직렬 처리
        bgra = cutout(img)
    ok, png = cv2.imencode(".png", bgra)
    return png.tobytes()


def _card_cutout(data: bytes, fmt: str, width: int, height: int) -> tuple[bytes, str]:
    from steps.cardcrop import card_cutout_bgra, encode
    img = _decode_upload(data)
    with _state["busy"]:
        bgra = card_cutout_bgra(img, width, height)
    return encode(bgra, fmt)


@app.post("/cutout")
async def cutout_endpoint(req: Request, file: UploadFile = File(...)):
    if not _authorized(req):
        return JSONResponse({"error": {"code": "UNAUTHORIZED", "message": "Bearer token required", "retryable": False}}, status_code=401)
    t0 = time.time()
    try:
        import asyncio
        png = await asyncio.to_thread(_cutout_png, await file.read())
    except ItemError as e:
        return JSONResponse(e.to_dict(), status_code=e.http_status)
    except Exception as e:                         # noqa: BLE001
        log.exception("cutout 예외")
        return JSONResponse(ItemError("MODEL_ERROR", str(e)[:200], retryable=True, http_status=500).to_dict(), status_code=500)
    return Response(content=png, media_type="image/png", headers={"X-Process-Seconds": f"{time.time() - t0:.2f}"})


@app.post("/card-cutout")
async def card_cutout_endpoint(req: Request, file: UploadFile = File(...), fmt: str = Query("webp", pattern="^(webp|png)$"),
                               width: int = Query(896, ge=64, le=4096), height: int = Query(1152, ge=64, le=4096)):
    if not _authorized(req):
        return JSONResponse({"error": {"code": "UNAUTHORIZED", "message": "Bearer token required", "retryable": False}}, status_code=401)
    t0 = time.time()
    try:
        import asyncio
        body, media = await asyncio.to_thread(_card_cutout, await file.read(), fmt, width, height)
    except ItemError as e:
        return JSONResponse(e.to_dict(), status_code=e.http_status)
    except Exception as e:                         # noqa: BLE001
        log.exception("card-cutout 예외")
        return JSONResponse(ItemError("MODEL_ERROR", str(e)[:200], retryable=True, http_status=500).to_dict(), status_code=500)
    return Response(content=body, media_type=media, headers={"X-Process-Seconds": f"{time.time() - t0:.2f}"})


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8080")), workers=1)
