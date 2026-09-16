"""아이템 파싱·검증. RabbitMQ 메시지(EC2)와 GPU 서비스 요청 본문 두 모양을 같은 Item 으로 만든다.

백엔드 전달서(2026-09-08) 4절: outputTargets 의 variant·objectKey·파일 형식은 **요청받은 그대로** 유지한다
(SUBJECT_MASK 를 CUTOUT 으로 바꾸지 않는다). 할당된 Target 이 두 개면 두 개만 만든다.
"""
from __future__ import annotations

import pathlib
import sys
from dataclasses import dataclass, field

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from manifests import MODEL_VERSION, by_code   # noqa: E402

EVENT_TYPE = "AI_GENERATION_ITEM_REQUESTED"
SCHEMA_VERSION = 1
# variant -> (content type, 파일 형식 설명)
VARIANTS = {
    "IMAGE": "image/webp",          # 무손실 webp RGB
    "CUTOUT": "image/webp",         # 무손실 webp RGBA
    "THUMBNAIL": "image/webp",      # 긴 변 320px, 품질 80
    "SUBJECT_MASK": "image/png",    # 관리자 경로. subject-mask.png (image/png) 유지
}


class ItemError(Exception):
    """오류 코드 `[A-Z][A-Z0-9_]{2,79}`. retryable=False 는 즉시 최종 /fail, True 는 워커가 총 3회 안에서 재시도."""

    def __init__(self, code: str, message: str = "", retryable: bool = False, http_status: int | None = None):
        super().__init__(f"{code}: {message}")
        self.code, self.message, self.retryable = code, message, retryable
        self.http_status = http_status or (503 if retryable else 400)
        self.attempt = 0

    def to_dict(self) -> dict:
        return {"error": {"code": self.code, "message": self.message, "retryable": self.retryable}}


@dataclass
class Target:
    variant: str
    bucket_type: str
    object_key: str
    upload_url: str | None = None

    @property
    def content_type(self) -> str:
        return VARIANTS[self.variant]


@dataclass
class Item:
    item_id: str
    batch_id: str
    style_preset: str
    gender: str                       # "male" | "female"
    model_version: str
    prompt_template_version: str | None
    source_bucket_type: str
    source_key: str
    source_checksum: str | None
    source_download_url: str | None
    targets: list[Target] = field(default_factory=list)
    message_id: str | None = None
    trace_id: str | None = None
    user_id: str | None = None
    generation_type: str | None = None
    attempt: int = 0                  # 봉투 attempt (백엔드 측 실행 번호 참고값)
    defer_count: int = 0
    preprocess_hint: dict | None = None

    def target(self, variant: str) -> Target | None:
        return next((t for t in self.targets if t.variant == variant), None)

    @property
    def wanted(self) -> list[str]:
        return [t.variant for t in self.targets]


def _gender(v) -> str:
    v = (v or "").upper()
    if v not in ("MALE", "FEMALE"):
        raise ItemError("INVALID_MESSAGE", f"subject.gender 는 MALE|FEMALE 이어야 함 (받은 값 {v!r})")
    return v.lower()


def _targets(raw) -> list[Target]:
    out = []
    for t in raw or []:
        v = t.get("variant")
        if v not in VARIANTS:
            raise ItemError("INVALID_MESSAGE", f"알 수 없는 outputTargets.variant {v!r}")
        if not t.get("objectKey"):
            raise ItemError("INVALID_MESSAGE", f"outputTargets[{v}].objectKey 없음")
        out.append(Target(v, t.get("bucketType", "AI_PROCESSED"), t["objectKey"], t.get("uploadUrl")))
    if not out:
        raise ItemError("INVALID_MESSAGE", "outputTargets 가 비어 있음")
    if len({t.variant for t in out}) != len(out):
        raise ItemError("INVALID_MESSAGE", "outputTargets 에 variant 가 중복됨")
    return out


def from_payload(payload: dict, *, message_id=None, trace_id=None, attempt=0, defer_count=0) -> Item:
    """규약 payload (RabbitMQ) 또는 GPU 서비스 요청 본문 (downloadUrl/uploadUrl 포함)."""
    try:
        src, subj, gen = payload["source"], payload["subject"], payload["generation"]
        return Item(
            item_id=str(payload["itemId"]), batch_id=str(payload.get("batchId", "")),
            style_preset=str(gen["stylePreset"]).upper(), gender=_gender(subj.get("gender")),
            model_version=gen.get("modelVersion", ""), prompt_template_version=gen.get("promptTemplateVersion"),
            source_bucket_type=src.get("bucketType", "ORIGINAL"), source_key=src["objectKey"],
            source_checksum=(src.get("checksumSha256") or None), source_download_url=src.get("downloadUrl"),
            targets=_targets(payload.get("outputTargets")),
            message_id=message_id, trace_id=trace_id, user_id=subj.get("subjectUserId"),
            generation_type=payload.get("generationType"), attempt=int(attempt or 0), defer_count=int(defer_count or 0),
            preprocess_hint=payload.get("preprocess"))
    except KeyError as e:
        raise ItemError("INVALID_MESSAGE", f"필수 필드 없음: {e}") from e
    except (TypeError, ValueError) as e:
        raise ItemError("INVALID_MESSAGE", f"필드 형식 오류: {e}") from e


def from_envelope(env: dict) -> Item:
    """RabbitMQ 봉투 전체. 형식 오류는 INVALID_MESSAGE(→ reject/quarantine), 처리 대상이 아닌 종류는 UNSUPPORTED_EVENT."""
    if not isinstance(env, dict) or "payload" not in env:
        raise ItemError("INVALID_MESSAGE", "봉투에 payload 가 없음")
    if int(env.get("schemaVersion", 0)) != SCHEMA_VERSION:
        raise ItemError("INVALID_MESSAGE", f"schemaVersion {env.get('schemaVersion')} 미지원")
    if env.get("eventType") != EVENT_TYPE:
        raise ItemError("UNSUPPORTED_EVENT", f"eventType {env.get('eventType')!r} 는 처리 대상이 아님")
    return from_payload(env["payload"], message_id=env.get("messageId"), trace_id=env.get("traceId"),
                        attempt=env.get("attempt", 0), defer_count=env.get("deferCount", 0))


def resolve(item: Item):
    """(컬렉션, 프리셋). 버전·코드 검증 — 실패는 재시도 없는 ItemError."""
    if item.model_version != MODEL_VERSION:
        raise ItemError("VERSION_MISMATCH", f"modelVersion {item.model_version!r} != {MODEL_VERSION!r}")
    try:
        coll, preset = by_code(item.style_preset)
    except KeyError:
        raise ItemError("UNSUPPORTED_STYLE", f"stylePreset {item.style_preset!r} 는 담당 코드가 아님") from None
    if item.prompt_template_version and item.prompt_template_version != coll.version:
        raise ItemError("VERSION_MISMATCH", f"promptTemplateVersion {item.prompt_template_version!r} != {coll.version!r}")
    return coll, preset
