"""환경변수 한 곳. 값은 백엔드 전달분(docs/BACKEND_CONTRACT.md 11절)을 .env 로 받는다. 비밀값은 저장소에 두지 않는다."""
from __future__ import annotations

import os
import socket


def env(name: str, default: str | None = None) -> str | None:
    return os.environ.get(name, default)


def flag(name: str, default: bool) -> bool:
    return env(name, "true" if default else "false").strip().lower() in ("1", "true", "yes", "on")


# RabbitMQ (전달서 4절) — 공유 큐라 기본은 소비 비활성. 전용 큐/Dispatcher 확정 뒤 켠다
CONSUMER_ENABLED = flag("AI_WORKER_CONSUMER_ENABLED", False)
RABBIT_URL = env("RABBIT_URL", "amqp://guest:guest@localhost:5672/%2F")
RABBIT_QUEUE = env("RABBIT_QUEUE", "motion.ai.generate.item")
RABBIT_EXCHANGE = env("RABBIT_EXCHANGE", "motion.ai")
RABBIT_ROUTING_KEY = env("RABBIT_ROUTING_KEY", "ai.generate.item")
RABBIT_RETRY_EXCHANGE = env("RABBIT_RETRY_EXCHANGE", "motion.retry.30s")   # claim BUSY 재발행
RABBIT_DECLARE = flag("RABBIT_DECLARE", False)                              # 로컬 시험에서만 큐를 직접 선언

# 백엔드 콜백 (전달서 5절). origin 은 고정 HTTPS, 경로는 /internal/v1/ai-generation-items/{itemId}/...
BACKEND_BASE_URL = env("BACKEND_BASE_URL", "http://localhost:8080")
BACKEND_HMAC_SECRET = env("BACKEND_HMAC_SECRET", "")
BACKEND_TIMEOUT = float(env("BACKEND_TIMEOUT_SEC", "15"))
BACKEND_RESEND = int(env("BACKEND_RESEND", "5"))              # Timeout/5xx 시 같은 콜백 재전송 횟수
HEARTBEAT_SEC = int(env("HEARTBEAT_SEC", "150"))              # lease 5분 -> 150초마다 /progress
WORKER_ID = env("WORKER_ID", f"ec2-worker-{socket.gethostname()}")[:100]

# 카탈로그 (전달서 3절) — Bearer 토큰
CATALOG_TOKEN = env("AI_PRESET_CATALOG_TOKEN", "")
HTTP_PORT = int(env("HTTP_PORT", "8090"))
HTTP_BIND = env("HTTP_BIND", "127.0.0.1")                    # Host 네트워크에서 loopback 에만 (백엔드 요구)

# S3 (전달서 7절)
ORIGINAL_BUCKET = env("ORIGINAL_BUCKET", "")
AI_PROCESSED_BUCKET = env("AI_PROCESSED_BUCKET", "")
AWS_REGION = env("AWS_REGION", "ap-northeast-2")
PRESIGN_TTL_SEC = int(env("PRESIGN_TTL_SEC", "1800"))
PREPROCESS_CACHE_S3 = flag("PREPROCESS_CACHE_S3", False)      # 권한·보유 정책 확정 전에는 메모리 캐시만

# GPU 모델 서버 (전달서 6절, 업체 중립). 비어 있으면 GPU 경로는 GPU_UNAVAILABLE 로 최종 실패
GPU_SERVICE_URL = env("GPU_SERVICE_URL", "")
GPU_AUTH_MODE = env("GPU_AUTH_MODE", "none")                  # none | bearer | gcp-id-token
GPU_AUTH_CREDENTIALS_FILE = env("GPU_AUTH_CREDENTIALS_FILE", "")   # bearer: 토큰 파일 / gcp: external_account JSON
GPU_CONNECT_TIMEOUT = float(env("GPU_CONNECT_TIMEOUT_SEC", "10"))
GPU_READ_TIMEOUT = float(env("GPU_READ_TIMEOUT_SEC", "630"))
GPU_UPLOAD_MODE = env("GPU_UPLOAD_MODE", "relay")             # relay: GPU 가 바이트를 돌려주고 EC2 가 SDK 로 업로드 / presigned: GPU 가 직접 PUT (POC 후)
GPU_IMAGE_ONLY = env("GPU_IMAGE_ONLY", "true").lower() == "true"   # GPU 서비스는 그림만, 누끼·인코딩·업로드는 워커(CPU). GPU 를 그림 전용으로 (단일 GPU 처리량↑)
CUTOUT_MAX_SIDE = int(env("CUTOUT_MAX_SIDE", "1200"))            # 이보다 큰 그림(웹툰 1792x2304)은 저해상도에서 누끼 뜨고 마스크만 확대
MAX_EXECUTIONS = 3                                            # 최초 포함 총 3회, 번호 0·1·2

# EC2 CPU 경로
CUTOUT_URL = env("CUTOUT_URL", "http://cutout:8001/cutout")
# 누끼가 GPU 서비스(/cutout, Bearer)로 갈 때: 토큰 문자열 또는 토큰 파일. 둘 다 비면 헤더 없이 부른다(EC2 컨테이너).
CUTOUT_TOKEN = env("CUTOUT_TOKEN", "")
CUTOUT_TOKEN_FILE = env("CUTOUT_TOKEN_FILE", "")
INSWAPPER_CTX = int(env("INSWAPPER_CTX", "-1"))
GLASSES_TO_GPU = flag("GLASSES_TO_GPU", True)                 # 안경 착용자의 직업·12지신은 Kontext 가 필요해 GPU 로
