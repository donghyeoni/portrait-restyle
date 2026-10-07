"""모니터가 운영 DB 에 던지는 SQL. 결과는 모두 json 하나로 받는다.

psql 로 돌리므로 바인딩 파라미터가 없다. 그래서 SQL 에 들어가는 값은 전부 여기서 형식을 확인한 것만 쓴다.
숫자는 int 로 바꾸고, 코드 값은 허용 목록이나 [A-Z0-9_] 형식만, 자유 입력(닉네임)은 따옴표를 두 번 써서 문자열로만 넣는다.
"""
from __future__ import annotations

import re

STATUSES = ("PENDING", "PROCESSING", "RETRY_WAITING", "RECONCILING", "COMPLETED", "FAILED", "CANCELED")
ACTIVE = ("PENDING", "PROCESSING", "RETRY_WAITING", "RECONCILING")
RARITIES = ("N", "R", "SR", "SSR")
GENERATION_TYPES = ("USER_CARD", "DRAW_CARD_ASSET")
TARGETS = ("EC2_CPU", "GPU", "CLOUD_RUN_GPU")
MAX_HOURS = 24 * 30
MAX_LIMIT = 100
_CODE = re.compile(r"^[A-Z0-9_]{1,80}$")
_COLLECTION = re.compile(r"^[a-z0-9_]{1,50}$")


class Invalid(ValueError):
    pass


def _int(value, name, low=1, high=None, default=None):
    if value in (None, ""):
        if default is None:
            return None
        return default
    try:
        number = int(str(value).strip())
    except ValueError as exc:
        raise Invalid(f"{name} 는 정수여야 합니다") from exc
    if number < low or (high is not None and number > high):
        raise Invalid(f"{name} 범위가 올바르지 않습니다")
    return number


def _one_of(value, allowed, name):
    if value in (None, ""):
        return None
    text = str(value).strip().upper()
    if text not in allowed:
        raise Invalid(f"{name} 는 {', '.join(allowed)} 중 하나여야 합니다")
    return text


def _code(value, name):
    if value in (None, ""):
        return None
    text = str(value).strip().upper()
    if not _CODE.match(text):
        raise Invalid(f"{name} 형식이 올바르지 않습니다")
    return text


def _collection(value):
    if value in (None, ""):
        return None
    text = str(value).strip().lower()
    if not _COLLECTION.match(text):
        raise Invalid("collection 형식이 올바르지 않습니다")
    return text


def literal(text: str) -> str:
    """자유 입력을 SQL 문자열 상수로. 제어 문자와 역슬래시는 받지 않는다."""
    if any(ord(ch) < 32 for ch in text) or "\\" in text:
        raise Invalid("쓸 수 없는 문자가 들어 있습니다")
    return "'" + text.replace("'", "''") + "'"


def _in(values) -> str:
    return ", ".join(f"'{v}'" for v in values)


def _ms(expr: str) -> str:
    return f"round(1000 * extract(epoch from ({expr})))"


def summary(hours) -> str:
    h = _int(hours, "hours", 1, MAX_HOURS, 24)
    since = f"now() - make_interval(hours => {h})"
    run = "i.completed_at - i.started_at"
    done = "i.status = 'COMPLETED' AND i.started_at IS NOT NULL"
    return f"""
    json_build_object(
      'hours', {h},
      'since', {since},
      'presets', COALESCE((SELECT json_agg(p ORDER BY p.total DESC, p."stylePreset") FROM (
          SELECT i.style_preset AS "stylePreset", max(i.preset_collection_code) AS collection,
                 i.rarity_code AS rarity, i.execution_target AS "executionTarget",
                 max(i.generation_source) AS "generationSource",
                 count(*) AS total,
                 count(*) FILTER (WHERE i.status = 'COMPLETED') AS completed,
                 count(*) FILTER (WHERE i.status = 'FAILED') AS failed,
                 count(*) FILTER (WHERE i.status IN ({_in(ACTIVE)})) AS active,
                 round(1000 * avg(extract(epoch from (i.started_at - i.created_at)))
                     FILTER (WHERE i.started_at IS NOT NULL)) AS "avgQueueMs",
                 round(1000 * avg(extract(epoch from ({run}))) FILTER (WHERE {done})) AS "avgRunMs",
                 round((1000 * percentile_cont(0.9) WITHIN GROUP (ORDER BY extract(epoch from ({run})))
                     FILTER (WHERE {done}))::numeric) AS "p90RunMs"
          FROM ai_generation_items i
          WHERE i.created_at >= {since}
          GROUP BY i.style_preset, i.rarity_code, i.execution_target) p), '[]'::json),
      'errors', COALESCE((SELECT json_agg(e) FROM (
          SELECT COALESCE(i.error_code, 'UNKNOWN') AS "errorCode", count(*) AS count
          FROM ai_generation_items i
          WHERE i.created_at >= {since} AND i.status = 'FAILED'
          GROUP BY 1 ORDER BY 2 DESC LIMIT 12) e), '[]'::json)
    )"""


