"""RabbitMQ 소비 루프 (전달서 4·5절).

  수신 → 파싱·검증 → /claim(disposition) → 처리(하트비트 병행, 총 3회 실행) → /complete | 최종 /fail → ACK
  · 콜백이 2xx 를 돌려주기 전에는 ACK 하지 않는다. Timeout/5xx 는 같은 콜백 ID·Body 로 재전송.
  · claim BUSY → deferCount+1 로 retry exchange(motion.retry.30s) 에 재발행(confirm) 후 원본 ACK.
  · claim 종결(ALREADY_COMPLETED/FAILED/CANCELED) → 작업 없이 ACK.
  · 형식 오류(JSON·schemaVersion·필수 필드) → reject(requeue=False) → quarantine.
  · 형식은 맞지만 처리 대상이 아닌 종류(공유 큐의 다른 이벤트, 담당하지 않는 코드) → 소비 중단 (임의 ACK·quarantine·무한 requeue 금지).
"""
from __future__ import annotations

import base64
import json
import logging
import pathlib
import sys
import tempfile
import threading
import time

import pika

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from manifests import MODEL_VERSION                                          # noqa: E402
from serving.common import outputs as O, preprocess as P, s3io                # noqa: E402
from serving.common.item import Item, ItemError, from_envelope, resolve        # noqa: E402
from serving.worker import backend, config as C, cpu_path, gpu_client          # noqa: E402

log = logging.getLogger("worker.consumer")
STAGES = ("DOWNLOADING", "PREPROCESSING", "GENERATING", "UPLOADING")


class StopConsuming(Exception):
    """처리 대상이 아닌 메시지를 만났다. 소비를 멈추고 라우팅을 확인해야 한다."""


class Heartbeat(threading.Thread):
    """HEARTBEAT_SEC 마다 /progress. GPU 호출·재시도 대기와 독립적으로 돈다."""

    def __init__(self, item: Item, token: str):
        super().__init__(daemon=True)
        self.item, self.token = item, token
        self.stage, self.pct = "DOWNLOADING", 0
        self._stop = threading.Event()

    def set(self, stage: str, pct: int, push: bool = False):
        self.stage, self.pct = stage, pct
        if push:
            backend.progress(self.item.item_id, self.token, pct, stage, self.item.trace_id)

    def run(self):
        while not self._stop.wait(C.HEARTBEAT_SEC):
            backend.progress(self.item.item_id, self.token, self.pct, self.stage, self.item.trace_id)

    def stop(self):
        self._stop.set()


# ------------------------------------------------------------- 전처리 캐시 ----
_PRE: dict[str, dict] = {}       # (source_key, checksum) -> info. 권한·정책 확정 전에는 메모리만 (PREPROCESS_CACHE_S3=false)
_PRE_MAX = 200


def preprocess_info(item: Item, img, s3: s3io.S3) -> dict:
    key = f"{item.source_key}|{item.source_checksum or ''}"
    if key in _PRE:
        return _PRE[key]
    info = None
    cache_key = item.source_key.rsplit("/", 1)[0] + "/preprocess.json"
    if C.PREPROCESS_CACHE_S3:
        info = P.from_json(s3.get(item.source_bucket_type, cache_key))
    if info is None:
        info = P.analyze(img)
        if C.PREPROCESS_CACHE_S3:
            try:
                s3.put(item.source_bucket_type, cache_key, P.to_json(info), "application/json")
            except Exception as e:                   # noqa: BLE001
                log.warning("전처리 캐시 저장 실패: %s", e)
    if len(_PRE) >= _PRE_MAX:
        _PRE.pop(next(iter(_PRE)))
    _PRE[key] = info
    return info


