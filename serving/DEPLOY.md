# EC2 워커 배포 안내 (백엔드 전달용)

2026-09-08 · 백엔드 실서버 확인 결과 반영. 규약은 [docs/BACKEND_CONTRACT.md](../docs/BACKEND_CONTRACT.md).

## 1. 무엇을 올리나

| 컨테이너 | 이미지 | 역할 | 네트워크 |
|---|---|---|---|
| `<ai-worker>` | `portrait-worker:v1` | RabbitMQ 소비(기본 비활성), `/collections`, 코스튬 CPU 생성, GPU 서버 호출, S3 업로드, 백엔드 콜백 | host, `127.0.0.1:8090` |
| `motion-cutout` | `motion-cutout:1.0` | BiRefNet-portrait CPU 누끼 | host, `127.0.0.1:8001` |

GPU 모델 서버(`serving/gpu/`)는 GPU 환경 선정 후 별도 배포. 그 전까지 워커의 `GPU_SERVICE_URL`은 비워 두고, GPU 아이템은 `GPU_UNAVAILABLE`로 최종 실패한다.

## 2. 경로 (백엔드 확정)

| 용도 | 경로 |
|---|---|
| compose·빌드 컨텍스트 | `/opt/<project>/ai-worker/` — 저장소를 그대로 clone/copy (`serving/docker-compose.yml`을 여기서 실행) |
| 환경변수 | `/etc/<project>/ai-worker/worker.env` — `serving/worker/.env.example`을 복사해 `<...>` 채움 |
| 인증 파일 | `/etc/<project>/ai-worker/secrets/` — 컨테이너에 `/secrets` 읽기 전용으로 마운트 |

## 3. 빌드

AI 팀 로컬에는 Docker가 없어 **이미지는 EC2(Docker 29.1.3 / Compose 2.40.3)에서 빌드**해야 한다. 빌드 뒤 digest를 알려 주면 규약 11절에 기록한다.

```bash
cd /opt/<project>/ai-worker                       # 저장소 루트
docker build -f serving/worker/Dockerfile -t portrait-worker:v1 .    # 약 2GB, 10~15분. 빌드 중 InsightFace·inswapper 1.2GB 다운로드
docker build -t motion-cutout:1.0 serving/cutout-cpu                # BiRefNet onnx(973MB)를 serving/cutout-cpu/models/ 에 먼저 둔다
docker images --digests | grep -E "portrait-worker|motion-cutout"
```

- 워커 빌드는 `scripts/fetch_worker_models.sh`가 InsightFace 두 팩과 `inswapper_128.onnx`를 받고, `scripts/verify_models.py`가 `models/registry.yaml`의 sha256과 대조한다. 불일치면 빌드 실패.
- 저장소에 가중치·비밀값은 없다. `manifests/backlog/`는 이미지에서 제거된다.

## 4. 실행과 스모크 테스트 (Consumer 끈 상태)

```bash
cp serving/worker/.env.example /etc/<project>/ai-worker/worker.env     # <...> 채우기. AI_WORKER_CONSUMER_ENABLED=false 유지
cd /opt/<project>/ai-worker && docker compose -f serving/docker-compose.yml up -d
```

| 확인 | 명령 | 기대 |
|---|---|---|
| 워커 생존 | `curl -s http://127.0.0.1:8090/health` | `{"status":"ok","workerId":…,"consumerEnabled":false}` |
| 카탈로그 인증 | `curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8090/collections` | `401` |
| 카탈로그 | `curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8090/collections` | `catalogVersion`, 23 items, rarity 포함, `ETag` 헤더 |
| ETag | `curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $TOKEN" -H 'If-None-Match: "<catalogVersion>"' …/collections` | `304` |
| 누끼 | `curl -s -F "file=@photo.jpg" http://127.0.0.1:8001/cutout -o out.png` | RGBA PNG |
| RabbitMQ 접속 | 워커 로그에 연결 오류 없음 (소비는 안 함) | |
| S3 | 워커 컨테이너에서 `aws s3api head-object --bucket $AI_PROCESSED_BUCKET --key <아무 키>` 가 403 이 아닌지 | 권한 확인 |
| 콜백 | Nginx `/internal/v1/ai-generation-items/**` 프록시 보완 완료 후, 존재하지 않는 itemId 로 `/claim` 호출 → 서명 통과·404 | HMAC 규격 확인 |

## 5. E2E → Consumer 활성화

1. 백엔드가 테스트 아이템(DOCTOR 1건, 안경 없는 원본)을 발행 → 워커에 `AI_WORKER_CONSUMER_ENABLED=true` 로 재시작 → claim → complete → S3 HeadObject native sha256 확인 → ACK.
2. GPU 환경이 준비되면 `GPU_SERVICE_URL`·`GPU_AUTH_MODE`·`GPU_AUTH_CREDENTIALS_FILE` 채우고 CYBERPUNK·WEBTOON·안경 착용자 DOCTOR 1건씩.
3. 전용 큐 또는 Dispatcher 전환과 함께 상시 활성.

