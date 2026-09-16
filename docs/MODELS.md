# 모델 목록

`models/` 하위에 배치한다. ComfyUI 는 `extra_model_paths.yaml` 로 이 경로를 참조한다.
기계가 읽는 목록은 [`models/registry.yaml`](../models/registry.yaml) 이다 (파일·용량·출처·라이선스·엔진). 이 문서는 사람용 설명.
용량이 크고 라이선스가 제각각이므로 저장소에는 포함하지 않는다.

## 현재 스택 (명세서 기준)

| 용도 | 파일 | 배치 | 용량 | 출처 |
|---|---|---|---|---|
| 베이스 | `flux1-dev.safetensors` | `unet/` | 22.17GB | [black-forest-labs/FLUX.1-dev](https://huggingface.co/black-forest-labs/FLUX.1-dev) (게이트) |
| 텍스트 인코더 | `t5xxl_fp16.safetensors` | `clip/` | 9.79GB | [comfyanonymous/flux_text_encoders](https://huggingface.co/comfyanonymous/flux_text_encoders) |
| 텍스트 인코더 | `clip_l.safetensors` | `clip/` | 0.25GB | 〃 |
| VAE | `ae.safetensors` | `vae/` | 0.33GB | [black-forest-labs/FLUX.1-dev](https://huggingface.co/black-forest-labs/FLUX.1-dev) (게이트) |
| 정체성 | `pulid_flux_v0.9.1.safetensors` | `pulid/` | 1.14GB | [guozinan/PuLID](https://huggingface.co/guozinan/PuLID) |
| 얼굴 검출/임베딩/성별 | antelopev2 (`*.onnx`) | `insightface/models/antelopev2/` | 0.36GB | [DIAMONIK7777/antelopev2](https://huggingface.co/DIAMONIK7777/antelopev2) |
| 이미지 편집 | `flux1-dev-kontext_fp8_scaled.safetensors` | `unet/` | 11.9GB | [Comfy-Org/flux1-kontext-dev_ComfyUI](https://huggingface.co/Comfy-Org/flux1-kontext-dev_ComfyUI) |
| 웹툰풍 LoRA (자체 학습) | `ani_webtoon_kontext/v1_1500.safetensors` | `loras/` | ~0.2GB | `train/ani_lora` 7쌍으로 ai-toolkit 학습, 1500스텝 |

`bash scripts/download_models.sh` 로 일괄 다운로드한다.
FLUX 는 게이트 저장소라 라이선스 동의 계정의 `HF_TOKEN` 이 필요하다.

L40S 48GB 한 장에 전부 올라간다 (생성 중 여유 44GB 관측).

### 명세서와 다른 점

| 명세서 | 실제 |
|---|---|
| `banodoco/ComfyUI-PuLID-Flux` | 404. `lldacing/ComfyUI_PuLID_Flux_ll` 를 썼다 |
| `pulid_flux_v1` | 실제 파일명은 `pulid_flux_v0.9.1.safetensors` |
| t5xxl fp8 | fp16 을 썼다. L40S 48GB 이면 fp8 로 아낄 이유가 없다 |

> FLUX.1-dev 는 **비상업 라이선스**. 본 프로젝트는 비상업 용도이므로 사용 가능.
> 용도가 바뀌면 FLUX.1-schnell(Apache-2.0) 또는 SDXL 계열로 전환해야 한다.
> 가중치를 받으려면 HuggingFace 에서 라이선스에 동의한 계정의 토큰이 필요하다.

## 평가용 (생성에는 쓰지 않는다)

`bash scripts/download_models.sh --eval` 로 함께 받는다.

| 용도 | 모델 | 배치 | 용량 | 출처 |
|---|---|---|---|---|
| 정체성 점수 | antelopev2 `glintr100` (ArcFace) | `insightface/` | 위와 공유 | 위와 동일 |
| 화풍 점수 | `clip-vit-large-patch14` | `clip/` | 1.7GB | [openai/clip-vit-large-patch14](https://huggingface.co/openai/clip-vit-large-patch14) |
| 심미 점수 | LAION aesthetic predictor (MLP 헤드) | `aesthetic/` | 0.01GB | [camenduru/improved-aesthetic-predictor](https://huggingface.co/camenduru/improved-aesthetic-predictor) |
| 업스케일 | `4x-UltraSharp.pth` | `upscale_models/` | 0.07GB | [Kim2091/UltraSharp](https://huggingface.co/Kim2091/UltraSharp) |

## 제거한 모델 (1번 모델 — 고흐·할로윈)

2026-09-01 에 코드까지 전부 지웠다. 가중치는 그 전에 이미 지운 상태였다(25.58GB).
되살리려면 아래 표를 보고 다시 받고, 코드는 git 에서 꺼낸다:

    git show 1d044af:workflows/base/build.py > workflows/base/build.py

### 함께 지운 것
`workflows/base/*` (SDXL 그래프 5개), `data/styles/*/style.yaml`,
리전 마스크 파이프라인(`generate_masks`, `bisenet_model`, `build_denoise_map`,
`generate_depth`, `metrics/region`, BiSeNet 가중치),
옛 포토카드 흐름(`gen_photocards`, `finish_photocard`, `expand_canvas`, `face_detail`),
옛 평가 하네스(`evaluate`, `grid`, `naming`, `per_image`, `report`, `sanity_check`),
SDXL 벤치마크 3종.

### 가중치 출처

SDXL 노선을 접으면서 지웠다. 되살릴 이유가 생기면 여기를 보고 다시 받으면 된다.

| 모델 | 용도였던 것 |
|---|---|
| SDXL Base 1.0 / RealVisXL V5.0 fp16 | 베이스 |
| ControlNet SDXL depth / canny | 구조 고정 |
| InstantID (ip-adapter + ControlNet) | 정체성 보존 |
| IP-Adapter SDXL | 스타일 주입 |
| 고흐 LoRA | 화풍 |
| BiSeNet face-parsing (옛 리전 마스크) | 리전 마스크 용도는 버림. **facexlib 의 BiSeNet 은 살아 있다** — 안경 판별(`common/glasses.py`)과 5.weather 입력 피부 리터치(`common/retouch.py`)의 얼굴 파싱에 쓴다 |

FLUX + PuLID 가 정체성과 피부 질감 양쪽에서 앞서고, 얼굴만 쓰기로 하면서
구조 제어(ControlNet)와 리전 마스크가 모두 불필요해졌다.
