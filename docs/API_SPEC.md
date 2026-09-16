# 백엔드 ↔ AI API 명세서

백엔드(Spring)와 AI 파이프라인 사이의 연동 규격. 아이템 하나 = 카드 한 장이다.
전체 운영 규칙·결정 이력은 [BACKEND_CONTRACT.md](BACKEND_CONTRACT.md), 구조는 [ARCHITECTURE.md](ARCHITECTURE.md).

## 인터페이스 한눈에

| # | 방향 | 채널 | 용도 |
|---|---|---|---|
| ① 작업 요청 | 백엔드 → AI | RabbitMQ | 카드 한 장 생성 요청 |
| ② 결과 콜백 | AI → 백엔드 | HTTPS (HMAC) | claim · progress · complete · fail |
| ③ 카탈로그 | 백엔드 → AI 워커 | HTTP (Bearer) | 지원 카드 목록 조회 |
| ④ 모델 호출 | 워커 → GPU 서비스 | HTTP (내부) | 컨셉·웹툰 생성 (`/generate`) |

```
백엔드 ──①큐──▶ RabbitMQ ──▶ EC2 워커 ──④──▶ GPU 서비스
   ▲                            │
   └──────②콜백(HMAC)───────────┘   ③ GET /collections (백엔드 → 워커)
```

파이프라인 버전: `portrait-restyle-v1` · 메시지 `AI_GENERATION_ITEM_REQUESTED` (schemaVersion 1)

---

## 카드 카탈로그

| collection | stylePreset | rarity | 엔진 | executionTarget |
|---|---|---|---|---|
| jobs | `ARTIST` `CONSTRUCTION_WORKER` `COOK` `DETECTIVE` `DOCTOR` `FIREFIGHTER` `MAGICIAN` `POLICE_OFFICER` | N | inswapper_128 | `EC2_CPU` |
| zodiac | `MOUSE` `COW` `TIGER` `RABBIT` `DRAGON` `SNAKE` `HORSE` `SHEEP` `MONKEY` `CHICKEN` `DOG` `PIG` | N | inswapper_128 | `EC2_CPU` |
| concept | `CYBERPUNK` `VAMPIRE` | R | FLUX.1-dev + PuLID | `GPU` |
| ani | `WEBTOON` | SR | FLUX.1 Kontext + LoRA | `GPU` |
| original | `ORIGINAL` | N | 원본 크롭 (생성 없음) | `EC2_CPU` |

- 코드 표의 주인은 워커 매니페스트(`manifests/*.yaml`). 백엔드는 **③ `GET /collections`** 로 읽는다.
- 안경 착용자의 직업·12지신은 예외로 GPU 경로를 탄다(원본 안경을 Kontext로 그려 넣어야 함).

---

## ① 작업 요청 — RabbitMQ (백엔드 → AI)

| 항목 | 값 |
|---|---|
| Exchange / Routing key | `motion.ai` / `ai.generate.item` (CPU·GPU 분리 시 `ai.generate.item.cpu` / `.gpu`) |
| Queue | `motion.ai.generate.item` (분리 시 `…​.cpu` / `…​.gpu`) |
| 재시도 exchange | `motion.retry.30s` (BUSY 재발행, `deferCount+1`) |
| eventType / schemaVersion | `AI_GENERATION_ITEM_REQUESTED` / `1` |
| 소비 설정 | manual ACK, prefetch 1, concurrency 1 |

payload 주요 필드:

```json
{
  "itemId": "6101", "batchId": "6001", "generationType": "CARD",
  "source":  { "bucketType": "ORIGINAL", "objectKey": "users/101/ai-sources/29/original.webp", "checksumSha256": "…64 hex" },
  "subject": { "gender": "MALE" },
  "generation": { "stylePreset": "DOCTOR", "modelVersion": "portrait-restyle-v1", "promptTemplateVersion": "jobs-v3" },
  "outputTargets": [
    { "variant": "IMAGE",  "bucketType": "AI_PROCESSED", "objectKey": "users/101/ai-results/6001/6101/image.webp" },
    { "variant": "CUTOUT", "bucketType": "AI_PROCESSED", "objectKey": "users/101/ai-results/6001/6101/cutout.webp" }
  ]
}
```

