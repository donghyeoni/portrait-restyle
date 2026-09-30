# 카드 생성 도구 (로컬 전용): 모니터 · 실험실

운영 서비스에 배포하지 않고 **이 PC 에서만** 도는 모델 개발용 관리 화면. 생성 항목을 찾아
**원본 사진 → AI 결과 → 누끼 → 완성 카드**와 실행 기록·실패 원인·소요 시간을 본다.

- 운영 DB 는 EC2 의 Postgres 컨테이너 안에서 `psql` 을 **READ ONLY 트랜잭션**으로 돌려 읽는다. 쓰기는 하지 않는다.
- 이미지는 EC2 에서 이미 S3 를 쓰는 워커 컨테이너가 자기 자격으로 읽어 SSH 로 넘긴다. **S3·DB 자격 증명은 이 PC 로 가져오지 않는다.**
- 받은 이미지는 메모리에만 5분 두고 디스크에 쓰지 않는다.
- 회원 원본 사진을 열 때마다 `logs/media-access.log` 에 한 줄(JSON)씩 남긴다.
- 서버는 `127.0.0.1` 에만 붙고, 다른 사이트에서 오는 요청(Host·Origin 이 로컬이 아닌 것)은 거절한다.

## 준비

1. `local.example.json` 을 `local.json` 으로 복사해 채운다. `local.json` 은 git 에 올라가지 않는다.
   - `ec2.host`, `ec2.user`, `ec2.key` — EC2 SSH 접속 정보와 개인키 경로
   - `postgresContainer` — Postgres 컨테이너 이름
   - `s3Container` — S3 를 쓰는 워커 컨테이너 이름(환경에 `ORIGINAL_BUCKET`·`AI_PROCESSED_BUCKET` 이 있어야 한다)
   - `teamFrontend` (선택) — 서비스 프런트 저장소의 `frontend` 폴더. 완성 카드 미리보기에 쓴다
2. `ssh` 가 PATH 에 있어야 한다(Windows 10+ 기본 OpenSSH). 파이썬은 표준 라이브러리만 쓴다.

## 실행

```bash
.venv/Scripts/python tools/admin-local/server.py
```

`http://127.0.0.1:8765` 을 연다. `--port` 로 바꿀 수 있다.

## 완성 카드 미리보기 (선택)

완성 카드는 서비스 서버에 저장되지 않고 앱이 화면에서 합성한다. 같은 모습을 보려면 서비스 프런트의
`CardFace` 를 이 도구용으로 한 번 빌드한다. 팀 저장소의 `node_modules` 를 쓰며, 결과(`static/cardface/`)는 팀 코드라 git 에 올리지 않는다.

```bash
node tools/admin-local/cardface/build.mjs
```

빌드하지 않으면 카드 칸만 비어 있고 나머지는 그대로 쓸 수 있다. 서비스 카드 디자인이 바뀌면 다시 빌드한다.

## 화면

| 영역 | 내용 |
|---|---|
| 요약 | 최근 N시간 프리셋·등급·실행 위치(CPU/GPU/Gemini)별 요청·완료율·실패, 평균 대기·평균 생성·p90. 실패 코드 순위(누르면 목록이 좁혀진다) |
| 목록 | 회원(닉네임·번호), 상태, 등급, 종류, 프리셋, 실패 코드, 기간으로 거른다 |
| 상세 | 단계별 이미지 4칸, 실행 회차별 대기·실행 막대, 실패 이벤트, 모델·프롬프트 버전·시드 |

단계별(받기·생성·올리기) 시간은 서비스 DB 에 기록되지 않아 여기서는 실행 전체 시간만 보인다.

## 실험실

테스트 사진(또는 모니터 항목의 회원 원본)으로 프리셋을 **파라미터를 바꿔** 돌리고 단계별 결과를 본다.
운영 GPU 서비스·워커·ComfyUI 설정은 건드리지 않는다. GPU 서버의 JupyterHub 로 실행기(`lab/gpu_lab.py`)를
올려 백그라운드 프로세스로 돌리고, 결과 파일을 받아 이 PC 의 `lab-runs/` 에 둔다(git 에 올리지 않는다).

| 단계 | 내용 |
|---|---|
| 사진 분석 | 운영과 같은 antelopev2 검출·안경 판별. 얼굴 박스·랜드마크를 그린 이미지, 얼굴 높이 비율, 자동 성별 |
| 중간 스텝 | PuLID·Kontext 는 같은 시드로 N 스텝에서 멈춘 실제 중간 상태(KSamplerAdvanced). 스텝마다 다시 그려 시간이 늘어난다 |
| 결과 · 누끼 · 카드 | AI 결과, BiRefNet 누끼, 서비스 CardFace 로 합성한 카드 |
| 지표 | 원본 대비 얼굴 유사도(antelopev2), 단계별 시간, 실제로 적용된 값과 프롬프트 |

엔진별로 바꿀 수 있는 값: PuLID(시드·스텝·guidance·PuLID weight/시작/끝·안경 문구·추가 프롬프트),
Kontext(시드·스텝·guidance·LoRA 강도/사용·업스케일·프롬프트), 얼굴 교체(앞머리·크롭·GFPGAN·얼굴만 교체).
실행 기록은 두 개 이상 체크하면 결과와 값 차이를 나란히 비교한다. 모니터 상세의 **이 사진으로 실험**은
그 항목의 회원 원본을 받아 GPU 서버로 올린다(열람 기록이 남는다).

운영과 다른 점: 얼굴 분석·GFPGAN·누끼·업스케일은 CPU 에서 돈다(GPU 는 ComfyUI 만). 그래서 업스케일은
4x-UltraSharp 대신 Lanczos 이고, 얼굴 교체의 누끼는 사전 계산 마스크 대신 BiRefNet 이다. 안경 보정(Kontext)은 끈다.

### 준비

1. GPU 서버의 JupyterHub 에서 **Token** 페이지(`/hub/token`)로 API 토큰을 만든다.
2. `local.json` 의 `jupyter.url`(사용자 서버 주소)과 `jupyter.token` 을 채운다.
3. `gpu` 에 서버 경로를 넣는다: `codeRoot`(파이프라인 코드), `pylib`(추가 라이브러리, 선택), `comfyInput`(ComfyUI input 폴더),
   `node`(실험에 쓸 ComfyUI 주소 — 운영과 같이 쓰면 그만큼 운영이 기다린다), `python`(파이프라인 venv 의 python).

## 파일

- `server.py` — 로컬 HTTP 서버와 API
- `queries.py` — 운영 DB 에 던지는 SQL(값은 모두 형식을 확인한 것만 넣는다)
- `remote.py` — SSH 로 psql 실행, 워커 컨테이너로 S3 객체 받기
- `static/` — 화면(프레임워크 없이 HTML·CSS·JS)
- `cardface/` — 완성 카드 미리보기 빌드
- `lab.py` · `jupyter.py` — 실험실 프리셋·기록, GPU 서버 JupyterHub 연결
- `lab/gpu_lab.py` — GPU 서버에서 도는 실험 실행기
