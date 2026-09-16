# AI 아이템 생성 연동 규약

대상 메시지: `AI_GENERATION_ITEM_REQUESTED` (schemaVersion 1) · **v4 (2026-09-08, 백엔드 "MOTION AI 엔지니어 연동 전달서" 반영)** · 파이프라인 `portrait-restyle-v1`

v3 → v4 요지: Cloud Run 보류(GPU 환경 미정, HTTP 계약만 유지) · `executionTarget=GPU` · `/collections` Bearer+rarity+ETag ·
SUBJECT_MASK 변환 금지 · claim disposition · HMAC 규격 확정 · 총 3회 실행(0·1·2) · 콜백 재전송 ID 유지 · `/complete` DTO 필드 한정 ·
S3 native SHA-256 · 공유 큐라 소비 기본 비활성.

## 1. 한 줄 요약

아이템 하나는 **그림 한 장**이다. `stylePreset`에 프리셋 코드가 오고, EC2 워커가 그 한 장을 요청받은 `outputTargets` 그대로(변형·키·형식) S3에
올린 뒤 백엔드 콜백(`/complete`)으로 알린다. 직업·12지신은 EC2 CPU에서 직접 만들고, 컨셉·웹툰(과 안경 착용자의 코스튬)은 GPU 모델 서버를 HTTP로 호출한다.

## 2. 담당 stylePreset 23개

| collection | stylePreset | rarity | 엔진 | executionTarget | 장당 시간 (개발 서버 실측) |
|---|---|---|---|---|---|
| jobs | `ARTIST` `CONSTRUCTION_WORKER` `COOK` `DETECTIVE` `DOCTOR` `FIREFIGHTER` `MAGICIAN` `POLICE_OFFICER` | N | inswapper_128 | `EC2_CPU` | 약 7초 (누끼 포함) |
| zodiac | `MOUSE` `COW` `TIGER` `RABBIT` `DRAGON` `SNAKE` `HORSE` `SHEEP` `MONKEY` `CHICKEN` `DOG` `PIG` | N | inswapper_128 | `EC2_CPU` | 약 7초 |
| concept | `CYBERPUNK` `VAMPIRE` | R | FLUX.1-dev + PuLID | `GPU` | 15~30초 |
| ani | `WEBTOON` | SR | FLUX.1 Kontext + LoRA | `GPU` | 30~40초 (업스케일 포함) |

- 코드 표의 주인은 워커 매니페스트(`manifests/*.yaml`, 정적 사본 `manifests/catalog.json`). 백엔드는 `GET /collections`로 읽는다.
- **`EC2_CPU`는 주 실행 경로 분류이며 안경 착용자의 직업·12지신은 예외다.** 원본 안경을 Kontext로 그려 넣어야 해서(장당 약 20초) EC2 워커가
  GPU 모델 서버로 보낸다. GPU가 없는 상태에서 이 케이스가 오면 `GPU_UNAVAILABLE`로 최종 실패시키고 정상 처리로 표시하지 않는다.
- 아이템 수는 요청한 항목 수만큼이다(23은 현재 카탈로그 구성). 워커는 rarity·sequence를 재배정하지 않는다.

## 3. 메시지 (RabbitMQ)

| 항목 | 값 |
|---|---|
| Exchange / Routing key / Queue | `motion.ai` / `ai.generate.item` / `motion.ai.generate.item` |
| eventType / schemaVersion | `AI_GENERATION_ITEM_REQUESTED` / `1` |
| 소비 설정 | manual ACK, prefetch 1, concurrency 1 |
| BUSY 재발행 | `motion.retry.30s` exchange + `ai.generate.item`, deferCount+1, publisher confirm 후 원본 ACK |

payload는 백엔드 명세 8.1절 그대로 읽는다(`itemId`·`batchId`·`generationType`·`source`·`subject`·`generation`·`outputTargets`). 성별은 `subject.gender`.
메시지에 프리사인 URL·catalogVersion·executionTarget은 들어오지 않는다. 워커는 `stylePreset`과 모델·프롬프트 버전으로 매니페스트를 찾는다.

**공유 큐**: 현재 큐에는 소문자 26개 프리셋·legacy USER_CARD·Gemini·DRAW_CARD_ASSET이 섞여 있다. 전용 큐 또는 Dispatcher가 확정되기 전까지
`AI_WORKER_CONSUMER_ENABLED=false`를 유지한다(워커는 `/collections`만 서빙).