- 워커는 `stylePreset`으로 매니페스트를 찾아 라우팅한다(프리사인 URL·executionTarget은 메시지에 없음).
- `outputTargets`의 variant·objectKey·형식은 **요청받은 그대로** 유지한다.
- 처리 대상이 아닌 eventType·담당하지 않는 코드는 소비를 중단한다(임의 ACK·무한 requeue 금지).

---

## ② 결과 콜백 — HTTPS (AI → 백엔드)

공통 경로: `POST {origin}/internal/v1/ai-generation-items/{itemId}/{action}`

### 인증 (HMAC-SHA256)

| Header | 값 |
|---|---|
| `X-AI-Timestamp` | Unix epoch seconds |
| `X-AI-Nonce` | 요청마다 새 UUID |
| `X-AI-Signature` | `v1=` + HMAC-SHA256 lowercase hex |
| `X-Trace-Id` | 메시지 traceId |
| `X-Idempotency-Key` | complete·fail 의 `callbackEventId` |

```
bodyHash  = lowercaseHex(SHA256(rawBodyBytes))
canonical = timestamp + "\n" + nonce + "\n" + "POST" + "\n" + path + "\n" + bodyHash
signature = "v1=" + lowercaseHex(HMAC_SHA256(secret, canonical))
```

재전송 시 `callbackEventId`·멱등키·Body는 유지하고 Timestamp·Nonce·서명만 갱신한다.

### 엔드포인트

| API | 요청 Body | 응답 |
|---|---|---|
| `POST …/claim` | `{ messageId, workerId }` | 200 `{ disposition, workerToken, leaseExpiresAt }` (BUSY도 200, token null) |
| `POST …/progress` | `{ workerToken, progress: 0~99, stage }` | 204 (lease 5분 갱신) |
| `POST …/complete` | 아래 참조 | 200 빈 Body (동일 재전송 200, 내용 다르면 409 `AI_RESULT_CONFLICT`) |
| `POST …/fail` | `{ callbackEventId, workerToken, errorCode, retryable, attempt }` | 200 (최종 실패 확정) |

`disposition` ∈ `CLAIMED` · `BUSY` · `ALREADY_COMPLETED` · `ALREADY_FAILED` · `CANCELED`
`stage` ∈ `DOWNLOADING` · `PREPROCESSING` · `GENERATING` · `UPLOADING`

`/complete` Body:

```json
{
  "callbackEventId": "uuid", "workerToken": "claim 응답 UUID",
  "modelVersion": "portrait-restyle-v1", "seed": 1000,
  "outputs": [
    { "variant": "IMAGE",  "bucketType": "AI_PROCESSED", "objectKey": "users/101/ai-results/6001/6101/image.webp",  "contentType": "image/webp", "fileSize": 812345, "checksumSha256": "…64 hex" },
    { "variant": "CUTOUT", "bucketType": "AI_PROCESSED", "objectKey": "users/101/ai-results/6001/6101/cutout.webp", "contentType": "image/webp", "fileSize": 640221, "checksumSha256": "…" }
  ]
}
```

`outputs`는 DTO 필드만(variant · bucketType · objectKey · contentType · fileSize · checksumSha256). `seed`는 필수 정수(inswapper·원본은 0).

---

## ③ 카탈로그 — GET /collections (백엔드 → AI 워커)

`GET http://<worker-host>:8090/collections`, `Authorization: Bearer <token>` — GPU를 깨우지 않고 매니페스트만 읽는다.

- `ETag: "<catalogVersion>"` (strong). `If-None-Match` 일치 시 **304**.
- 백엔드는 시작 시·5분 간격 갱신, 마지막 정상 카탈로그 24시간 유효.

```json
{
  "catalogVersion": "a0030921", "modelVersion": "portrait-restyle-v1",
  "items": [
    { "stylePreset": "DOCTOR",   "collection": "jobs",     "rarity": "N",  "executionTarget": "EC2_CPU", "enabled": true, "width": 896,  "height": 1152, "promptTemplateVersion": "jobs-v3" },
    { "stylePreset": "CYBERPUNK","collection": "concept",  "rarity": "R",  "executionTarget": "GPU",     "enabled": true, "width": 896,  "height": 1152, "promptTemplateVersion": "concept-v4" },
    { "stylePreset": "WEBTOON",  "collection": "ani",      "rarity": "SR", "executionTarget": "GPU",     "enabled": true, "width": 1290, "height": 1659, "promptTemplateVersion": "ani-v2" },
    { "stylePreset": "ORIGINAL", "collection": "original", "rarity": "N",  "executionTarget": "EC2_CPU", "enabled": true, "width": 896,  "height": 1152, "promptTemplateVersion": "original-v1" }
  ]
}
```

