"""HTTP API for background removal.

POST /cutout       multipart field "file" (png/jpg/webp)  -> image/png with alpha (source size)
POST /card-cutout  multipart field "file"                 -> RGBA card image 896x1152 (lossless webp by default)
GET  /health       {"status": "ok", "model": "birefnet-portrait"}
"""
import time

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response

from .card import CARD_H, CARD_W, card_cutout_bytes
from .engine import MODEL, MODEL_FILE, MemoryLow, available_mb, cutout_bytes, load_session

app = FastAPI(title="Motion Cutout", version="1.3")
MAX_BYTES = 25 * 1024 * 1024


@app.on_event("startup")
def _warm_up():
    load_session()


@app.get("/health")
def health():
    return {"status": "ok", "model": MODEL, "file": MODEL_FILE, "host_available_mb": available_mb()}


@app.post("/cutout")
async def cutout_endpoint(file: UploadFile = File(...)):
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty file")
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "file too large (max 25MB)")
    t0 = time.time()
    try:
        png = await run_in_threadpool(cutout_bytes, data)
    except MemoryLow as e:
        raise HTTPException(503, str(e), headers={"Retry-After": "60"})
    except Exception as e:  # bad image data etc.
        raise HTTPException(422, f"cannot process image: {e}")
    headers = {"X-Process-Seconds": f"{time.time() - t0:.2f}"}
    return Response(content=png, media_type="image/png", headers=headers)


@app.post("/card-cutout")
async def card_cutout_endpoint(
    file: UploadFile = File(...),
    fmt: str = Query("webp", pattern="^(webp|png)$"),
    width: int = Query(CARD_W, ge=64, le=4096),
    height: int = Query(CARD_H, ge=64, le=4096),
):
    """Background removed + subject-centred crop to the card aspect, resized to width x height."""
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty file")
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "file too large (max 25MB)")
    t0 = time.time()
    try:
        body, media_type = await run_in_threadpool(card_cutout_bytes, data, fmt, width, height)
    except MemoryLow as e:
        raise HTTPException(503, str(e), headers={"Retry-After": "60"})
    except Exception as e:
        raise HTTPException(422, f"cannot process image: {e}")
    return Response(content=body, media_type=media_type, headers={"X-Process-Seconds": f"{time.time() - t0:.2f}"})