| 상황 | 워커 동작 |
|---|---|
| 잘못된 JSON · schemaVersion ≠ 1 · 필수 필드 누락 | `reject(requeue=false)` → quarantine |
| 형식은 정상이지만 처리 대상이 아닌 eventType / 담당하지 않는 stylePreset | requeue 한 번 뒤 **소비 중단**. 임의 ACK·quarantine·무한 requeue 금지 |
| claim `CLAIMED` | workerToken으로 처리 시작 |
| claim `BUSY` | deferCount+1로 retry exchange 재발행(confirm) → 원본 ACK |
| claim `ALREADY_COMPLETED` / `ALREADY_FAILED` / `CANCELED` | 작업 없이 ACK |
| complete 또는 최종 fail 2xx | ACK |
| 콜백 Timeout / 5xx | 같은 callbackEventId·Body로 콜백만 재전송(서명 헤더 갱신). ACK 보류 |
| 콜백 400/401/404/409/422/403 | 무조건 ACK 하지 않음. quarantine 후 사람이 확인 |

## 4. 필드 해석 (우리 프리셋일 때)

| 필드 | 우리 경로 |
|---|---|
| `stylePreset` | 2절 23개 코드(대문자). 컬렉션·executionTarget 라우팅 기준 |
| `modelVersion` | `portrait-restyle-v1`. 다르면 `VERSION_MISMATCH`로 `/fail` |
| `promptTemplateVersion` | 컬렉션 매니페스트 버전(예 `jobs-v3`). 있으면 대조, 다르면 `VERSION_MISMATCH` |
| `prompt`, `concept` | 무시 (튜닝된 고정 프롬프트) |
| `sequence`, `rarity` | 재배정하지 않음 |
| `subject.gender` | `MALE`/`FEMALE` 그대로. 판별하지 않음 |
| `source.checksumSha256` | 다운로드 뒤 대조. 불일치면 `SOURCE_CHECKSUM` |
| `outputTargets[]` | **variant·objectKey·형식 그대로.** 두 개면 두 개만, `SUBJECT_MASK`는 `subject-mask.png` image/png |

## 5. EC2 워커 처리 순서

```
RabbitMQ 수신 → 파싱·검증
 → POST /claim (messageId, workerId) → disposition 분기 (3절 표)
 → 원본 GET(SDK) → 체크섬 대조 → 전처리(얼굴·안경, 프로세스 메모리 캐시)
 → 실행 (총 3회, 번호 0·1·2; 재시도 가능 오류만 다시)
     EC2_CPU: inswapper → 누끼(cutout-cpu 8001) → 썸네일 → S3 PUT (ChecksumSHA256 native)
     GPU:     프리사인 GET 발급 → POST {GPU_SERVICE_URL}/generate (연결 10초·응답 630초)
              relay(기본): GPU 가 outputs[].data(base64) 반환 → EC2 가 SDK 로 native 체크섬 업로드
              presigned(POC 후): GPU 가 프리사인 PUT 으로 직접 업로드
 → 150초마다 /progress (호출·재시도 대기와 독립)
 → POST /complete (DTO 필드만) 또는 최종 POST /fail (attempt = 마지막 실행 번호)
 → 2xx 뒤에만 ACK
```

- **멱등성**: 모든 Target이 S3에 있고 HeadObject에 native SHA-256이 있을 때만 재생성 없이 `/complete`. 존재만으로 성공 처리하지 않는다.
- **재시도**: 429·일시적 모델/S3 오류는 최초 포함 총 3회(5초·15초 백오프). boto3 자동 재시도는 standard 2회로 제한. 소진 시 `/fail`(`retryable:false`, `attempt:2`).
  검증 실패(`VERSION_MISMATCH`, `UNSUPPORTED_STYLE`, `SOURCE_CHECKSUM`, `NO_FACE`, `GPU_UNAVAILABLE`)는 즉시 `/fail`(`attempt:0`). `/fail` 뒤 Retry Queue로 보내지 않는다.
- **출력 형식**: IMAGE 무손실 webp RGB · CUTOUT 무손실 webp RGBA · THUMBNAIL 긴 변 320px webp 품질 80 · SUBJECT_MASK `subject-mask.png` image/png, **8비트 단일 채널 흑백(배경 0, 피사체 1~255)**.
  IMAGE·CUTOUT 크기 jobs/zodiac/concept 896×1152, WEBTOON 1792×2304.
