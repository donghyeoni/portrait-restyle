#!/usr/bin/env bash
# EC2 워커 이미지 빌드 시 InsightFace 두 팩과 inswapper_128 을 받는다 (약 1.2GB). 저장소에는 가중치가 없다.
# 대상: $1 (기본 /app/models/insightface)
#   antelopev2/   검출·임베딩·안경 판별   https://huggingface.co/DIAMONIK7777/antelopev2
#   buffalo_l/    inswapper 임베딩 공간   https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip
#   inswapper_128.onnx                    https://huggingface.co/ezioruan/inswapper_128.onnx
# 받은 뒤 models/registry.yaml 의 sha256 과 대조하는 것은 verify_models.py 가 한다.
set -euo pipefail
DEST="${1:-/app/models/insightface}"
mkdir -p "$DEST/models/antelopev2" "$DEST/models/buffalo_l"

fetch() { # url dest
  if [ ! -s "$2" ]; then
    echo "  get $(basename "$2")"; curl -fsSL --retry 3 -o "$2" "$1"
  fi
}
A=https://huggingface.co/DIAMONIK7777/antelopev2/resolve/main
for f in 1k3d68.onnx 2d106det.onnx genderage.onnx glintr100.onnx scrfd_10g_bnkps.onnx; do
  fetch "$A/$f" "$DEST/models/antelopev2/$f"
done
if [ ! -s "$DEST/models/buffalo_l/w600k_r50.onnx" ]; then
  echo "  get buffalo_l.zip"
  curl -fsSL --retry 3 -o /tmp/buffalo_l.zip https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip
  python3 -c "import zipfile; zipfile.ZipFile('/tmp/buffalo_l.zip').extractall('$DEST/models/buffalo_l')"
  rm -f /tmp/buffalo_l.zip
fi
fetch https://huggingface.co/ezioruan/inswapper_128.onnx/resolve/main/inswapper_128.onnx "$DEST/inswapper_128.onnx"
echo "models ready: $(du -sh "$DEST" | cut -f1)"