# -------------------------------------------------------------- 결과 확인 ----
def _all_present(s3: s3io.S3, item: Item) -> list[dict] | None:
    """모든 Target 이 이미 S3 에 있고 native 체크섬이 있으면 메타데이터를 만들어 돌려준다 (멱등). 하나라도 없으면 None."""
    descs = []
    for t in item.targets:
        h = s3.head(t.bucket_type, t.object_key)
        if not h or not h.get("ChecksumSHA256"):
            return None
        hex_ = base64.b64decode(h["ChecksumSHA256"]).hex()
        descs.append({"variant": t.variant, "bucketType": t.bucket_type, "objectKey": t.object_key,
                      "contentType": h.get("ContentType", t.content_type), "fileSize": int(h["ContentLength"]), "checksumSha256": hex_})
    return descs


def _upload_all(s3: s3io.S3, item: Item, made: dict[str, tuple[bytes, int, int]]) -> list[dict]:
    descs = []
    for t in item.targets:
        data, w, h = made[t.variant]
        try:
            s3.put(t.bucket_type, t.object_key, data, t.content_type, O.sha256_b64(data))
        except Exception as e:                       # noqa: BLE001
            raise ItemError("UPLOAD_FAILED", f"S3 업로드 실패 {t.variant}: {type(e).__name__}", retryable=True) from e
        descs.append(O.describe(t.variant, data, w, h, t.object_key, t.bucket_type))
    return descs


def _gpu_cutout_fn(item: Item, coll, preset):
    """GPU 아이템 누끼(CPU). 컨셉·웹툰은 저해상도 누끼+마스크 확대, 안경 코스튬(inswapper)은 참고 이미지 사전 마스크."""
    if coll.engine == "inswapper":
        from steps import refmask
        from steps.crop import OVERRIDE
        ref_dir = coll.reference_dir() / item.gender
        ref = next((p for p in ref_dir.iterdir() if p.stem == preset["key"]), None)
        if ref is not None:
            return refmask.cutout_fn(ref, OVERRIDE.get(ref.stem, {}).get("above", 1.0), cpu_path.cutout_downscaled)
    return cpu_path.cutout_downscaled