- **전처리 캐시**: `preprocess.json` S3 저장은 권한·보유 정책 확정 뒤(`PREPROCESS_CACHE_S3=true`). 그 전에는 프로세스 메모리 캐시. 임베딩·URL·토큰은 로그에 남기지 않는다.
- **NO_FACE**: 해당 아이템만 최종 실패. 같은 원본의 다른 아이템 취소는 하지 않는다.

## 6. 백엔드 콜백 API (워커 → 백엔드)

공통 경로 `/internal/v1/ai-generation-items/{itemId}` + 백엔드가 준 고정 HTTPS origin. `/api` 등을 붙이지 않는다.

### 6.1 HMAC

| Header | 값 |
|---|---|
| `X-AI-Timestamp` | Unix epoch seconds |
| `X-AI-Nonce` | 요청마다 새 UUID |
| `X-AI-Signature` | `v1=` + HMAC-SHA256 lowercase hex |
| `X-Trace-Id` | 메시지 traceId (`[A-Za-z0-9._-]{1,64}`) |
| `X-Idempotency-Key` | complete·fail 에서 Body의 callbackEventId |

```
bodyHash  = lowercaseHex(SHA256(rawRequestBodyBytes))
canonical = timestamp + "\n" + nonce + "\n" + "POST" + "\n" + path + "\n" + bodyHash
signature = "v1=" + lowercaseHex(HMAC_SHA256(UTF8(secret), UTF8(canonical)))
```
JSON은 한 번 직렬화한 바이트를 서명·전송에 같이 쓴다. 재전송은 callbackEventId·멱등키·Body 유지, Timestamp·Nonce·서명만 갱신.

### 6.2 API

| API | 요청 | 응답 |
|---|---|---|
| `POST …/claim` | `{messageId, workerId}` (workerId `[A-Za-z0-9][A-Za-z0-9._:-]{0,99}`) | 200 `{disposition, workerToken, leaseExpiresAt}` — BUSY도 200, token null |
| `POST …/progress` | `{workerToken, progress 0~99, stage}` stage ∈ DOWNLOADING·PREPROCESSING·GENERATING·UPLOADING | 204, lease 5분 갱신 |
| `POST …/complete` | 아래 | 200 빈 Body. 같은 ID·같은 내용 재전송 200, 다른 내용은 409 `AI_RESULT_CONFLICT` |
| `POST …/fail` | `{callbackEventId, workerToken, errorCode, retryable, attempt}` | 200. 항상 최종 실패 확정 |

```json
{
  "callbackEventId": "uuid", "workerToken": "claim 응답 UUID", "modelVersion": "portrait-restyle-v1", "seed": 1000,
  "outputs": [
    { "variant": "IMAGE",     "bucketType": "AI_PROCESSED", "objectKey": "users/101/ai-results/6001/6101/image.webp",     "contentType": "image/webp", "fileSize": 812345, "checksumSha256": "…64 hex" },
    { "variant": "CUTOUT",    "bucketType": "AI_PROCESSED", "objectKey": "users/101/ai-results/6001/6101/cutout.webp",    "contentType": "image/webp", "fileSize": 640221, "checksumSha256": "…" },
    { "variant": "THUMBNAIL", "bucketType": "AI_PROCESSED", "objectKey": "users/101/ai-results/6001/6101/thumbnail.webp", "contentType": "image/webp", "fileSize": 28114,  "checksumSha256": "…" }
  ]
}
```
`outputs`는 DTO 필드만(variant·bucketType·objectKey·contentType·fileSize·checksumSha256). GPU 응답의 width/height·metrics·promptTemplateVersion은 보내지 않는다.
`seed`는 필수 정수(inswapper는 0). 백엔드 오류 응답은 `{success:false, data:null, error:{code,message,fieldErrors}}`.

### 6.3 오류 코드 (`[A-Z][A-Z0-9_]{2,79}`)

| errorCode | 뜻 | 재시도 |
|---|---|---|
| `VERSION_MISMATCH` | modelVersion / promptTemplateVersion 불일치 | 없음 |
| `UNSUPPORTED_STYLE` | 담당하지 않는 코드 (소비 중단 대상, /fail 안 함) | — |
| `SOURCE_CHECKSUM` / `SOURCE_DOWNLOAD` | 원본 체크섬 불일치 / 원본 없음 | 없음 |
| `NO_FACE` | 얼굴 검출 실패 | 없음 |
| `GPU_UNAVAILABLE` | GPU 모델 서버 미설정 (안경 착용자 코스튬 포함) | 없음 |
| `MODEL_TIMEOUT` / `MODEL_ERROR` / `MODEL_BUSY` | GPU 응답 없음 / 5xx·생성 오류 / 429 | 총 3회 |
| `UPLOAD_FAILED` / `CUTOUT_FAILED` | S3 PUT 실패 / 누끼 서비스 오류 | 총 3회 |
| `WORKER_ERROR` | 워커 내부 예외 | 없음 |

