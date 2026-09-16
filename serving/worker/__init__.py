"""EC2 워커 (컨테이너).

  RabbitMQ 수신 -> 백엔드 /claim -> 프리사인 URL -> [EC2 CPU 생성 | Cloud Run /generate] -> /complete|/fail -> ACK

  config.py     환경변수
  backend.py    claim / progress / complete / fail (HMAC)
  cloudrun.py   Cloud Run 호출 (GCP ID 토큰, 재시도)
  cpu_path.py   직업·12지신을 EC2 안에서 생성 (inswapper + cutout-cpu)
  consumer.py   메시지 루프, 하트비트, ACK 규칙
  http_api.py   GET /collections, GET /health (GPU 를 깨우지 않는 경량 조회)
  __main__.py   진입점

  python -m serving.worker
"""