# ------------------------------------------------------------- 한 번 실행 ----
def execute_once(item: Item, coll, preset, s3: s3io.S3, hb: Heartbeat, img, src_bytes: bytes) -> tuple[list[dict], int]:
    """한 번의 실행 (총 3회 중 1회). (outputs, seed). 실패는 ItemError."""
    info = preprocess_info(item, img, s3)
    hb.set("PREPROCESSING", 20)
    engine_seed = int(coll.get("pulid", coll.get("kontext", {})).get("seed", 1000)) if coll.engine not in ("inswapper", "original") else 0
    to_gpu = coll.engine in ("pulid", "kontext") or (bool(info.get("glasses")) and C.GLASSES_TO_GPU)

    hb.set("GENERATING", 30, push=True)
    if to_gpu:
        if not C.GPU_SERVICE_URL:
            # 안경 착용자 코스튬 포함: GPU 없이는 정상 처리로 표시하지 않는다 (전달서 2절)
            raise ItemError("GPU_UNAVAILABLE", "GPU 모델 서버 미설정", retryable=False)
        item.source_download_url = s3.presign_get(item.source_bucket_type, item.source_key)
        if C.GPU_UPLOAD_MODE == "presigned":
            for t in item.targets:
                t.upload_url = s3.presign_put(t.bucket_type, t.object_key, t.content_type)
        resp = gpu_client.generate_once(item, info)
        if resp.get("modelVersion") != item.model_version:
            raise ItemError("VERSION_MISMATCH", f"GPU modelVersion {resp.get('modelVersion')!r}", retryable=False)
        if "image" in resp:
            # GPU 서비스는 그림만 준다. 누끼·인코딩·업로드는 여기(CPU) — GPU 를 그림 전용으로 (단일 GPU 처리량↑)
            imgd = resp["image"]
            raw = base64.b64decode(imgd["data"])
            if O.sha256_hex(raw) != imgd.get("checksumSha256"):
                raise ItemError("MODEL_ERROR", "GPU 그림 바이트 체크섬 불일치", retryable=True)
            import cv2 as _cv2
            import numpy as _np
            final = _cv2.imdecode(_np.frombuffer(raw, _np.uint8), _cv2.IMREAD_COLOR)
            if final is None:
                raise ItemError("MODEL_ERROR", "GPU 그림 디코드 실패", retryable=True)
            hb.set("UPLOADING", 85, push=True)
            need_cut = any(v in item.wanted for v in ("CUTOUT", "SUBJECT_MASK"))
            made = O.build(final, item.wanted, _gpu_cutout_fn(item, coll, preset) if need_cut else None)
            return _upload_all(s3, item, made), int(resp.get("seed", engine_seed))
        # 구버전 GPU 서비스 호환: 완성 outputs 를 그대로 (relay/presigned)
        outs = resp.get("outputs") or []
        if {o.get("variant") for o in outs} != set(item.wanted):
            raise ItemError("MODEL_ERROR", f"GPU 응답 outputs 가 outputTargets 와 다름: {[o.get('variant') for o in outs]}", retryable=True)
        hb.set("UPLOADING", 85, push=True)
        if C.GPU_UPLOAD_MODE == "relay":
            made = {}
            for o in outs:
                data = base64.b64decode(o["data"])
                if O.sha256_hex(data) != o["checksumSha256"]:
                    raise ItemError("MODEL_ERROR", f"GPU 결과 바이트 체크섬 불일치 {o['variant']}", retryable=True)
                made[o["variant"]] = (data, o.get("width", 0), o.get("height", 0))
            return _upload_all(s3, item, made), int(resp.get("seed", engine_seed))
        for o in outs:
            t = item.target(o["variant"])
            if t is None or o.get("objectKey") != t.object_key:
                raise ItemError("MODEL_ERROR", f"GPU 응답 objectKey 불일치 {o.get('variant')}", retryable=True)
            o["bucketType"] = t.bucket_type
        return outs, int(resp.get("seed", engine_seed))

    # EC2 CPU 경로
    if coll.engine == "original":
        # 원본 사진 카드(N): 생성 없이 카드 규격 크롭 + 누끼(사전 마스크 없음 → BiRefNet 무조건).
        final = cpu_path.original(item, coll, preset, img)
        hb.set("UPLOADING", 80, push=True)
        need_cut = any(v in item.wanted for v in ("CUTOUT", "SUBJECT_MASK"))
        made = O.build(final, item.wanted, cpu_path.cutout_via_service if need_cut else None)
        return _upload_all(s3, item, made), engine_seed

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        src_path = pathlib.Path(f.name)
    import cv2
    cv2.imwrite(str(src_path), img)
    try:
        final, ref = cpu_path.swap(item, coll, preset, img, src_path)
    finally:
        src_path.unlink(missing_ok=True)
    hb.set("UPLOADING", 80, push=True)
    need_cut = any(v in item.wanted for v in ("CUTOUT", "SUBJECT_MASK"))
    # 누끼: 참고 이미지의 사전 마스크(0.01초). 없거나 불일치면 누끼 서비스(BiRefNet CPU 14초)로 안전망
    from steps import refmask
    from steps.crop import OVERRIDE
    cut = refmask.cutout_fn(ref, OVERRIDE.get(ref.stem, {}).get("above", 1.0), cpu_path.cutout_via_service)
    made = O.build(final, item.wanted, cut if need_cut else None)
    return _upload_all(s3, item, made), engine_seed


