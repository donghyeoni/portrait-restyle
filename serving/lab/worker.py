"""화풍 실험실 워커. 실험 전용 큐(motion.ai.lab)를 소비한다. 운영 카드 큐와 메시지를 섞지 않는다.

흐름 (백엔드 계약: 팀 저장소 docs/contracts/style-lab-v1.md):
  1. 메시지 payload 의 presigned GET 으로 입력·참고 이미지를 받는다 (워커에 S3 자격이 없다)
  2. serving.lab.runner.run 으로 실행한다. 단계가 바뀔 때마다 /progress
  3. 결과를 webp 로 바꿔 payload 의 presigned PUT 으로 올린다
  4. /complete (실패면 /fail) — 같은 HMAC 서명(serving.worker.backend)

환경변수: RABBIT_URL, LAB_QUEUE(기본 motion.ai.lab), LAB_COMFY_NODE(기본 http://127.0.0.1:8191),
          LAB_CTX_ID(얼굴 분석·누끼 GPU 번호, 기본 -1=CPU), BACKEND_BASE_URL, BACKEND_HMAC_SECRET
"""
from __future__ import annotations

import json
import logging
import os
import pathlib
import shutil
import tempfile
import time
import uuid

import cv2
import numpy as np
import requests

from serving.lab.runner import LabError, run
from serving.worker import backend

log = logging.getLogger("lab.worker")
RABBIT_URL = os.environ.get("RABBIT_URL", "amqp://guest:guest@localhost:5672/%2F")
QUEUE = os.environ.get("LAB_QUEUE", "motion.ai.lab")
NODE = os.environ.get("LAB_COMFY_NODE", "http://127.0.0.1:8191")
CTX_ID = int(os.environ.get("LAB_CTX_ID", "-1"))
EVENT_TYPE = "STYLE_LAB_RUN_REQUESTED"
BASE_PATH = "/internal/v1/ai-generation-items/style-lab-runs"   # Nginx 가 이미 넘기는 접두사 아래
MAX_DOWNLOAD = 15 * 1024 * 1024
INPUT_FILES = {"input": "input.png", "styleRef": "style_ref.png", "template": "template.png"}


