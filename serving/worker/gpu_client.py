"""GPU 모델 서버 호출 (전달서 6절, 업체 중립). POST {GPU_SERVICE_URL}/generate 동기.

  연결 10초 · 응답 대기 630초. 429·5xx·타임아웃·연결 실패는 재시도 대상, 4xx 는 입력 문제로 즉시 실패.
  총 실행 횟수는 호출자(consumer)가 관리한다 — 여기서는 한 번만 시도하고 ItemError 를 던진다.

인증 GPU_AUTH_MODE:
  none          로컬 시험
  bearer        GPU_AUTH_CREDENTIALS_FILE 의 토큰 문자열을 Authorization: Bearer 로
  gcp-id-token  GPU_AUTH_CREDENTIALS_FILE = external_account JSON (GOOGLE_APPLICATION_CREDENTIALS), audience = GPU_SERVICE_URL
업로드 GPU_UPLOAD_MODE:
  relay      uploadUrl 을 보내지 않고 GPU 가 outputs[].data(base64) 를 돌려준다 → EC2 가 SDK 로 native 체크섬 업로드 (기본)
  presigned  uploadUrl 을 보내 GPU 가 직접 PUT (S3 서명 체크섬 POC 뒤)
"""
from __future__ import annotations

import logging
import pathlib
import time

import requests

from serving.common.item import Item, ItemError
from serving.worker import config as C

log = logging.getLogger("worker.gpu")
_TOKEN: dict = {}


def _auth_header() -> dict:
    mode = C.GPU_AUTH_MODE
    if mode == "none":
        return {}
    if mode == "bearer":
        tok = pathlib.Path(C.GPU_AUTH_CREDENTIALS_FILE).read_text(encoding="utf-8").strip()
        return {"Authorization": f"Bearer {tok}"}
    if mode == "gcp-id-token":
        now = time.time()
        if _TOKEN.get("exp", 0) - 60 <= now:
            import os
            os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", C.GPU_AUTH_CREDENTIALS_FILE)
            import google.auth.transport.requests
            import google.oauth2.id_token
            _TOKEN.update(tok=google.oauth2.id_token.fetch_id_token(google.auth.transport.requests.Request(), C.GPU_SERVICE_URL),
                          exp=now + 50 * 60)
        return {"Authorization": f"Bearer {_TOKEN['tok']}"}
    raise ItemError("GPU_AUTH_CONFIG", f"알 수 없는 GPU_AUTH_MODE {mode!r}")


def request_body(item: Item, preprocess: dict | None, relay: bool) -> dict:
    """전달서 6절 요청 = Rabbit payload + downloadUrl (+ uploadUrl, 선택 preprocess)."""
    return {
        "itemId": item.item_id, "batchId": item.batch_id, "generationType": item.generation_type,
        "source": {"bucketType": item.source_bucket_type, "objectKey": item.source_key,
                   "checksumSha256": item.source_checksum, "downloadUrl": item.source_download_url},
        "subject": {"subjectUserId": item.user_id, "gender": item.gender.upper()},
        "generation": {"stylePreset": item.style_preset, "modelVersion": item.model_version,
                       "promptTemplateVersion": item.prompt_template_version},
        "outputTargets": [{"variant": t.variant, "bucketType": t.bucket_type, "objectKey": t.object_key,
                           **({} if relay else {"uploadUrl": t.upload_url})} for t in item.targets],
        "returnBytes": relay,
        "imageOnly": C.GPU_IMAGE_ONLY,
        "preprocess": preprocess,
    }


def generate_once(item: Item, preprocess: dict | None) -> dict:
    """한 번 호출. 성공 시 응답 dict, 실패는 ItemError(retryable 로 재시도 여부)."""
    if not C.GPU_SERVICE_URL:
        raise ItemError("GPU_UNAVAILABLE", "GPU_SERVICE_URL 미설정 — GPU 환경 미정", retryable=False)
    url = C.GPU_SERVICE_URL.rstrip("/") + "/generate"
    body = request_body(item, preprocess, relay=(C.GPU_UPLOAD_MODE == "relay"))
    try:
        r = requests.post(url, json=body, headers={"Content-Type": "application/json", **_auth_header()},
                          timeout=(C.GPU_CONNECT_TIMEOUT, C.GPU_READ_TIMEOUT))
    except requests.Timeout:
        raise ItemError("MODEL_TIMEOUT", f"GPU 응답 없음 ({C.GPU_READ_TIMEOUT:.0f}s)", retryable=True)
    except requests.RequestException as e:
        raise ItemError("MODEL_ERROR", f"GPU 연결 실패: {type(e).__name__}", retryable=True)
    if r.status_code == 200:
        return r.json()
    try:
        err = r.json().get("error", {})
    except Exception:
        err = {}
    code = err.get("code") or ("MODEL_BUSY" if r.status_code == 429 else "MODEL_ERROR")
    msg = err.get("message") or r.text[:200]
    if 400 <= r.status_code < 500 and r.status_code != 429:
        raise ItemError(code, msg, retryable=False)
    raise ItemError(code, msg, retryable=True)