def process(item: Item, s3: s3io.S3, hb: Heartbeat) -> tuple[list[dict], int]:
    """총 MAX_EXECUTIONS 회. 재시도 가능한 오류만 다시 실행하고, 소진하면 마지막 오류에 attempt 를 붙여 던진다."""
    coll, preset = resolve(item)
    done = _all_present(s3, item)
    if done:
        log.info("%s 모든 Target 이 이미 존재 — 재생성 없이 complete", item.item_id)
        return done, int(coll.get("pulid", coll.get("kontext", {})).get("seed", 1000)) if coll.engine != "inswapper" else 0

    hb.set("DOWNLOADING", 5, push=True)
    src_bytes = s3.get(item.source_bucket_type, item.source_key)
    if src_bytes is None:
        raise ItemError("SOURCE_DOWNLOAD", f"원본 없음: {item.source_key}", retryable=False)
    if item.source_checksum and O.sha256_hex(src_bytes) != item.source_checksum.lower():
        raise ItemError("SOURCE_CHECKSUM", "원본 체크섬 불일치")
    img = P.decode_image(src_bytes)

    last: ItemError | None = None
    for attempt in range(C.MAX_EXECUTIONS):               # 0, 1, 2
        try:
            return execute_once(item, coll, preset, s3, hb, img, src_bytes)
        except ItemError as e:
            e.attempt = attempt
            last = e
            if not e.retryable:
                raise
            log.warning("%s 실행 %d/%d 실패 %s: %s", item.item_id, attempt, C.MAX_EXECUTIONS - 1, e.code, e.message)
            if attempt < C.MAX_EXECUTIONS - 1:
                time.sleep(5 * (3 ** attempt))            # 5s, 15s (하트비트는 계속 돈다)
    assert last is not None
    last.retryable = False                                # 소진 → 최종 실패
    raise last


# ---------------------------------------------------------------- 메시지 ----
def _republish_deferred(ch, body: bytes, env: dict):
    """claim BUSY: deferCount+1 로 retry exchange 에 발행(confirm). 실행 번호(attempt)는 그대로."""
    env = dict(env); env["deferCount"] = int(env.get("deferCount", 0)) + 1
    ch.basic_publish(C.RABBIT_RETRY_EXCHANGE, C.RABBIT_ROUTING_KEY, json.dumps(env, ensure_ascii=False).encode("utf-8"),
                     properties=pika.BasicProperties(content_type="application/json", delivery_mode=2), mandatory=True)