## 5-1. 알파 테스트

알파 기간의 GPU 구성(개발 GPU 서버 + SSH 역터널), 양쪽 준비 항목, 스모크·E2E 절차, 판정 기준은 [docs/ALPHA_TEST.md](../docs/ALPHA_TEST.md) 에 따로 정리했다.

## 6. 환경변수 전체 목록

| 변수 | 기본 | 설명 |
|---|---|---|
| `AI_WORKER_CONSUMER_ENABLED` | `false` | RabbitMQ 소비 여부. `/collections`는 항상 서빙 |
| `RABBIT_URL` | — | `amqp://<ai-worker>:<pw>@127.0.0.1:5672/%2F<VHOST>` |
| `RABBIT_QUEUE` / `RABBIT_EXCHANGE` / `RABBIT_ROUTING_KEY` / `RABBIT_RETRY_EXCHANGE` | `motion.ai.generate.item` / `motion.ai` / `ai.generate.item` / `motion.retry.30s` | 백엔드 선언값. 워커는 선언 안 함 (`RABBIT_DECLARE=false`) |
| `BACKEND_BASE_URL` | — | `https://<BACKEND_HOST>` (프록시 보완 확인 후) |
| `BACKEND_HMAC_SECRET` | — | 비밀 채널 |
| `BACKEND_TIMEOUT_SEC` / `BACKEND_RESEND` | `15` / `5` | 콜백 타임아웃, Timeout·5xx 재전송 횟수 |
| `HEARTBEAT_SEC` | `150` | `/progress` 주기 (lease 5분) |
| `WORKER_ID` | 호스트명 기반 | `[A-Za-z0-9][A-Za-z0-9._:-]{0,99}` |
| `AI_PRESET_CATALOG_TOKEN` | — | `/collections` Bearer. 비밀 채널 |
| `HTTP_BIND` / `HTTP_PORT` | `127.0.0.1` / `8090` | 카탈로그·헬스 |
| `AWS_REGION` | `ap-northeast-2` | |
| `ORIGINAL_BUCKET` / `AI_PROCESSED_BUCKET` | 확정값 (.env.example) | |
| `AWS_SHARED_CREDENTIALS_FILE` / `AWS_PROFILE` | 미설정 | 워커 전용 최소권한 자격 증명을 파일로 받을 때. 인스턴스 역할이면 불필요 |
| `PRESIGN_TTL_SEC` | `1800` | 프리사인 URL 유효시간 |
| `PREPROCESS_CACHE_S3` | `false` | 전처리 캐시 S3 저장 (권한·정책 확정 후) |
| `GPU_SERVICE_URL` | 빈 값 | GPU 모델 서버. 비면 GPU 경로 최종 실패 |
| `GPU_AUTH_MODE` / `GPU_AUTH_CREDENTIALS_FILE` | `none` / 빈 값 | `bearer`(토큰 파일) / `gcp-id-token`(external_account JSON) |
| `GPU_CONNECT_TIMEOUT_SEC` / `GPU_READ_TIMEOUT_SEC` | `10` / `630` | |
| `GPU_UPLOAD_MODE` | `relay` | 공식 경로. `presigned`는 S3 서명 PUT POC 후 |
| `GPU_IMAGE_ONLY` | `true` | GPU 서비스는 그림만 반환, 누끼·인코딩·업로드는 워커(CPU). GPU 를 그림 전용으로 |
| `CUTOUT_MAX_SIDE` | `1200` | 이보다 큰 그림(웹툰)은 저해상도에서 누끼 뜨고 마스크만 확대 |
| `GLASSES_TO_GPU` | `true` | 안경 착용자 코스튬을 GPU 로 |
| `CUTOUT_URL` | `http://127.0.0.1:8001/cutout` | 누끼 컨테이너 |
| `INSWAPPER_CTX` | `-1` | inswapper 장치 (-1 CPU) |

## 7. AI 팀 → 백엔드 산출물

| 항목 | 위치 |
|---|---|
| compose | `serving/docker-compose.yml` |
| 환경변수 목록·예시 | 이 문서 6절, `serving/worker/.env.example` |
| 가중치 목록·sha256 | `models/registry.yaml` (개발 서버 실측) |
| 프리셋 코드 표 | `manifests/catalog.json` (= `GET /collections`) |
| 이미지 digest | EC2 빌드 후 기록 (AI 팀 로컬에 Docker 없음) |
| API 예제·테스트 결과 | `docs/BACKEND_CONTRACT.md` 6·7·11절, `docs/DECISIONS.md` |