_ROW = f"""
    i.item_id::text AS "itemId", i.batch_id::text AS "batchId", b.generation_type AS "generationType",
    CASE WHEN b.subject_user_id IS NULL THEN NULL ELSE json_build_object(
        'userId', b.subject_user_id::text, 'publicUserNumber', u.public_user_number, 'nickname', u.nickname) END AS subject,
    i.style_preset AS "stylePreset", i.preset_collection_code AS collection, i.rarity_code AS rarity,
    i.execution_target AS "executionTarget", i.generation_source AS "generationSource",
    i.status, i.progress, i.error_code AS "errorCode", i.attempt_count AS "attemptCount",
    i.created_at AS "createdAt", i.started_at AS "startedAt", i.completed_at AS "completedAt",
    {_ms("i.started_at - i.created_at")} AS "queueMs", {_ms("i.completed_at - i.started_at")} AS "runMs"
"""
_FROM = """
    FROM ai_generation_items i
    JOIN ai_generation_batches b ON b.batch_id = i.batch_id
    LEFT JOIN users u ON u.user_id = b.subject_user_id
"""


def items(params: dict) -> tuple[str, int]:
    h = _int(params.get("hours"), "hours", 1, MAX_HOURS, 72)
    limit = _int(params.get("limit"), "limit", 1, MAX_LIMIT, 50)
    where = [f"i.created_at >= now() - make_interval(hours => {h})"]
    conditions = [
        (_int(params.get("userId"), "userId"), "b.subject_user_id = {}"),
        (_one_of(params.get("status"), STATUSES, "status"), "i.status = '{}'"),
        (_code(params.get("stylePreset"), "stylePreset"), "i.style_preset = '{}'"),
        (_collection(params.get("collection")), "i.preset_collection_code = '{}'"),
        (_one_of(params.get("rarity"), RARITIES, "rarity"), "i.rarity_code = '{}'"),
        (_code(params.get("errorCode"), "errorCode"), "i.error_code = '{}'"),
        (_one_of(params.get("generationType"), GENERATION_TYPES, "generationType"), "b.generation_type = '{}'"),
        (_one_of(params.get("executionTarget"), TARGETS, "executionTarget"), "i.execution_target = '{}'"),
        (_int(params.get("cursor"), "cursor"), "i.item_id < {}"),
    ]
    where += [template.format(value) for value, template in conditions if value is not None]
    sql = f"""
    COALESCE((SELECT json_agg(r ORDER BY (r."itemId")::bigint DESC) FROM (
        SELECT {_ROW} {_FROM}
        WHERE {' AND '.join(where)}
        ORDER BY i.item_id DESC
        LIMIT {limit + 1}) r), '[]'::json)"""
    return sql, limit


