# EC2 워커 이미지 (CPU). RabbitMQ 소비 · 직업/12지신 얼굴 교체 · GPU 서버 호출 · 백엔드 콜백 · GET /collections.
#
#   docker build -f serving/worker/Dockerfile -t portrait-worker:v1 .      (저장소 루트에서)
#
# InsightFace 두 팩과 inswapper_128(약 1.2GB)은 빌드 중 scripts/fetch_worker_models.sh 가 받는다 — 저장소에 가중치가 없다.
# 빌드 뒤 verify_models.py 가 models/registry.yaml 의 sha256 과 대조해 다르면 빌드가 실패한다.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 OMP_NUM_THREADS=2

RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 libgomp1 curl build-essential cmake \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY serving/worker/requirements.txt /app/serving/worker/requirements.txt
# torch CPU 는 BiSeNet(안경 판별) 때문에만 필요하다.
# insightface==0.7.3 은 소스 빌드라 컴파일러(build-essential)와 numpy·cython 이 먼저 있어야 한다.
# torch·torchvision 을 CPU 휠로 함께 고정한다. torchvision 을 빼면 facexlib 이 CUDA torch(수 GB)를 끌어온다.
RUN pip install torch==2.7.1 torchvision==0.22.1 --index-url https://download.pytorch.org/whl/cpu \
 && pip install "numpy<3" cython \
 && pip install -r /app/serving/worker/requirements.txt \
 && python -c "from facexlib.parsing import init_parsing_model; init_parsing_model(model_name='bisenet', device='cpu')"

COPY scripts/fetch_worker_models.sh scripts/verify_models.py /app/scripts/
COPY models/registry.yaml /app/models/registry.yaml
RUN bash /app/scripts/fetch_worker_models.sh /app/models/insightface \
 && python /app/scripts/verify_models.py --root /app/models --only insightface-antelopev2 insightface-buffalo_l inswapper-128

COPY engines /app/engines
COPY steps /app/steps
COPY manifests /app/manifests
COPY serving/common /app/serving/common
COPY serving/worker /app/serving/worker
COPY data/reference/jobs /app/data/reference/jobs
COPY data/reference/zodiac /app/data/reference/zodiac
RUN touch /app/serving/__init__.py && rm -rf /app/manifests/backlog

EXPOSE 8090
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s CMD curl -fs http://127.0.0.1:8090/health || exit 1
CMD ["python", "-m", "serving.worker"]
