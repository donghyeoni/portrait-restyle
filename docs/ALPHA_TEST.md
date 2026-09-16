# 알파 테스트 계획 — AI 카드 생성 (portrait-restyle × 백엔드)

작성 2026-09-08 · 갱신 2026-09-10 · 상태: 가동 중 (E2E 1차 통과) · 규약 원본은 [BACKEND_CONTRACT.md](BACKEND_CONTRACT.md) v4 (이 문서는 그 위에서 알파 기간의 구성·절차만 정한다)

## 1. 목적

정식 GPU 서버를 들이기 전에, **실제 백엔드·RabbitMQ·S3·EC2 워커**와 **개발 GPU 서버**를 이어 사용자 사진 1장 → 카드 23장이 끝까지 만들어지는지
확인한다. 결과가 쓸 만하면 정식 GPU 환경(후보: AWS EC2 g6e.xlarge 서울)을 정해 옮긴다. 옮길 때 바뀌는 것은 워커의 `GPU_SERVICE_URL` 하나다.

## 2. 알파 구성 (실측, 2026-09-10)

계획은 EC2 워커 + 역터널이었으나, 실제 알파는 **워커·누끼·GPU 서비스를 모두 개발 GPU 서버에** 올리고 RabbitMQ 만 EC2 에서 끌어오는 구성으로 가동됐다.
개발 GPU 서버는 인바운드가 없지만 22/443 아웃바운드는 되므로, 서버가 EC2 로 `ssh -L 5672:127.0.0.1:5672` 정방향 터널을 열면 워커가 `127.0.0.1:5672` 로 큐를 읽고
콜백(`https://<BACKEND_HOST>`)과 S3 업로드는 443 으로 직접 나간다. 역터널·EC2 컨테이너·EC2 SSH 사용자가 모두 불필요해졌다.

```
프론트 → 백엔드 → RabbitMQ (motion.ai.generate.item, EC2 127.0.0.1:5672)
                                          ▲ ssh -L 5672 (motion-tunnel@<BACKEND_HOST>)
   ┌──────────────────────────────────────┴──────────────────────────────────┐
   │ 개발 GPU 서버 (TLJH, L40S device 1)                                        │
   │  serving.worker (gpu-worker-01) :8090 ── 127.0.0.1:5672 큐 소비           │
   │    직업·12지신: CPU inswapper ── 누끼 serving/cutout-cpu :8001 (BiRefNet CPU) │
   │    컨셉·웹툰: → serving.gpu.service :8080 (Bearer) → ComfyUI :8189          │
   │  S3 SDK 업로드(443) · 백엔드 콜백 HMAC(443)                                 │
   └─────────────────────────────────────────────────────────────────────────┘
```

| 자리 | 알파에서 | 정식에서 |
|---|---|---|
| GPU 모델 서버 | 개발 GPU 서버 `serving.gpu.service` (가중치 상주, 비용 0) | EC2 g6e 등, 같은 HTTP 계약 |
| 워커·누끼 | 같은 서버의 프로세스 (`python -m serving.worker`, `uvicorn app.server` 8001) | EC2 컨테이너 (`serving/DEPLOY.md`) 또는 GPU 서버 동거 |
| 큐 | 공유 큐, `AI_WORKER_CONSUMER_ENABLED=true` (E2E 에서 문제 없음) | 전용 큐 또는 Dispatcher |
| 큐 접속 | 정방향 SSH 터널 `-L 5672` (EC2 `motion-tunnel` 사용자) | RabbitMQ 를 워커가 있는 망에서 직접 |
| S3 업로드 | relay (서버 SDK, native SHA-256) | 동일 |

워커 환경: `GPU_SERVICE_URL=http://127.0.0.1:8080`, `GPU_AUTH_MODE=bearer`, `GPU_AUTH_CREDENTIALS_FILE=~/portrait-restyle/secrets/gpu-token`, `GPU_UPLOAD_MODE=relay`, `GLASSES_TO_GPU=false`, `INSWAPPER_CTX=-1`(CPU), `WORKER_ID=gpu-worker-01`.

## 3. 범위

| 포함 | 제외 |
|---|---|
| 23 프리셋 (직업 8·12지신 12·컨셉 2·웹툰 1), 성별 2, 안경 착용자 | 계절·컨셉 4종(백로그), Gemini 프리셋 |
| 3 출력(IMAGE·CUTOUT·THUMBNAIL), 2 출력, 관리자 SUBJECT_MASK | 서명 PUT 업로드 |
| claim/progress/complete/fail, 재시도 3회, BUSY·종결 disposition | 다중 워커, GPU 동시성 2 이상 |
| `/collections` Bearer·ETag | 자동 배치 취소(NO_FACE 시) |

## 4. 준비 — 누가 무엇을

