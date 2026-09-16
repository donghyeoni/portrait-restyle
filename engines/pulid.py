"""FLUX.1-dev + PuLID 워크플로우 그래프 (명세서 기준).

증명사진에서 얼굴 임베딩만 추출해 FLUX 로 컨셉 이미지를 생성한다.
의상·배경·조명은 프롬프트로 새로 만들고, 얼굴 정체성은 PuLID 가 유지한다.

노드 규격은 서버의 /object_info 로 실제 확인한 것이다:
  PulidFluxModelLoader   -> PULIDFLUX
  PulidFluxEvaClipLoader -> EVA_CLIP
  PulidFluxInsightFaceLoader(provider) -> FACEANALYSIS
  ApplyPulidFlux(model, pulid_flux, eva_clip, face_analysis, image,
                 weight, start_at, end_at) -> MODEL

명세서 8절 튜닝 파라미터:
  sampler euler / scheduler simple
  CFG(FluxGuidance) 3.0~3.5  — FLUX 는 높으면 왜곡된다
  steps 20~25
  PuLID weight 0.8~1.0
"""
from __future__ import annotations

import pathlib
from dataclasses import dataclass

import yaml

# 화풍 프리셋. 사용자가 style 로 고른다.
#
# subject 는 성별에 따라 갈린다. 요구사항 b(의상)를 지키려면 남/여 복식을
# 구분해야 하고, 남성 입력이 여성적으로 그려지는 문제도 여기서 막는다.
# scene(배경) 과 quality(촬영 특성) 는 성별과 무관하므로 공유한다.
# 네거티브 프롬프트는 FLUX 에서 아무 일도 하지 않는다.
#
# FLUX.1-dev 는 guidance distilled 라 KSampler 의 cfg 가 1.0 이어야 하는데,
# cfg=1.0 이면 classifier-free guidance 가 꺼지고 네거티브 조건이 쓰이지 않는다.
# 같은 시드로 (기본 / 빈 문자열 / 정반대) 세 가지 네거티브를 넣고 재보니
# **픽셀 해시가 셋 다 동일했다**. "person, human face, portrait, man" 처럼
# 결과를 뒤집을 법한 네거티브를 넣어도 이미지가 한 비트도 안 바뀐다.
#
# 그래서 예전 네거티브에 있던 의도는 전부 포지티브로 옮겼다:
#   plastic skin, airbrushed  -> "detailed skin texture, visible skin pores"
#   id photo, business suit   -> 의상·배경을 포지티브에서 통째로 지정
#   cartoon, 3d render        -> "photorealistic"
#   low cut, cleavage         -> "modest high neckline, fully covered shoulders"
NEGATIVE = ""   # 비워 둔다. 채워도 효과가 없고 채워 두면 착각을 부른다

# 프리셋 문구는 manifests/concept.yaml 에 있다 (컬렉션 = 설정 파일). 여기서는 읽어서 조립만 한다.
#   common:  quality / modest / neck / glasses  — 공용 구절. subject 문구 안의 {MODEST} {NECK} 자리에 들어간다
#   presets: 코드 -> {key, subject_male, subject_female, scene, params, params_male, params_female, enabled}
# 각 구절을 그렇게 정한 실측 근거는 yaml 주석과 docs/DECISIONS.md 에 있다.
_ROOT = pathlib.Path(__file__).resolve().parents[1]
MANIFEST = _ROOT / "manifests" / "concept.yaml"


def _load():
    m = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    common = {k.upper(): v for k, v in m["common"].items()}
    styles: dict[str, dict] = {}
    for code, p in m["presets"].items():
        styles[p["key"]] = {
            "code": code,
            "enabled": p.get("enabled", True),
            "subject_male": p["subject_male"].format(**common),
            "subject_female": p["subject_female"].format(**common),
            "scene": p["scene"],
            "negative": NEGATIVE,
            "params": p.get("params", {}),
            "params_male": p.get("params_male", {}),
            "params_female": p.get("params_female", {}),
        }
    return styles, common


STYLES, _COMMON = _load()
QUALITY = _COMMON["QUALITY"]
MODEST = _COMMON["MODEST"]
NECK = _COMMON["NECK"]
GLASSES = _COMMON["GLASSES"]


@dataclass
class FluxPulidConfig:
    face_image: str                      # ComfyUI/input 기준 파일명
    positive: str
    negative: str = ""
    unet: str = "flux1-dev.safetensors"
    clip_l: str = "clip_l.safetensors"
    t5: str = "t5xxl_fp16.safetensors"
    vae: str = "ae.safetensors"
    pulid: str = "pulid_flux_v0.9.1.safetensors"

    width: int = 896
    height: int = 1152
    steps: int = 22                      # 명세서 20~25
    # vampire 6명 스윕(2026-09-01)에서 정한 값. pulid_weight 와 짝이다 —
    # weight 를 올리면 배경 대비가 죽어서 guidance 로 되살린다.
    guidance: float = 3.2                # 4.0 은 5종을 인형처럼 만들었다 (2026-09-03 스윕)
    sampler: str = "euler"               # 명세서 권장
    scheduler: str = "simple"            # 명세서 권장
    denoise: float = 1.0
    seed: int = 0

    # 노드 허용 범위는 0~5.0 이고 명세서의 0.8~1.0 은 권장값일 뿐이다.
    # 실측(vampire, 6명): 1.0 -> 0.8343, 1.3 -> 0.8507, 1.6 -> 0.7663, 2.0 -> 0.3928.
    # 1.3 과 1.6 사이에 절벽이 있다. 0.9 에서는 얼굴이 원본보다 갸름해졌다.
    pulid_weight: float = 1.0            # 1.3 은 질감을 지우고 정체성도 낮았다 (커버·화풍 스윕 공통)
    pulid_start: float = 0.0
    pulid_end: float = 1.0
    face_provider: str = "CUDA"

    # fp8 은 VRAM 을 아끼지만 품질이 조금 떨어진다.
    # L40S 46GB 면 default(bf16)로 충분하다.
    weight_dtype: str = "default"
    # 4개 ComfyUI 인스턴스가 output 디렉터리를 공유한다. 접두사가 같으면
    # 서로 다른 노드의 동시 작업이 같은 out_000NN_.png 카운터를 잡아
    # 한쪽이 다른 쪽을 덮어쓴다 — 실제로 A/B 결과 두 건이 바이트 동일해졌다.
    # 요청마다 고유 접두사를 준다.
    filename_prefix: str = "flux_pulid/out"


