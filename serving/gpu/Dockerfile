# GPU 모델 서버 이미지 (업체 중립). 코드·ComfyUI·런타임만 들어간다 — 가중치(약 40GB)는 읽기 전용 볼륨(MODELS_DIR)에서 읽는다.
# CUDA 12.8 + torch cu128: Blackwell(sm_120)까지 지원. GPU 환경은 백엔드 선정 후 확정.
#
#   docker build -f serving/gpu/Dockerfile -t portrait-gpu:v1 .   (저장소 루트에서)
#
FROM nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04

ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_BREAK_SYSTEM_PACKAGES=1 \
    MODELS_DIR=/models COMFY_DIR=/app/ComfyUI COMFY_PORT=8189 PORT=8080 \
    U2NET_HOME=/models/rembg

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-pip python3-venv git curl libgl1 libglib2.0-0 libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# torch (cu128, Blackwell 지원) 먼저 — 레이어 캐시
RUN pip install torch==2.7.1 torchvision==0.22.1 --index-url https://download.pytorch.org/whl/cu128

# ComfyUI + PuLID 노드 (버전 고정: 실측한 조합)
ARG COMFY_REF=v0.3.44
ARG PULID_NODE_REF=main
RUN git clone --depth 1 --branch ${COMFY_REF} https://github.com/comfyanonymous/ComfyUI.git ${COMFY_DIR} \
 && pip install -r ${COMFY_DIR}/requirements.txt \
 && git clone --depth 1 --branch ${PULID_NODE_REF} https://github.com/lldacing/ComfyUI_PuLID_Flux_ll.git ${COMFY_DIR}/custom_nodes/ComfyUI_PuLID_Flux_ll \
 && pip install -r ${COMFY_DIR}/custom_nodes/ComfyUI_PuLID_Flux_ll/requirements.txt

COPY serving/gpu/requirements.txt /app/serving/gpu/requirements.txt
RUN pip install -r /app/serving/gpu/requirements.txt

# facexlib(BiSeNet 안경 판별) 가중치는 패키지 폴더에 받는다 — 빌드 시 미리 받아 런타임 인터넷 의존 제거
RUN python3 -c "from facexlib.parsing import init_parsing_model; init_parsing_model(model_name='bisenet', device='cpu')"

# 우리 코드 + 코스튬 참고 이미지(약 13MB). 가중치·결과물은 포함하지 않는다
COPY engines /app/engines
COPY steps /app/steps
COPY manifests /app/manifests
COPY serving/common /app/serving/common
COPY serving/gpu /app/serving/gpu
COPY data/reference/jobs /app/data/reference/jobs
COPY data/reference/zodiac /app/data/reference/zodiac
RUN touch /app/serving/__init__.py

# ComfyUI 가 우리 models/ 를 보게 하는 링크는 entrypoint 가 MODELS_DIR 로 건다
COPY serving/gpu/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

EXPOSE 8080
ENTRYPOINT ["/app/entrypoint.sh"]