| 쪽 | 항목 | 상태 |
|---|---|---|
| 백엔드 | `<ai-worker>` 큐 읽기 권한·비밀번호 재발급, HMAC Secret, Catalog Token (비밀 채널) | 준비 중 |
| 백엔드 | Nginx `/internal/v1/ai-generation-items/**` 프록시 보완 | 완료 (9/10 외부 확인: HMAC·타임스탬프 검증 동작) |
| 백엔드 | 워커 전용 S3 최소권한 인증 (역할 또는 자격 증명 파일) | 준비 중 |
| 백엔드 | EC2 에서 `portrait-worker:v1`·`motion-cutout:1.0` 빌드, `/etc/<project>/ai-worker/worker.env` 작성, compose 기동 (Consumer 끔) | `serving/DEPLOY.md` |
| 백엔드 | EC2 터널 전용 SSH 사용자 + AI 팀 공개키 등록 | 요청 |
| AI 팀 | 개발 GPU 서버 상시 서비스·터널 운용 (서버 권한은 AI 팀에 있음) | 서비스 가동 (9/10) |
| AI 팀 | `GPU_SERVICE_TOKEN` 발급 → 비밀 채널 → EC2 `/etc/<project>/ai-worker/secrets/gpu-token` | 발급 완료 (9/10), 전달 대기 |
| AI 팀 | GPU 서버에서 `serving/gpu/run_tljh.sh start`, `serving/gpu/tunnel.sh start` | keeper 가동 (9/10). 터널은 백엔드 SSH 사용자 뒤 |
| AI 팀 | 테스트 원본 사진 세트: 남·여, 안경 있음·없음, 전신 사진 1장 (얼굴 작음) | 준비됨 (`data/input`) |

워커 `.env` 알파 값: `GPU_SERVICE_URL=http://127.0.0.1:18080`, `GPU_AUTH_MODE=bearer`, `GPU_AUTH_CREDENTIALS_FILE=/secrets/gpu-token`, `GPU_UPLOAD_MODE=relay`, `AI_WORKER_CONSUMER_ENABLED=false`(시작 시).

## 5. 절차

### 5.1 스모크 (Consumer 끔, 하루)

| # | 확인 | 방법 | 기대 |
|---|---|---|---|
| S1 | 워커·누끼 기동 | EC2 `curl 127.0.0.1:8090/health`, `127.0.0.1:8001/health` | 200 |
| S2 | 카탈로그 | 토큰 없음 → 401, 있음 → 200 + 23 items + rarity + ETag, If-None-Match → 304 | 백엔드가 5분 주기 갱신 성공 |
| S3 | RabbitMQ 접속 | 워커 로그에 연결 오류 없음 (소비는 안 함) | |
| S4 | S3 권한 | 워커 컨테이너에서 결과 버킷 HeadObject / 테스트 PutObject | 403 없음, HeadObject 에 ChecksumSHA256 |
| S5 | 콜백 HMAC | 없는 itemId 로 `/claim` → 404 (서명은 통과) | 401 이 아님 |
| S6 | 터널 | EC2 `curl 127.0.0.1:18080/health` | `comfyReady: true` |
| S7 | GPU 직접 호출 | EC2 에서 `/generate` 1건 (returnBytes) | 200, outputs 3개, 체크섬 일치. GPU 서버 내부 선검증 9/10: CYBERPUNK 37.4s(모델 로드 11.7s 포함), 3 출력 체크섬 일치 |

### 5.2 E2E (Consumer 켬, 하루)

백엔드가 테스트 사용자 1명(안경 없음)으로 아이템을 발행한다. 워커는 `AI_WORKER_CONSUMER_ENABLED=true` 로 재시작.

| # | 케이스 | 기대 |
|---|---|---|
| E1 | DOCTOR (CPU) | claim → progress → complete → ACK. S3 3파일, HeadObject native SHA-256 = 콜백 값 |
| E2 | CYBERPUNK (GPU, 터널) | 15~40초 + 첫 건 모델 로드. complete |
| E3 | WEBTOON | 1792×2304, complete |
| E4 | 사용자 1명 전체 23건 | 전부 complete, 백엔드 Batch 집계 완료. 총 소요 기록 |
| E5 | 안경 착용자 DOCTOR | GPU 경로(Kontext 안경), 착용 상태로 complete |
| E6 | 전신 사진(얼굴 작음) CYBERPUNK | 정체성 유지 (품질 확인) |
| E7 | 같은 아이템 재발행 | 재생성 없이 complete (멱등) |
| E8 | 모르는 코드(Gemini 프리셋) 수신 | 워커 소비 중단·로그 (공유 큐 검증). 백엔드가 Dispatcher/전용 큐 판단 |
| E9 | GPU 터널 끊김 중 GPU 아이템 | 3회 재시도 후 `/fail MODEL_ERROR attempt 2`, 하트비트 유지 |
| E10 | 잘못된 modelVersion | `/fail VERSION_MISMATCH attempt 0` |
| E11 | 콜백 5xx 유발(백엔드 협조) | 같은 callbackEventId 로 재전송, ACK 보류 |

### 5.3 관찰 (일주일)