def build_graph(face_image: str, prompt: str = "", style: str = "vampire",
                seed: int | None = None, gender: str = "male",
                glasses: bool = False, **kw) -> dict:
    """API 서버에서 호출하는 진입점.

    gender 는 InsightFace genderage 로 판별한 값("male"/"female")을 받는다.
    복식·호칭이 성별에 따라 갈리므로 프리셋에서 subject 를 골라 쓴다.
    """
    preset = STYLES.get(style, STYLES["vampire"])
    subject = preset["subject_female" if gender == "female" else "subject_male"]
    parts = [subject, preset["scene"], QUALITY]
    if glasses:
        parts.insert(1, GLASSES)  # 주체 바로 뒤
    if prompt:
        parts.insert(1, prompt)   # 사용자 추가 프롬프트는 주체 바로 뒤에
    # 화풍이 생성 파라미터를 지정할 수 있다.
    # params_male / params_female 로 성별에 따라 갈 수도 있다 —
    # 예: 갓은 남성에만 있으므로 pulid_start 를 늦추는 것도 남성에만 필요하다.
    # 우선순위: 호출자 kw > 성별별 params > 공통 params
    params = {**preset.get("params", {}),
              **preset.get(f"params_{gender}", {}),
              **kw}
    cfg = FluxPulidConfig(face_image=face_image, positive=", ".join(parts),
                          negative=preset["negative"],
                          seed=seed if seed is not None else 0, **params)
    return build(cfg)


def build(c: FluxPulidConfig) -> dict:
    g: dict = {}

    g["unet"] = {"class_type": "UNETLoader",
                 "inputs": {"unet_name": c.unet, "weight_dtype": c.weight_dtype}}
    g["clip"] = {"class_type": "DualCLIPLoader",
                 "inputs": {"clip_name1": c.t5, "clip_name2": c.clip_l,
                            "type": "flux", "device": "default"}}
    g["vae"] = {"class_type": "VAELoader", "inputs": {"vae_name": c.vae}}

    # --- PuLID: 얼굴 임베딩을 FLUX 모델에 주입 ---
    g["pulid_model"] = {"class_type": "PulidFluxModelLoader",
                        "inputs": {"pulid_file": c.pulid}}
    g["eva"] = {"class_type": "PulidFluxEvaClipLoader", "inputs": {}}
    g["face"] = {"class_type": "PulidFluxInsightFaceLoader",
                 "inputs": {"provider": c.face_provider}}
    g["src"] = {"class_type": "LoadImage", "inputs": {"image": c.face_image}}
    g["apply"] = {"class_type": "ApplyPulidFlux",
                  "inputs": {"model": ["unet", 0],
                             "pulid_flux": ["pulid_model", 0],
                             "eva_clip": ["eva", 0],
                             "face_analysis": ["face", 0],
                             "image": ["src", 0],
                             "weight": c.pulid_weight,
                             "start_at": c.pulid_start,
                             "end_at": c.pulid_end}}

    # --- 조건 ---
    g["pos"] = {"class_type": "CLIPTextEncode",
                "inputs": {"text": c.positive, "clip": ["clip", 0]}}
    g["neg"] = {"class_type": "CLIPTextEncode",
                "inputs": {"text": c.negative, "clip": ["clip", 0]}}
    # FLUX 는 CFG 대신 guidance 임베딩을 쓴다
    g["guid"] = {"class_type": "FluxGuidance",
                 "inputs": {"conditioning": ["pos", 0], "guidance": c.guidance}}

    g["latent"] = {"class_type": "EmptySD3LatentImage",
                   "inputs": {"width": c.width, "height": c.height, "batch_size": 1}}

    g["sampler"] = {"class_type": "KSampler",
                    "inputs": {"seed": c.seed, "steps": c.steps,
                               "cfg": 1.0,               # FLUX 는 1.0 고정
                               "sampler_name": c.sampler, "scheduler": c.scheduler,
                               "denoise": c.denoise,
                               "model": ["apply", 0],
                               "positive": ["guid", 0], "negative": ["neg", 0],
                               "latent_image": ["latent", 0]}}
    g["decode"] = {"class_type": "VAEDecode",
                   "inputs": {"samples": ["sampler", 0], "vae": ["vae", 0]}}
    g["save"] = {"class_type": "SaveImage",
                 "inputs": {"filename_prefix": c.filename_prefix,
                            "images": ["decode", 0]}}
    return g
