# BiRefNet-portrait background removal service (CPU only)
# Build:  docker build -t motion-cutout:1.0 .
# Run:    docker run -d --name cutout -p 8001:8001 --memory 4g --cpus 2 --restart unless-stopped motion-cutout:1.0
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    # rembg looks for <name>.onnx in this directory before downloading anything
    U2NET_HOME=/models \
    OMP_NUM_THREADS=4

RUN apt-get update \
 && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

# model file is copied into the image so the container never needs internet
COPY models/birefnet-portrait.onnx /models/birefnet-portrait.onnx
COPY app/ ./app/

# fail the build if rembg would try to download instead of using the bundled file
RUN python -c "from app.engine import load_session; s = load_session(); print('model ok:', s.model_path)"

EXPOSE 8001
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s CMD curl -fs http://localhost:8001/health || exit 1
CMD ["uvicorn", "app.server:app", "--host", "0.0.0.0", "--port", "8001", "--workers", "1"]