def handle(ch, method, props, body: bytes, s3: s3io.S3):
    tag = method.delivery_tag
    try:
        env = json.loads(body)
        item = from_envelope(env)
    except ItemError as e:
        if e.code == "UNSUPPORTED_EVENT":
            log.error("처리 대상이 아닌 메시지(%s) — 소비 중단, 라우팅 확인 필요", e.message)
            ch.basic_nack(tag, requeue=True)
            raise StopConsuming(e.message)
        log.error("메시지 형식 오류 → quarantine: %s", e)
        ch.basic_nack(tag, requeue=False)
        return
    except ValueError as e:
        log.error("JSON 오류 → quarantine: %s", e)
        ch.basic_nack(tag, requeue=False)
        return

    try:
        resolve(item)
    except ItemError as e:
        if e.code == "UNSUPPORTED_STYLE":
            log.error("%s 담당 코드 아님 (%s) — 소비 중단, 라우팅 확인 필요", item.item_id, item.style_preset)
            ch.basic_nack(tag, requeue=True)
            raise StopConsuming(e.message)
        # VERSION_MISMATCH 등은 claim 뒤 /fail 로 확정한다

    # claim
    try:
        cl = backend.claim(item.item_id, item.message_id or "", item.trace_id)
    except backend.BackendError as e:
        if e.status and 400 <= e.status < 500:
            log.error("%s claim %s — 요청 문제, quarantine: %s", item.item_id, e.status, e.text[:120])
            ch.basic_nack(tag, requeue=False)
            return
        log.error("%s claim 불가(%s) — requeue", item.item_id, e)
        time.sleep(5)
        ch.basic_nack(tag, requeue=True)
        return
    disp = cl.get("disposition")
    if disp == "BUSY":
        _republish_deferred(ch, body, env)
        ch.basic_ack(tag)
        log.info("%s BUSY → retry exchange 재발행 (deferCount %d)", item.item_id, item.defer_count + 1)
        return
    if disp in ("ALREADY_COMPLETED", "ALREADY_FAILED", "CANCELED"):
        ch.basic_ack(tag)
        log.info("%s %s → 작업 없이 ACK", item.item_id, disp)
        return
    if disp != "CLAIMED" or not cl.get("workerToken"):
        log.error("%s 알 수 없는 claim 응답 %s — requeue", item.item_id, cl)
        time.sleep(5)
        ch.basic_nack(tag, requeue=True)
        return
    token = cl["workerToken"]

    hb = Heartbeat(item, token)
    hb.start()
    t0 = time.time()
    result: dict = {}

    def work():
        try:
            result["ok"] = process(item, s3, hb)
        except ItemError as e:
            result["err"] = e
        except Exception as e:                       # noqa: BLE001
            log.exception("%s 처리 중 예외", item.item_id)
            err = ItemError("WORKER_ERROR", f"{type(e).__name__}", retryable=False)
            result["err"] = err

    th = threading.Thread(target=work, daemon=True)
    th.start()
    th.join()
    hb.stop()

    try:
        if "ok" in result:
            outs, seed = result["ok"]
            backend.complete(item.item_id, token, MODEL_VERSION, seed, O.to_callback(outs), item.trace_id)
            log.info("%s %s 완료 %.1fs", item.item_id, item.style_preset, time.time() - t0)
        else:
            e: ItemError = result["err"]
            backend.fail(item.item_id, token, e.code, e.retryable, e.attempt, item.trace_id)
            log.warning("%s %s 최종 실패 %s (attempt %d): %s", item.item_id, item.style_preset, e.code, e.attempt, e.message)
    except backend.BackendError as e:
        if e.status and 400 <= e.status < 500:
            # 400 요청 수정 / 401 서명 / 404·409 상태 충돌 / 422·403 S3 — 무조건 ACK 하지 않고 격리해 사람이 본다
            log.error("%s 콜백 %s: %s — quarantine", item.item_id, e.status, e.text[:160])
            ch.basic_nack(tag, requeue=False)
            return
        log.error("%s 콜백 재전송 소진(%s) — requeue", item.item_id, e)
        time.sleep(10)
        ch.basic_nack(tag, requeue=True)
        return
    ch.basic_ack(tag)


def run_forever():
    s3 = s3io.S3({"ORIGINAL": C.ORIGINAL_BUCKET, "AI_PROCESSED": C.AI_PROCESSED_BUCKET}, C.AWS_REGION)
    while True:
        try:
            conn = pika.BlockingConnection(pika.URLParameters(C.RABBIT_URL))
            ch = conn.channel()
            ch.confirm_delivery()                    # BUSY 재발행 confirm
            if C.RABBIT_DECLARE:
                ch.exchange_declare(C.RABBIT_EXCHANGE, exchange_type="topic", durable=True)
                ch.exchange_declare(C.RABBIT_RETRY_EXCHANGE, exchange_type="topic", durable=True)
                ch.queue_declare(C.RABBIT_QUEUE, durable=True)
                ch.queue_bind(C.RABBIT_QUEUE, C.RABBIT_EXCHANGE, C.RABBIT_ROUTING_KEY)
            ch.basic_qos(prefetch_count=1)
            ch.basic_consume(C.RABBIT_QUEUE, lambda c, m, p, b: handle(c, m, p, b, s3))
            log.info("소비 시작 queue=%s worker=%s", C.RABBIT_QUEUE, C.WORKER_ID)
            ch.start_consuming()
        except StopConsuming as e:
            log.error("소비 중단: %s. 라우팅/Dispatcher 확인 뒤 재시작하라", e)
            return
        except (pika.exceptions.AMQPConnectionError, pika.exceptions.StreamLostError) as e:
            log.error("RabbitMQ 연결 끊김: %s — 10초 후 재연결", e)
            time.sleep(10)
