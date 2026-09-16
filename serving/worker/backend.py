"""백엔드 콜백 (전달서 5절). claim / progress / complete / fail.

HMAC (5.1):
  bodyHash  = hex(SHA256(rawBody))
  canonical = timestamp + "\\n" + nonce + "\\n" + "POST" + "\\n" + path + "\\n" + bodyHash
  X-AI-Signature = "v1=" + hex(HMAC_SHA256(secret, canonical))
  헤더: X-AI-Timestamp, X-AI-Nonce, X-AI-Signature, X-Trace-Id, (complete/fail) X-Idempotency-Key = callbackEventId

같은 콜백을 재전송할 때는 ID·Body 를 그대로 두고 Timestamp·Nonce·서명만 새로 만든다. JSON 은 한 번만 직렬화한다.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import time
import uuid

import requests

from serving.worker import config as C

log = logging.getLogger("worker.backend")
_TRACE_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
BASE_PATH = "/internal/v1/ai-generation-items"


class BackendError(Exception):
    """status 로 분기한다: 5xx/timeout(None) 은 재전송, 4xx 는 요청 문제."""

    def __init__(self, status: int | None, text: str = ""):
        super().__init__(f"backend {status}: {text[:200]}")
        self.status, self.text = status, text


def _trace(trace_id: str | None) -> str:
    t = (trace_id or uuid.uuid4().hex)
    return t if _TRACE_RE.match(t) else uuid.uuid4().hex


def _headers(path: str, body: bytes, trace_id: str | None, idem: str | None) -> dict:
    ts, nonce = str(int(time.time())), str(uuid.uuid4())
    body_hash = hashlib.sha256(body).hexdigest()
    canonical = f"{ts}\n{nonce}\nPOST\n{path}\n{body_hash}"
    sig = hmac.new(C.BACKEND_HMAC_SECRET.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256).hexdigest()
    h = {"Content-Type": "application/json", "X-AI-Timestamp": ts, "X-AI-Nonce": nonce,
         "X-AI-Signature": "v1=" + sig, "X-Trace-Id": _trace(trace_id)}
    if idem:
        h["X-Idempotency-Key"] = idem
    return h


def _post(path: str, payload: dict, *, trace_id: str | None, idem: str | None = None, resend: int | None = None) -> tuple[int, dict]:
    """(status, json|{}) . Timeout/5xx 는 같은 Body 로 재전송(서명 헤더만 갱신). 4xx 는 BackendError 로 즉시."""
    url = C.BACKEND_BASE_URL.rstrip("/") + path
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")   # 한 번만 직렬화
    tries = C.BACKEND_RESEND if resend is None else resend
    last: BackendError | None = None
    for i in range(tries):
        try:
            r = requests.post(url, data=body, headers=_headers(path, body, trace_id, idem), timeout=C.BACKEND_TIMEOUT)
        except requests.RequestException as e:
            last = BackendError(None, str(e))
        else:
            if 200 <= r.status_code < 300:
                try:
                    return r.status_code, (r.json() if r.content else {})
                except ValueError:
                    return r.status_code, {}
            last = BackendError(r.status_code, r.text)
            if 400 <= r.status_code < 500:
                raise last
        log.warning("backend %s 재전송 %d/%d: %s", path, i + 1, tries, last)
        time.sleep(min(30, 3 * (i + 1)))
    raise last if last else BackendError(None, "backend 호출 실패")


# ------------------------------------------------------------------- API ----
def claim(item_id: str, message_id: str, trace_id: str | None) -> dict:
    """{disposition: CLAIMED|BUSY|ALREADY_COMPLETED|ALREADY_FAILED|CANCELED, workerToken, leaseExpiresAt} (항상 200)."""
    _, data = _post(f"{BASE_PATH}/{item_id}/claim", {"messageId": message_id, "workerId": C.WORKER_ID}, trace_id=trace_id)
    if "disposition" not in data:
        raise BackendError(200, f"claim 응답에 disposition 없음: {str(data)[:120]}")
    return data


def progress(item_id: str, token: str, pct: int, stage: str, trace_id: str | None) -> None:
    """204. 실패해도 작업을 멈추지 않는다 (lease 만료는 complete/fail 응답에서 드러난다)."""
    try:
        _post(f"{BASE_PATH}/{item_id}/progress", {"workerToken": token, "progress": max(0, min(99, int(pct))), "stage": stage},
              trace_id=trace_id, resend=1)
    except Exception as e:                        # noqa: BLE001
        log.warning("progress 실패 %s: %s", item_id, e)


def complete(item_id: str, token: str, model_version: str, seed: int, outputs: list[dict], trace_id: str | None,
             callback_event_id: str | None = None) -> str:
    """outputs 는 DTO 필드만 (serving.common.outputs.to_callback). 돌려주는 값: callbackEventId (재전송 시 재사용)."""
    cid = callback_event_id or str(uuid.uuid4())
    _post(f"{BASE_PATH}/{item_id}/complete",
          {"callbackEventId": cid, "workerToken": token, "modelVersion": model_version, "seed": int(seed), "outputs": outputs},
          trace_id=trace_id, idem=cid)
    return cid


def fail(item_id: str, token: str, code: str, retryable: bool, attempt: int, trace_id: str | None,
         callback_event_id: str | None = None) -> str:
    """최종 실패 확정. attempt 는 마지막 실행 번호 (0·1·2)."""
    cid = callback_event_id or str(uuid.uuid4())
    _post(f"{BASE_PATH}/{item_id}/fail",
          {"callbackEventId": cid, "workerToken": token, "errorCode": code, "retryable": bool(retryable), "attempt": int(attempt)},
          trace_id=trace_id, idem=cid)
    return cid