def _download(url: str, dest: pathlib.Path):
    with requests.get(url, timeout=(10, 60), stream=True) as r:
        if r.status_code != 200:
            raise LabError("INPUT_UNAVAILABLE", f"입력 이미지를 받지 못했습니다 (HTTP {r.status_code})")
        data = bytearray()
        for chunk in r.iter_content(65536):
            data += chunk
            if len(data) > MAX_DOWNLOAD:
                raise LabError("INPUT_TOO_LARGE", "입력 이미지가 너무 큽니다")
    img = cv2.imdecode(np.frombuffer(bytes(data), np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise LabError("IMAGE_INVALID", "입력 이미지를 읽지 못했습니다")
    cv2.imwrite(str(dest), img)


def _webp(path: pathlib.Path) -> bytes:
    img = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    ok, buf = cv2.imencode(".webp", img, [cv2.IMWRITE_WEBP_QUALITY, 92])
    if not ok:
        raise RuntimeError(f"webp 변환 실패: {path.name}")
    return buf.tobytes()


def _put(url: str, data: bytes):
    for i in range(3):
        try:
            r = requests.put(url, data=data, headers={"Content-Type": "image/webp"}, timeout=(10, 120))
            if 200 <= r.status_code < 300:
                return
            err = f"HTTP {r.status_code}: {r.text[:160]}"
        except requests.RequestException as e:
            err = str(e)
        log.warning("업로드 재시도 %d/3: %s", i + 1, err)
        time.sleep(3 * (i + 1))
    raise LabError("UPLOAD_FAILED", "결과 이미지를 올리지 못했습니다")


def _outputs(workdir: pathlib.Path, result: dict) -> list[tuple[str, pathlib.Path]]:
    """(slot, 파일) — slot 은 백엔드가 presign 해 둔 이름."""
    files = [("analysis", workdir / "analysis.png")]
    if result.get("reference"):
        files.append(("reference", workdir / result["reference"]))
    for i, s in enumerate(result.get("steps", []), start=1):
        s["slot"] = f"step-{i}"
        files.append((s["slot"], workdir / s.pop("file")))
    files += [("result", workdir / "result.png"), ("cutout", workdir / "cutout.png")]
    return files


def process(payload: dict, trace_id: str | None) -> dict:
    run_id = payload["runId"]
    targets = payload.get("outputs") or {}
    workdir = pathlib.Path(tempfile.mkdtemp(prefix=f"lab-{run_id}-"))
    try:
        def on_stage(name: str, label: str):
            try:                                       # 진행 표시는 실패해도 실행을 멈추지 않는다
                backend._post(f"{BASE_PATH}/{run_id}/progress", {"stage": name[:40], "label": label[:80]},
                              trace_id=trace_id, resend=1)
            except Exception as e:                     # noqa: BLE001
                log.warning("실험 %s progress 실패: %s", run_id, e)

        on_stage("download", "이미지 받기")
        for key, file in INPUT_FILES.items():
            src = (payload.get("inputs") or {}).get(key)
            if src and src.get("downloadUrl"):
                _download(src["downloadUrl"], workdir / file)
        if not (workdir / "input.png").exists():
            raise LabError("INPUT_MISSING", "입력 사진이 없습니다")
        result = run(payload, workdir, node=NODE, ctx_id=CTX_ID, on_stage=on_stage)
        on_stage("upload", "결과 올리기")
        uploaded = []
        for slot, path in _outputs(workdir, result):
            target = targets.get(slot)
            if not target or not path.exists():
                continue
            data = _webp(path)
            _put(target["uploadUrl"], data)
            uploaded.append({"slot": slot, "fileSize": len(data)})
        result["outputs"] = uploaded
        result.pop("reference", None)
        return result
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def handle(ch, method, body: bytes):
    tag = method.delivery_tag
    try:
        env = json.loads(body)
        if env.get("eventType") != EVENT_TYPE or env.get("schemaVersion") != 1:
            raise ValueError(f"처리 대상이 아닌 메시지: {env.get('eventType')}")
        payload, trace = env["payload"], env.get("traceId")
        run_id = int(payload["runId"])
    except (ValueError, KeyError, TypeError) as e:
        log.error("메시지 형식 오류 → quarantine: %s", e)
        ch.basic_nack(tag, requeue=False)
        return
    t0 = time.time()
    try:                                           # 실행 결과를 먼저 정하고, 콜백은 그 다음에 한 번만 보낸다
        path, body = f"{BASE_PATH}/{run_id}/complete", process(payload, trace)
        log.info("실험 %s 완료 %.1fs", run_id, time.time() - t0)
    except LabError as e:
        path, body = f"{BASE_PATH}/{run_id}/fail", {"errorCode": e.code, "message": e.message[:300]}
        log.warning("실험 %s 실패 %s: %s", run_id, e.code, e.message)
    except Exception as e:                         # noqa: BLE001
        log.exception("실험 %s 처리 중 예외", run_id)
        path, body = f"{BASE_PATH}/{run_id}/fail", {"errorCode": "WORKER_ERROR",
                                                     "message": f"{type(e).__name__}: {str(e)[:240]}"}
    cid = str(uuid.uuid4())
    try:
        backend._post(path, {"callbackEventId": cid, **body}, trace_id=trace, idem=cid)
    except backend.BackendError as e:
        if e.status and 400 <= e.status < 500:
            log.error("실험 %s 콜백 %s: %s — 버림", run_id, e.status, e.text[:160])   # 이미 끝났거나 없는 실험
            ch.basic_ack(tag)
            return
        log.error("실험 %s 콜백 재전송 소진(%s) — requeue", run_id, e)
        time.sleep(10)
        ch.basic_nack(tag, requeue=True)
        return
    ch.basic_ack(tag)


def run_forever():
    import pika
    while True:
        try:
            conn = pika.BlockingConnection(pika.URLParameters(RABBIT_URL))
            ch = conn.channel()
            ch.basic_qos(prefetch_count=1)
            ch.basic_consume(QUEUE, lambda c, m, p, b: handle(c, m, b))
            log.info("실험실 소비 시작 queue=%s node=%s ctx=%d", QUEUE, NODE, CTX_ID)
            ch.start_consuming()
        except (pika.exceptions.AMQPConnectionError, pika.exceptions.StreamLostError,
                pika.exceptions.ChannelClosedByBroker) as e:
            log.error("RabbitMQ 연결 끊김: %s — 10초 후 재연결", e)
            time.sleep(10)
