"""FLUX.1 Kontext dev 편집 엔진 (범용).

입력 사진을 참조 잠재(ReferenceLatent)로 넣고 문장으로 "무엇을 바꿀지"만 지시한다.
구도·머리·옷·안경이 그대로 남는 것이 특징이라 웹툰(ani)·사계절(weather)·코스튬 안경이 같이 쓴다.
컬렉션별 문구·LoRA·크기는 manifests/<id>.yaml 이 갖고, 여기는 그래프와 호출만 있다.

  from engines.kontext import edit
  img = edit(src_path, prompt, width=896, height=1152, lora="ani_webtoon_kontext/v1_1500.safetensors")

LoRA 는 models/loras/ 기준 상대 경로. 파일이 없으면 LoRA 없이 돈다 (lora_available 로 확인).
"""
from __future__ import annotations

import pathlib

import numpy as np

from engines.comfy import DEFAULT_NODE, decode, stage, submit, unstage

ROOT = pathlib.Path(__file__).resolve().parents[1]
UNET = "flux1-dev-kontext_fp8_scaled.safetensors"
LORA_DIR = ROOT / "models" / "loras"


def lora_available(name: str | None) -> bool:
    return bool(name) and (LORA_DIR / name).exists()


def graph(img_name: str, prompt: str, tag: str, *, width: int = 896, height: int = 1152,
          guidance: float = 2.5, steps: int = 20, seed: int = 1000,
          lora: str | None = None, lora_strength: float = 1.0) -> dict:
    g = {
        "unet": {"class_type": "UNETLoader", "inputs": {"unet_name": UNET, "weight_dtype": "default"}},
        "clip": {"class_type": "DualCLIPLoader", "inputs": {"clip_name1": "t5xxl_fp16.safetensors",
                 "clip_name2": "clip_l.safetensors", "type": "flux", "device": "default"}},
        "vae": {"class_type": "VAELoader", "inputs": {"vae_name": "ae.safetensors"}},
        "img": {"class_type": "LoadImage", "inputs": {"image": img_name}},
        "scale": {"class_type": "FluxKontextImageScale", "inputs": {"image": ["img", 0]}},
        "enc": {"class_type": "VAEEncode", "inputs": {"pixels": ["scale", 0], "vae": ["vae", 0]}},
        "pos": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["clip", 0]}},
        "neg": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["clip", 0]}},
        "ref": {"class_type": "ReferenceLatent", "inputs": {"conditioning": ["pos", 0], "latent": ["enc", 0]}},
        "guid": {"class_type": "FluxGuidance", "inputs": {"conditioning": ["ref", 0], "guidance": guidance}},
        "lat": {"class_type": "EmptySD3LatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}},
        "sampler": {"class_type": "KSampler", "inputs": {"model": ["unet", 0], "positive": ["guid", 0],
                    "negative": ["neg", 0], "latent_image": ["lat", 0], "seed": seed, "steps": steps,
                    "cfg": 1.0, "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0}},
        "dec": {"class_type": "VAEDecode", "inputs": {"samples": ["sampler", 0], "vae": ["vae", 0]}},
        "save": {"class_type": "SaveImage", "inputs": {"images": ["dec", 0], "filename_prefix": "kontext/" + tag}},
    }
    if lora:
        g["lora"] = {"class_type": "LoraLoaderModelOnly",
                     "inputs": {"model": ["unet", 0], "lora_name": lora, "strength_model": lora_strength}}
        g["sampler"]["inputs"]["model"] = ["lora", 0]
    return g


def edit(src, prompt: str, *, width: int = 896, height: int = 1152, guidance: float = 2.5,
         steps: int = 20, seed: int = 1000, lora: str | None = None, lora_strength: float = 1.0,
         trigger: str | None = None, node: str = DEFAULT_NODE, tag: str = "edit") -> np.ndarray:
    """src(경로 또는 BGR 배열)를 편집해 BGR 배열로 돌려준다. LoRA 파일이 없으면 문장만으로 돈다."""
    use_lora = lora if lora_available(lora) else None
    text = f"{trigger} {prompt}" if (use_lora and trigger) else prompt
    name = stage(src, "_kx")
    try:
        data = submit(node, graph(name, text, tag, width=width, height=height, guidance=guidance,
                                  steps=steps, seed=seed, lora=use_lora, lora_strength=lora_strength))
    finally:
        unstage(name)
    return decode(data)
