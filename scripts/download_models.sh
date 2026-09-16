#!/usr/bin/env bash
# 모델 가중치 일괄 다운로드. 병렬 4스트림, 이미 있는 파일은 건너뛴다.
#
#   bash scripts/download_models.sh          # 생성에 필요한 것만
#   bash scripts/download_models.sh --eval   # 평가용까지
#
# FLUX.1-dev 는 게이트 저장소다. huggingface.co/black-forest-labs/FLUX.1-dev 에서
# 라이선스에 동의한 계정의 토큰이 필요하다:
#
#   export HF_TOKEN=hf_xxxx
#
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
M="$ROOT/models"
HF="https://huggingface.co"
WANT_EVAL="${1:-}"

# URL|저장경로
GEN=$(cat <<EOF
$HF/black-forest-labs/FLUX.1-dev/resolve/main/flux1-dev.safetensors|$M/unet/flux1-dev.safetensors
$HF/black-forest-labs/FLUX.1-dev/resolve/main/ae.safetensors|$M/vae/ae.safetensors
$HF/comfyanonymous/flux_text_encoders/resolve/main/t5xxl_fp16.safetensors|$M/clip/t5xxl_fp16.safetensors
$HF/comfyanonymous/flux_text_encoders/resolve/main/clip_l.safetensors|$M/clip/clip_l.safetensors
$HF/guozinan/PuLID/resolve/main/pulid_flux_v0.9.1.safetensors|$M/pulid/pulid_flux_v0.9.1.safetensors
$HF/DIAMONIK7777/antelopev2/resolve/main/scrfd_10g_bnkps.onnx|$M/insightface/models/antelopev2/scrfd_10g_bnkps.onnx
$HF/DIAMONIK7777/antelopev2/resolve/main/glintr100.onnx|$M/insightface/models/antelopev2/glintr100.onnx
$HF/DIAMONIK7777/antelopev2/resolve/main/genderage.onnx|$M/insightface/models/antelopev2/genderage.onnx
$HF/DIAMONIK7777/antelopev2/resolve/main/1k3d68.onnx|$M/insightface/models/antelopev2/1k3d68.onnx
$HF/DIAMONIK7777/antelopev2/resolve/main/2d106det.onnx|$M/insightface/models/antelopev2/2d106det.onnx
EOF
)

# 채점에만 쓴다. 생성에는 필요 없다.
EVAL=$(cat <<EOF
$HF/openai/clip-vit-large-patch14/resolve/main/model.safetensors|$M/clip/clip-vit-large-patch14/model.safetensors
$HF/openai/clip-vit-large-patch14/resolve/main/config.json|$M/clip/clip-vit-large-patch14/config.json
$HF/openai/clip-vit-large-patch14/resolve/main/preprocessor_config.json|$M/clip/clip-vit-large-patch14/preprocessor_config.json
$HF/openai/clip-vit-large-patch14/resolve/main/tokenizer.json|$M/clip/clip-vit-large-patch14/tokenizer.json
$HF/openai/clip-vit-large-patch14/resolve/main/tokenizer_config.json|$M/clip/clip-vit-large-patch14/tokenizer_config.json
$HF/openai/clip-vit-large-patch14/resolve/main/vocab.json|$M/clip/clip-vit-large-patch14/vocab.json
$HF/openai/clip-vit-large-patch14/resolve/main/merges.txt|$M/clip/clip-vit-large-patch14/merges.txt
$HF/openai/clip-vit-large-patch14/resolve/main/special_tokens_map.json|$M/clip/clip-vit-large-patch14/special_tokens_map.json
$HF/camenduru/improved-aesthetic-predictor/resolve/main/sac+logos+ava1-l14-linearMSE.pth|$M/aesthetic/sac+logos+ava1-l14-linearMSE.pth
$HF/Kim2091/UltraSharp/resolve/main/4x-UltraSharp.pth|$M/upscale_models/4x-UltraSharp.pth
EOF
)

fetch() {
  local url="${1%%|*}" dst="${1##*|}"
  if [ -s "$dst" ]; then echo "SKIP  $(basename "$dst")"; return 0; fi
  mkdir -p "$(dirname "$dst")"
  local auth=()
  [ -n "${HF_TOKEN:-}" ] && auth=(-H "Authorization: Bearer $HF_TOKEN")
  if curl -fL --retry 5 --retry-delay 3 -C - -s "${auth[@]}" -o "$dst.part" "$url"; then
    mv "$dst.part" "$dst"
    echo "OK    $(basename "$dst")  $(du -h "$dst" | cut -f1)"
  else
    rm -f "$dst.part"
    echo "FAIL  $(basename "$dst")  <- $url"
    return 1
  fi
}
export -f fetch

MANIFEST="$GEN"
[ "$WANT_EVAL" = "--eval" ] && MANIFEST="$GEN
$EVAL"

echo "$MANIFEST" | xargs -P 4 -I{} bash -c 'fetch "$@"' _ {}

echo
echo "=== 결과 ==="
find "$M" -type f \( -name '*.safetensors' -o -name '*.pth' -o -name '*.onnx' \) \
  -printf '%10s  %p\n' 2>/dev/null | sed "s|$M/||" | sort -k2
echo "총 용량: $(du -sh "$M" 2>/dev/null | cut -f1)"
echo
echo "FLUX 가 FAIL 이면 HF_TOKEN 을 설정하고 라이선스에 동의했는지 확인하세요:"
echo "  $HF/black-forest-labs/FLUX.1-dev"
