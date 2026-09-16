# 서비스 구조

2026-09-08 기준 (백엔드 전달서 반영). 규약은 [BACKEND_CONTRACT.md](BACKEND_CONTRACT.md), 결정 이력은 [DECISIONS.md](DECISIONS.md).

## 1. 한 장 그림

```
프론트 ──▶ 백엔드(Spring) ──publish──▶ RabbitMQ  motion.ai / ai.generate.item / motion.ai.generate.item
                 ▲                            │                         (공유 큐 — 전용 큐 확정 전 소비 비활성)
                 │ /claim /progress            ▼ consume (prefetch 1)
                 │ /complete /fail (HMAC)  ┌──────────────────────────────┐
                 │ GET /collections (Bearer)│ EC2  worker 컨테이너          │
                 └─────────────────────────┤  · stylePreset 으로 분기        │
                                           │  · 직업·12지신: inswapper(CPU) ├──▶ cutout-cpu 컨테이너 (8001)
                                           │  · 컨셉·웹툰·안경 착용자 코스튬 │
                                           │      → GPU 서버 /generate       ├──▶ S3 (boto3, native SHA-256)
                                           │  · /collections (127.0.0.1:8090)│
                                           └───────────────┬──────────────┘
                                                           │ HTTPS (인증 방식 미정), 프리사인 GET 동봉, 결과 바이트 반환(relay)
                                                           ▼
                                           ┌──────────────────────────────┐
                                           │ GPU 모델 서버 (환경 미정)       │
                                           │  ComfyUI(자식) + pulid/kontext │
                                           │  + inswapper + BiRefNet 누끼   │
                                           │  가중치: 읽기 전용 볼륨         │
                                           └──────────────────────────────┘
```

## 2. 자리별 역할

| 자리 | 컴포넌트 | 하는 일 | 코드 |
|---|---|---|---|
| EC2 (host network) | `worker` | 큐 소비 → `/claim`(disposition) → 생성 또는 GPU 호출(하트비트 150초, 총 3회 실행) → S3 SDK 업로드 → `/complete`·`/fail` → ACK. `GET /collections` 127.0.0.1:8090 | `serving/worker/`, 배포 `serving/DEPLOY.md` |
| EC2 | `cutout-cpu` | BiRefNet-portrait CPU 누끼 (EC2 생성분) | `serving/cutout-cpu/` |
| GPU 환경 (미정) | GPU 모델 서버 | `/generate` 동기: 원본 GET → 전처리 → 엔진 → 누끼(CUDA) → 썸네일 → 결과 바이트 반환(또는 프리사인 PUT) | `serving/gpu/` |
| 가중치 저장소 (미정) | 읽기 전용 볼륨 | 약 40GB, 목록·버전·체크섬은 `models/registry.yaml` | |
| AWS | S3 | 원본 `users/{u}/ai-sources/{id}/`, 결과는 `outputTargets` 그대로 | |
| TLJH GPU 서버 | 개발·튜닝 전용 | 데이터셋 생성(`runner.py`), A/B, LoRA 학습, 서비스 코드 통합 시험 | `scripts/remote/` |

## 3. 처리 위치 분기

| 조건 | 위치 | 이유 |
|---|---|---|
| 직업·12지신, 안경 없음 | EC2 CPU | inswapper 는 CPU 1초/장. GPU 대기 없이 먼저 도착 |
| 직업·12지신, **안경 착용** | GPU | 원본 안경을 Kontext 로 그려 넣어야 함(장당 20초). GPU 없으면 `GPU_UNAVAILABLE` 최종 실패 |
| 컨셉(cyberpunk, vampire) | GPU | FLUX.1-dev + PuLID |
| 웹툰 | GPU | Kontext + LoRA + 2배 업스케일 |

안경 여부는 첫 아이템에서 판별해 프로세스 메모리에 캐시하고, GPU 요청의 `preprocess` 힌트로 넘긴다.

## 4. 사용자 한 명(아이템 23개)의 시간

| 경로 | 장수 | 시간 |
|---|---|---|
| EC2: 직업 8 + 12지신 12 (누끼 CPU 3~6초 포함) | 20 | 약 2~3분 (순차) |
| GPU: 컨셉 2 + 웹툰 1 | 3 | 약 1분 30초 + 콜드스타트 |
| 안경 착용자: 코스튬 20장이 GPU 로 | 20 | +7분 |

## 5. 네트워크·인증

| 구간 | 방식 |
|---|---|
| EC2 → RabbitMQ | AMQP (백엔드 전달 계정) |
| EC2 → 백엔드 콜백 | HTTPS, HMAC-SHA256 (`X-AI-Timestamp`·`X-AI-Nonce`·`X-AI-Signature v1=`·`X-Trace-Id`·`X-Idempotency-Key`) |
| 백엔드 → EC2 `/collections` | Host 127.0.0.1:8090, Bearer 토큰 |
| EC2 → S3 | boto3, EC2 IAM 역할 (키 없음), native SHA-256 업로드 |
| EC2 → GPU 서버 | HTTPS. `GPU_AUTH_MODE` none / bearer / gcp-id-token 준비, 환경 확정 후 선택 |
| GPU 서버 → S3 | 기본 없음(relay). POC 뒤 프리사인 PUT |

GPU 서버는 RabbitMQ 계정·백엔드 HMAC·AWS 장기 키를 갖지 않는다.

## 6. 이미지와 가중치

- 이미지에는 코드·런타임만. 가중치(게이트 라이선스 FLUX·Kontext, 실존 인물 LoRA)는 이미지·공개 레지스트리에 넣지 않는다.
- GPU 이미지: `nvidia/cuda:12.8` + torch 2.7.1 cu128 + ComfyUI + PuLID 노드 + 우리 코드 + 코스튬 참고 이미지 13MB (약 8~10GB).
- EC2 워커 이미지: python 3.12 + torch CPU(BiSeNet) + InsightFace·inswapper 1.2GB (약 2GB).

## 7. 운영 규칙

- **버전**: `modelVersion` 파이프라인, `promptTemplateVersion` 컬렉션, `catalogVersion` 코드 표 해시. 메시지의 버전과 워커 매니페스트가 다르면 `VERSION_MISMATCH`.
- **프리셋 추가 = 매니페스트 yaml 한 장** → 워커 재배포 → `catalogVersion` 변경 → 백엔드 재조회. 새 collection 이름·새 executionTarget 은 백엔드 허용값 검토 필요.
- **가중치 교체** = 볼륨 파일 교체 + `registry.yaml`. LoRA 는 버전 파일명(`v1_1500`)으로 롤백 가능.
- **로컬 전용**: `manifests/backlog/`, `data/backlog/`.

## 8. 남은 일

1. 백엔드: RabbitMQ 접속·S3 버킷·콜백 origin·HMAC secret·Catalog token 전달, 전용 큐 전환, S3 서명 PUT POC
2. 공동: GPU 업체·장비·가중치 저장소·레지스트리·인증 선정 → 가중치 업로드, 이미지 빌드·배포, 콜드스타트·VRAM 실측
3. 우리: `models/registry.yaml` 체크섬 채우기, SUBJECT_MASK 내용·WEBTOON rarity 확인 반영, 실환경 E2E(CPU 1건·GPU 1건·WEBTOON·안경)
