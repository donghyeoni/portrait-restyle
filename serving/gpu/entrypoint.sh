#!/usr/bin/env bash
# GPU 모델 서버 진입점. 가중치 볼륨(MODELS_DIR)을 코드가 기대하는 자리에 연결하고 서비스를 띄운다.
set -eu
: "${MODELS_DIR:=/models}"
: "${COMFY_DIR:=/app/ComfyUI}"

# 우리 코드는 <repo>/models 를 본다 (engines.inswapper, steps.upscale, steps.faces)
ln -sfn "${MODELS_DIR}" /app/models
# PuLID 노드는 ComfyUI/models/insightface 를 직접 본다
mkdir -p "${COMFY_DIR}/models"
ln -sfn "${MODELS_DIR}/insightface" "${COMFY_DIR}/models/insightface"
# EVA-CLIP(PuLID) 은 ComfyUI/models/clip 에서 찾는다 -> extra_model_paths 의 clip 이 MODELS_DIR/clip 을 가리키므로 그곳에 둔다

if [ ! -d "${MODELS_DIR}/unet" ]; then
  echo "경고: ${MODELS_DIR}/unet 이 없다. 가중치 볼륨 마운트를 확인하라" >&2
fi

cd /app
exec python3 -m serving.gpu.service