def detail(item_id) -> str:
    i = _int(item_id, "itemId")
    return f"""
    (SELECT json_build_object(
        'item', (SELECT row_to_json(r) FROM (SELECT {_ROW} {_FROM} WHERE i.item_id = {i}) r),
        'batchStatus', b.status, 'requestMode', b.request_mode, 'gender', b.gender_snapshot,
        'modelVersion', i.model_version, 'promptTemplateVersion', i.prompt_template_version,
        'presetCatalogVersion', i.preset_catalog_version, 'seed', i.seed,
        'cardAssetId', i.card_asset_id::text, 'cardAssetStatus', ca.status, 'themeReleaseId', i.theme_release_id::text,
        'attempts', COALESCE((SELECT json_agg(a ORDER BY a."attemptNo", a.created) FROM (
            SELECT attempt_id::text AS "attemptId", stage, attempt_no AS "attemptNo", trigger_type AS "triggerType",
                   status, started_at AS "startedAt", completed_at AS "completedAt", created_at AS created,
                   {_ms("completed_at - started_at")} AS "totalMs"
            FROM ai_generation_attempts WHERE item_id = {i}) a), '[]'::json),
        'failures', COALESCE((SELECT json_agg(f ORDER BY f.at) FROM (
            SELECT o.created_at AS at, p.body ->> 'errorCode' AS "errorCode",
                   (p.body ->> 'retryable')::boolean AS retryable, (p.body ->> 'attempt')::int AS attempt
            FROM outbox_events o
            CROSS JOIN LATERAL (SELECT CASE WHEN json_typeof(o.payload) = 'string'
                                            THEN (o.payload #>> '{{}}')::json ELSE o.payload END AS body) p
            WHERE o.aggregate_type = 'AI_GENERATION_ITEM' AND o.aggregate_id = {i}
              AND o.event_type = 'AI_GENERATION_ITEM_FAILED') f), '[]'::json),
        'media', COALESCE((SELECT json_agg(m ORDER BY m.ord, m."mediaFileId") FROM (
            SELECT * FROM (
                SELECT 0 AS ord, mf.media_file_id::text AS "mediaFileId", 'SOURCE' AS role, mf.variant,
                       mf.content_type AS "contentType", mf.file_size AS "fileSize", mf.bucket_type AS bucket, mf.object_key AS key
                FROM media_files mf
                WHERE mf.upload_id = b.source_upload_id AND mf.bucket_type = 'ORIGINAL' AND mf.variant = 'ORIGINAL'
                  AND mf.deleted_at IS NULL
                ORDER BY mf.media_file_id DESC LIMIT 1) s
            UNION ALL
            SELECT CASE mf.variant WHEN 'IMAGE' THEN 1 WHEN 'CUTOUT' THEN 2 WHEN 'THUMBNAIL' THEN 3 ELSE 4 END,
                   mf.media_file_id::text,
                   CASE mf.variant WHEN 'IMAGE' THEN 'RESULT' WHEN 'CUTOUT' THEN 'CUTOUT'
                                   WHEN 'THUMBNAIL' THEN 'THUMBNAIL' WHEN 'SUBJECT_MASK' THEN 'MASK' ELSE 'OTHER' END,
                   mf.variant, mf.content_type, mf.file_size, mf.bucket_type, mf.object_key
            FROM media_files mf
            WHERE mf.bucket_type = 'AI_PROCESSED' AND mf.deleted_at IS NULL
              AND mf.object_key IN (i.output_image_object_key, i.output_cutout_object_key,
                                    i.output_thumbnail_object_key, i.output_subject_mask_object_key)
        ) m), '[]'::json)
    )
    FROM ai_generation_items i
    JOIN ai_generation_batches b ON b.batch_id = i.batch_id
    LEFT JOIN card_assets ca ON ca.card_asset_id = i.card_asset_id
    WHERE i.item_id = {i})"""


def users(query: str) -> str:
    text = (query or "").strip()
    if not text:
        raise Invalid("검색어를 입력하세요")
    if len(text) > 40:
        raise Invalid("검색어가 너무 깁니다")
    number = int(text) if text.isdigit() and len(text) < 18 else -1
    like = literal(text.lower().replace("%", "").replace("_", "") + "%")
    return f"""
    COALESCE((SELECT json_agg(x) FROM (
        SELECT user_id::text AS "userId", public_user_number AS "publicUserNumber", nickname
        FROM users
        WHERE status = 'ACTIVE' AND deleted_at IS NULL AND role <> 'ADMIN'
          AND (lower(nickname) LIKE {like} OR public_user_number = {number} OR user_id = {number})
        ORDER BY nickname LIMIT 20) x), '[]'::json)"""
