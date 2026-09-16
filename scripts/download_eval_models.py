"""Phase 2 평가용 모델 다운로드.

- CLIP ViT-L/14 : 스타일 유사도 + aesthetic predictor 의 백본 (공용)
- LAION aesthetic predictor 헤드 : CLIP 임베딩 위의 선형 회귀 (1~10점)

    python scripts/download_eval_models.py
"""
from __future__ import annotations
import pathlib, urllib.request, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
CLIP_DIR = ROOT / "models" / "clip" / "clip-vit-large-patch14"
AES = ROOT / "models" / "aesthetic" / "sac+logos+ava1-l14-linearMSE.pth"
AES_URL = ("https://huggingface.co/camenduru/improved-aesthetic-predictor/"
           "resolve/main/sac%2Blogos%2Bava1-l14-linearMSE.pth")


def main() -> int:
    from huggingface_hub import snapshot_download

    if not (CLIP_DIR / "model.safetensors").exists():
        print("CLIP ViT-L/14 다운로드...")
        snapshot_download(
            "openai/clip-vit-large-patch14",
            local_dir=str(CLIP_DIR),
            allow_patterns=["*.json", "*.txt", "model.safetensors"],
        )
        print(f"  -> {CLIP_DIR}")
    else:
        print("CLIP ViT-L/14  이미 있음")

    if not AES.exists():
        print("aesthetic predictor 헤드 다운로드...")
        AES.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(AES_URL, AES)
        print(f"  -> {AES}  ({AES.stat().st_size/1048576:.1f} MB)")
    else:
        print("aesthetic predictor  이미 있음")

    print("\n=== 확인 ===")
    for f in sorted(CLIP_DIR.iterdir()):
        print(f"  {f.stat().st_size/1048576:8.1f} MB  clip/{f.name}")
    print(f"  {AES.stat().st_size/1048576:8.1f} MB  aesthetic/{AES.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