실사용자 10~30명. 기록: 아이템 성공률, 사용자당 총 시간, GPU 콜드스타트 빈도, 터널 끊김 횟수, 관리자 정리로 서비스가 내려간 횟수, 품질 불만(안경·정체성·누끼).

### 5.4 E2E 실측 (2026-09-10 13:14 ~ 14:34, 사용자 1명 · 안경 없음)

25 아이템 모두 claim → complete, fail 0. 워커 로그 기준 소요(누끼·S3 업로드·콜백 포함).

| 구분 | 건수 | 아이템당 | 비고 |
|---|---|---|---|
| 직업·12지신 (CPU inswapper) | 20 | 18~24초 | BiRefNet CUDA OOM → CPU 대체가 매번 발생 (GPU1 은 FLUX 상주) |
| CYBERPUNK / VAMPIRE (PuLID) | 4 | 27~34초 | 첫 건 모델 로드 포함 |
| WEBTOON (Kontext + 2x) | 1 | 49초 | 1792×2304 |
| 사용자 1명 23장 총 시간 | | **7분 51초** (14:25:45 → 14:33:36) | 직렬 처리 (prefetch 1, 워커 1) |

판정 대비: GPU 3장 103초(통과, 2분 이내). CPU 20장 6분 30초(**미달**, 목표 3분). 개선 후보는 8절.
확인된 것: 백엔드 콜백 HMAC 실키 검증 통과, S3 업로드·체크섬 콜백 정상(fail 없음), 공유 큐에서 모르는 메시지 없음(E8 미발생), 서비스 Bearer 인증 동작(무토큰 401 1건 기록).
미확인: E5(안경 착용자), E6(전신), E7(멱등 재발행), E9(터널 끊김), E10, E11.

## 6. 판정 기준

| 지표 | 통과 |
|---|---|
| 아이템 성공률 | ≥ 98% (실패는 원인 분류) |
| 사용자당 23장 총 시간 | CPU 20장 3분 이내, GPU 3장 2분 이내(콜드스타트 제외) |
| 콜백 정합 | `/complete` 체크섬 = S3 native SHA-256 100% |
| 멱등·재시도 | E7·E9·E11 통과 |
| 품질 | 안경 착용자 안경 유지, 정체성 육안 확인, 누끼 가장자리 이상 없음 |

통과하면 정식 GPU 환경을 정하고 `GPU_SERVICE_URL` 만 바꿔 이전한다. 미달 항목은 원인별로 규약 또는 모델 수정.

## 7. 알려진 한계·위험

| 위험 | 대응 |
|---|---|
| 개발 GPU 서버는 공유 서버 — 다른 사용자·재부팅으로 프로세스가 내려갈 수 있음 | `run_tljh.sh` 가 60초마다 ComfyUI·서비스를 복구. **워커·누끼·터널은 아직 nohup 단독 실행** → 감시 대상에 추가 필요 |
| RabbitMQ 정방향 터널(`ssh -L 5672`)이 단일 프로세스, 재접속 루프 없음 | 끊기면 워커는 10초마다 재연결을 시도하지만 터널이 없어 계속 실패. `tunnel.sh` 를 `-L` 모드로 바꿔 루프 감시로 전환 필요 |
| GPU 1장(우리 할당 device 1)·동시성 1 | 사용자 겹치면 GPU 아이템 대기. 알파 규모(≤30명)에서는 허용 |
| 공유 큐에 다른 메시지 | 워커가 소비 중단. E8 로 확인 뒤 전용 큐 전환 |
| S3 서명 PUT 미검증 | relay 로 우회. GPU→EC2 base64 전달(장당 1~2MB) |
| BiRefNet CUDA 메모리 부족(GPU1 에 FLUX 상주) | CPU 자동 대체가 매번 발생 (장당 +3~6초). `CUTOUT_PROVIDERS=cpu` 로 시도 자체를 생략하거나 빈 GPU 지정 검토 |

## 8. 종료 조건과 정리

- CPU 20장 속도: 프로파일 결과 아이템 19초 중 누끼(BiRefNet CPU) 14.6초. 참고 이미지 사전 마스크(`steps/refmask.py`, `scripts/precompute_masks.py`)로 누끼 모델을 없앰 -> 약 4초/장 예상. 워커 재시작 후 5.4 재측정.
- 다음 후보(순서대로): 워커 2~3개 병렬(CPU 아이템), IMAGE·CUTOUT webp 무손실 -> q95(−0.9초/장, 파일 4배 축소, 백엔드 통보), 컨셉·웹툰 누끼를 빈 GPU 로, PuLID/Kontext 스텝·fp8·캐시 노드는 품질 A/B 후.
- 종료: 6절 판정 후. 결과·측정값을 DECISIONS.md 와 규약 7절(시간·자원)에 반영.
- 정리: `tunnel.sh stop`, `run_tljh.sh stop`, EC2 터널 사용자 삭제, `GPU_SERVICE_TOKEN` 폐기. 워커·누끼 컨테이너는 정식 GPU 로 URL 만 바꿔 유지.