## 7. GPU 모델 서버 HTTP 계약 (워커 → 추후 선정할 GPU 환경)

| 항목 | 계약 |
|---|---|
| `GET /health` | 살아 있으면 200 `{"status":"ok","comfyReady":bool,"modelsLoaded":bool}`. 모델 미준비와 구분 |
| `POST /generate` | 아이템 1건 동기. 요청 = Rabbit payload + `source.downloadUrl` (+ `outputTargets[].uploadUrl` 또는 `returnBytes:true`) + 선택 `preprocess` |
| 성공 응답 | `itemId, modelVersion, promptTemplateVersion, seed, outputs[], metrics`. outputs 항목 = variant, objectKey, contentType, fileSize, checksumSha256, width, height (+ `data` base64, relay 일 때) |
| 실패 응답 | 4xx(입력 문제) / 5xx(생성·업로드) + `{error:{code,message,retryable}}`. 429 = 일시적 포화 |
| 시간·동시성 | 생성 600초 · 워커 연결 10초·응답 630초 · 동시성 1 |
| 인증 | 미정. `GPU_AUTH_MODE` none / bearer / gcp-id-token 을 준비해 둠 |

GPU 서버는 RabbitMQ 계정·백엔드 HMAC·AWS 장기 키를 갖지 않는다. 가중치는 이미지와 별도(읽기 전용 볼륨), 버전·체크섬은 `models/registry.yaml`.
동일 아이템 재호출·타임아웃 후 중복 요청에도 결과가 섞이지 않는다(요청마다 고유 스테이징 파일·출력 접두어).

자원·실측(개발 서버 L40S): 가중치 상주 약 34GB VRAM, 컨셉 15~30초, 웹툰 30~40초, 안경 착용자 코스튬 36초. 콜드스타트(가중치 로드)는 볼륨 속도에 따라 2~4분 예상, 환경 확정 후 실측.

## 8. GET /collections (EC2 워커, GPU를 깨우지 않음)

`GET http://<ai-worker>:8090/collections`, `Authorization: Bearer <AI_PRESET_CATALOG_TOKEN>`. Host의 `127.0.0.1:8090`에만 바인딩. flat `items[]`, 모든 item에 rarity.
ETag = `"<catalogVersion>"` (strong), `If-None-Match` 일치 시 304. 백엔드는 시작 시·5분 간격 갱신, 마지막 정상 카탈로그 24시간 유효.

```json
{ "catalogVersion": "13e1ad8e", "modelVersion": "portrait-restyle-v1",
  "items": [
    { "stylePreset": "DOCTOR",    "collection": "jobs",    "rarity": "N",  "executionTarget": "EC2_CPU", "enabled": true, "width": 896,  "height": 1152, "promptTemplateVersion": "jobs-v3" },
    { "stylePreset": "CYBERPUNK", "collection": "concept", "rarity": "R",  "executionTarget": "GPU",     "enabled": true, "width": 896,  "height": 1152, "promptTemplateVersion": "concept-v4" },
    { "stylePreset": "WEBTOON",   "collection": "ani",     "rarity": "SR", "executionTarget": "GPU",     "enabled": true, "width": 1792, "height": 2304, "promptTemplateVersion": "ani-v2" } ] }
```
width/height는 **최종 IMAGE·CUTOUT 파일 크기**다(WEBTOON은 2배 업스케일된 1792×2304). 구 값 `CLOUD_RUN_GPU`는 양쪽이 `GPU`로 정규화한다.

## 9. S3

- 원본 `users/{userId}/ai-sources/{uploadId}/original.webp`, 결과는 `outputTargets.objectKey` 그대로.
- 버킷 실명은 `ORIGINAL_BUCKET`, `AI_PROCESSED_BUCKET` 환경변수.
- 업로드는 native SHA-256(`ChecksumSHA256` Base64) 포함. 백엔드는 HeadObject의 native checksum·Content-Type·Content-Length로 완료 검증. 콜백 체크섬은 같은 digest의 hex.
- 프리사인 URL은 claim 뒤 발급, TTL 30분, 큐·DB·로그에 저장하지 않는다.
- **relay 가 공식 경로**: 프리사인 PUT은 실제 HTTP·HeadObject 체크섬 POC 통과 전까지 쓰지 않는다. GPU 결과는 EC2 워커가 받아 AWS SDK로 업로드한다.

