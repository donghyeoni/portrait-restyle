"""경량 HTTP: GET /collections (전달서 3절), GET /health. GPU 를 깨우지 않는다 — 매니페스트만 읽는다.

  Authorization: Bearer <AI_PRESET_CATALOG_TOKEN>   (토큰이 설정돼 있으면 필수)
  ETag = catalogVersion (strong). If-None-Match 일치 시 304.
  Host 네트워크에서 127.0.0.1:8090 에만 바인딩한다 (HTTP_BIND). 외부·GPU 서버에 공개하지 않는다.
"""
from __future__ import annotations

import pathlib
import sys
import threading

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from manifests import catalog_payload   # noqa: E402
from serving.worker import config as C  # noqa: E402

app = FastAPI(title="portrait-restyle worker", version="1")


def _authorized(req: Request) -> bool:
    if not C.CATALOG_TOKEN:
        return True
    return req.headers.get("Authorization", "") == f"Bearer {C.CATALOG_TOKEN}"


@app.get("/health")
def health():
    return {"status": "ok", "workerId": C.WORKER_ID, "consumerEnabled": C.CONSUMER_ENABLED}


@app.get("/collections")
def collections(req: Request):
    if not _authorized(req):
        return JSONResponse({"error": {"code": "UNAUTHORIZED", "message": "Bearer token required"}}, status_code=401)
    cat = catalog_payload()
    etag = f'"{cat["catalogVersion"]}"'
    if req.headers.get("If-None-Match") == etag:
        return Response(status_code=304, headers={"ETag": etag})
    return JSONResponse(cat, headers={"ETag": etag, "Cache-Control": "no-cache"})


def serve_in_thread(port: int = C.HTTP_PORT, host: str = C.HTTP_BIND) -> threading.Thread:
    import uvicorn
    t = threading.Thread(target=lambda: uvicorn.run(app, host=host, port=port, log_level="warning"), daemon=True)
    t.start()
    return t