`width`/`height`는 **최종 IMAGE·CUTOUT 파일 크기**다.

---

## ④ GPU 모델 서비스 — 내부 HTTP (워커 → GPU)

컨셉·웹툰(과 안경 착용자 코스튬)만 GPU 서비스를 호출한다. 워커는 역방향 SSH 터널로 접근한다.

| API | 계약 |
|---|---|
| `GET /health` | 200 `{ "status":"ok", "comfyReady": bool, "modelsLoaded": bool }` |
| `POST /generate` | 아이템 1건 동기. 요청 = 큐 payload + `source.downloadUrl` (+ `outputTargets[].uploadUrl` 또는 `returnBytes:true`) + 선택 `preprocess`, `imageOnly` |

성공 응답: `{ itemId, modelVersion, promptTemplateVersion, seed, outputs[], metrics }`
outputs 항목 = variant · objectKey · contentType · fileSize · checksumSha256 · width · height (+ `data` base64, relay 일 때).
실패 응답: 4xx(입력) / 5xx(생성·업로드) + `{ error: { code, message, retryable } }`. 429 = 일시적 포화.
시간·동시성: 생성 600초 · 연결 10초 · 응답 630초 · 동시성 1.

> `imageOnly:true` 면 GPU는 그림만 반환하고, 누끼·인코딩·업로드는 워커(CPU)가 한다(단일 GPU를 그림 전용으로).

---

## S3 규약

- 원본: `users/{userId}/ai-sources/{uploadId}/original.webp`
- 결과: `outputTargets[].objectKey` 그대로. 버킷은 `ORIGINAL_BUCKET` / `AI_PROCESSED_BUCKET` 환경변수.
- 업로드는 native SHA-256(`ChecksumSHA256` Base64) 포함. 콜백 체크섬은 같은 digest의 hex.
- 프리사인 URL은 claim 뒤 발급, TTL 30분, 큐·DB·로그에 저장하지 않는다.
- **relay가 공식 경로**: GPU 결과는 EC2 워커가 받아 AWS SDK로 업로드한다.

출력 형식: IMAGE 무손실 webp RGB · CUTOUT 무손실 webp RGBA · THUMBNAIL 긴 변 320px webp(품질 80) · SUBJECT_MASK `subject-mask.png` image/png(8비트 흑백, 배경 0·피사체 1~255).

---

## 오류 코드 (`/fail` errorCode)

| errorCode | 뜻 | 재시도 |
|---|---|---|
| `VERSION_MISMATCH` | modelVersion / promptTemplateVersion 불일치 | 없음 |
| `SOURCE_CHECKSUM` / `SOURCE_DOWNLOAD` | 원본 체크섬 불일치 / 원본 없음 | 없음 |
| `NO_FACE` | 얼굴 검출 실패 | 없음 |
| `GPU_UNAVAILABLE` | GPU 모델 서버 미설정 | 없음 |
| `MODEL_TIMEOUT` / `MODEL_ERROR` / `MODEL_BUSY` | GPU 응답 없음 / 5xx·생성 오류 / 429 | 총 3회 |
| `UPLOAD_FAILED` / `CUTOUT_FAILED` | S3 PUT 실패 / 누끼 서비스 오류 | 총 3회 |
| `WORKER_ERROR` | 워커 내부 예외 | 없음 |

재시도 가능 오류는 최초 포함 총 3회(5초·15초 백오프). 검증 실패는 즉시 `/fail`(`attempt:0`).

---

## 버전 관리

| 버전 | 대상 | 불일치 시 |
|---|---|---|
| `modelVersion` | 파이프라인 (`portrait-restyle-v1`) | `VERSION_MISMATCH` → `/fail` |
| `promptTemplateVersion` | 카드(컬렉션) 매니페스트 (예 `jobs-v3`) | `VERSION_MISMATCH` → `/fail` |
| `catalogVersion` | 코드 표 해시 | 바뀌면 백엔드가 카탈로그 재조회 |

카드 추가 = 매니페스트 yaml 한 장 → 워커 재배포 → `catalogVersion` 변경 → 백엔드 재조회.