## 10. 배치

백엔드 실서버 확정(2026-09-08): RabbitMQ `127.0.0.1:5672` vhost `/<VHOST>` 계정 `<ai-worker>`(권한 보완 중) · S3 `<ORIGINAL_BUCKET> / `<AI_PROCESSED_BUCKET> (ap-northeast-2, 워커 전용 최소권한 준비 중) ·
콜백 origin `https://<BACKEND_HOST>`(Nginx 프록시 보완 중) · 카탈로그 `http://<ai-worker>:8090/collections` · EC2는 백엔드가 직접 배포(Docker 29.1.3 / Compose 2.40.3, 별도 compose, **host network**, 카탈로그·누끼 포트 127.0.0.1 바인딩) ·
경로 `/opt/<project>/ai-worker`, `/etc/<project>/ai-worker/worker.env`, `/etc/<project>/ai-worker/secrets/`. 배포 절차는 `serving/DEPLOY.md`.


| 자리 | 컴포넌트 | 역할 | 코드 |
|---|---|---|---|
| EC2 | `worker` | RabbitMQ 소비, claim/progress/complete/fail, jobs·zodiac 생성, GPU 호출, S3 SDK 업로드, `/collections` | `serving/worker/` |
| EC2 | `cutout-cpu` (8001) | EC2 생성분 누끼 | `serving/cutout-cpu/` |
| GPU 환경 (미정) | GPU 모델 서버 | `/health`, `/generate`: concept·ani(+안경 착용자 코스튬) 생성·누끼·썸네일 | `serving/gpu/` (Dockerfile: CUDA 12.8, torch cu128) |
| AWS | S3 | 원본·결과 | |
| TLJH GPU 서버 | 개발·튜닝 전용 | 서비스 경로 아님 | |

## 11. 서로 전달할 값

| 제공 | 항목 | 상태 |
|---|---|---|
| AI 팀 | 23개 매니페스트(rarity·실행 경로·크기·버전) | 완료 — `manifests/catalog.json`, WEBTOON rarity 확인 요청 |
| AI 팀 | 워커·GPU 서버 이미지 digest, 실행 방법·환경변수 목록 | Dockerfile·`.env.example` 준비, 이미지 빌드는 배포 준비 시 |
| AI 팀 | `/collections`·`/health`·`/generate` 예제, 콜백·재시도 테스트 결과 | 개발 서버에서 가짜 백엔드·S3로 9 케이스 통과 (DECISIONS.md) |
| AI 팀 | 가중치 크기·버전·체크섬, Peak VRAM/RAM·로딩·추론 실측, 안경 보정의 GPU 의존성 | `models/registry.yaml`(체크섬 채울 것), VRAM 34GB, 안경 보정 = GPU 필수 |
| 백엔드 | RabbitMQ host/port/vhost/계정, S3 버킷명·권한 | 대기 |
| 백엔드 | 고정 Callback origin, HMAC Secret, Catalog Token | 대기 |
| 공동 | 공유 큐 → 전용 큐 또는 Dispatcher, S3 서명 PUT POC | 대기 |
| 공동 | GPU 업체·장비·위치·가중치 저장소·레지스트리·인증 | 대기 |

## 12. 확인 완료 (백엔드 회신 2026-09-08)

| 질문 | 확정 |
|---|---|
| `SUBJECT_MASK` 내용 | 8비트 단일 채널 흑백 PNG. 배경 0, 피사체 1~255. `subject-mask.png`, `image/png` |
| `WEBTOON` rarity | `SR` |
| `/collections` width/height | 최종 IMAGE·CUTOUT 파일 크기. WEBTOON `1792×2304` |
| 공유 큐 전환 | 전용 큐 또는 Dispatcher 준비 전까지 `AI_WORKER_CONSUMER_ENABLED=false`. 일정은 공동 E2E 때 확정 |
| S3 서명 PUT | POC 통과 전까지 사용 안 함. relay(EC2 SDK 업로드)가 공식 경로 |

RabbitMQ 접속 정보, S3 버킷명, Callback origin, HMAC Secret, Catalog Token은 배포 단계에 비밀 채널로 전달 예정.
