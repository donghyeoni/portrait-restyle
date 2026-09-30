"""실험실 실행기. GPU 서버에서 돈다 (로컬 도구가 올려 두고 백그라운드로 실행한다).

    python gpu_lab.py <run_dir>

<run_dir>/request.json 과 <run_dir>/input.png 를 읽어 카드 한 장을 만들고, 단계마다 결과를 파일로 남긴다.
운영 서비스와 같은 엔진 함수를 쓰되 파라미터를 바꿀 수 있고 중간 결과를 남긴다. 운영 코드·설정은 건드리지 않는다.

request.json:
  stylePreset   프리셋 코드 (예: CYBERPUNK)
  gender        male | female | auto
  params        엔진별 조절값 (아래 PARAM_KEYS)
  captureSteps  디퓨전 중간 스텝 수 (0 이면 안 남김)
  env           {codeRoot, pylib, comfyInput, node} — 서버 경로. 로컬 설정에서 온다
남기는 파일: status.json(진행), analysis.png, step_XX.png, result.png, cutout.png, reference.png, result.json
"""
from __future__ import annotations

import copy
import json
import os
import pathlib
import sys
import time
import traceback

RUN = pathlib.Path(sys.argv[1]).resolve()
REQ = json.loads((RUN / "request.json").read_text(encoding="utf-8"))
ENV = REQ["env"]
os.environ["CUDA_VISIBLE_DEVICES"] = ""          # 얼굴 분석·GFPGAN·누끼는 CPU. GPU 는 ComfyUI 만 쓴다
os.environ["CUTOUT_PROVIDERS"] = "cpu"
CODE = pathlib.Path(os.path.expanduser(ENV["codeRoot"]))
sys.path[:0] = [str(CODE)] + ([os.path.expanduser(ENV["pylib"])] if ENV.get("pylib") else [])
os.chdir(CODE)

import cv2  # noqa: E402
import numpy as np  # noqa: E402

NODE = ENV["node"]
T0 = time.time()
TIMINGS: list[dict] = []


def status(stage: str, **extra):
    (RUN / "status.json").write_text(json.dumps({"stage": stage, "elapsedMs": int((time.time() - T0) * 1000), **extra},
                                                ensure_ascii=False), encoding="utf-8")


class Timer:
    def __init__(self, name: str, label: str):
        self.name, self.label = name, label

    def __enter__(self):
        status(self.name)
        self.t = time.time()
        return self

    def __exit__(self, *exc):
        TIMINGS.append({"stage": self.name, "label": self.label, "ms": int((time.time() - self.t) * 1000)})


def write(name: str, img) -> str:
    cv2.imwrite(str(RUN / name), img)
    return name


def advanced(graph: dict, end: int, total: int) -> dict:
    """KSampler 를 KSamplerAdvanced 로 바꿔 end 스텝에서 멈춘다. 같은 시드라 최종 결과의 실제 중간 상태다."""
    g = copy.deepcopy(graph)
    for node in g.values():
        if node["class_type"] == "KSampler":
            i = node["inputs"]
            node["class_type"] = "KSamplerAdvanced"
            node["inputs"] = {
                "model": i["model"], "positive": i["positive"], "negative": i["negative"], "latent_image": i["latent_image"],
                "add_noise": "enable", "noise_seed": i["seed"], "steps": total, "cfg": i["cfg"],
                "sampler_name": i["sampler_name"], "scheduler": i["scheduler"], "start_at_step": 0, "end_at_step": end,
                "return_with_leftover_noise": "enable" if end < total else "disable",
            }
            return g
    raise RuntimeError("KSampler 가 없는 그래프")


def capture_points(total: int, count: int) -> list[int]:
    if count <= 0 or total <= 1:
        return []
    points = sorted({max(1, round(total * (i + 1) / (count + 1))) for i in range(count)})
    return [p for p in points if p < total]


def diffusion(graph: dict, total: int, submit, decode) -> tuple[np.ndarray, list[dict]]:
    steps = []
    for k in capture_points(total, int(REQ.get("captureSteps", 0))):
        with Timer(f"step_{k:02d}", f"중간 스텝 {k}/{total}"):
            img = decode(submit(NODE, advanced(graph, k, total)))
        steps.append({"step": k, "total": total, "file": write(f"step_{k:02d}.png", img)})
    with Timer("generate", f"생성 {total}스텝"):
        final = decode(submit(NODE, graph))
    return final, steps


def main():
    import manifests
    from serving.common import preprocess as P
    from steps.faces import biggest, face_app
    import engines.comfy as comfy
    if ENV.get("comfyInput"):
        comfy.COMFY_IN = pathlib.Path(os.path.expanduser(ENV["comfyInput"]))
    from engines.comfy import decode, stage, submit, unstage

    params = REQ.get("params") or {}
    coll, preset = manifests.by_code(REQ["stylePreset"])
    engine = coll.engine
    src_path = RUN / "input.png"
    img = cv2.imread(str(src_path))
    if img is None:
        raise RuntimeError("입력 사진을 읽지 못했습니다")

    # 1) 사진 분석 — 운영과 같은 antelopev2 검출 + 안경 판별
    with Timer("analyze", "사진 분석"):
        info = P.analyze(img, ctx_id=-1)
        faces = face_app("buffalo_l", ctx_id=-1).get(img)
        face = biggest(faces) if faces else None
        gender = REQ.get("gender") or "auto"
        if gender == "auto":
            gender = "female" if face is not None and getattr(face, "sex", "M") == "F" else "male"
        overlay = img.copy()
        x1, y1, x2, y2 = [int(v) for v in info["bbox"]]
        cv2.rectangle(overlay, (x1, y1), (x2, y2), (120, 230, 120), max(2, img.shape[1] // 300))
        for x, y in info["kps"]:
            cv2.circle(overlay, (int(x), int(y)), max(3, img.shape[1] // 200), (80, 160, 255), -1)
        write("analysis.png", overlay)
    analysis = {"bbox": info["bbox"], "faces": info["faces"], "detScore": round(info["det_score"], 3),
                "glasses": info["glasses"], "glassesRatio": info["glasses_ratio"], "gender": gender,
                "faceHeightPct": round(100 * (y2 - y1) / img.shape[0], 1), "imageSize": info["image_size"]}

    steps: list[dict] = []
    reference = None
    used: dict = {}
    if engine == "pulid":
        from engines.pulid import build_graph
        name = stage(src_path, "_lab")
        try:
            kw = {k: params[k] for k in ("pulid_weight", "guidance", "steps", "pulid_start", "pulid_end") if params.get(k) is not None}
            seed = int(params.get("seed", coll.get("pulid", {}).get("seed", 1000)))
            choice = params.get("glasses") or "auto"
            glasses = bool(info.get("glasses")) if choice == "auto" else choice in ("on", True)
            g = build_graph(name, prompt=params.get("prompt") or "", style=preset["key"], gender=gender, seed=seed,
                            glasses=glasses, filename_prefix=f"lab/{RUN.name}", **kw)
            total = next(n["inputs"]["steps"] for n in g.values() if n["class_type"] == "KSampler")
            used = {"seed": seed, "steps": total, "glasses": glasses, **kw,
                    "prompt": next(n["inputs"]["text"] for k, n in g.items() if k == "pos")}
            final, steps = diffusion(g, total, submit, decode)
        finally:
            unstage(name)
    elif engine == "kontext":
        from engines import kontext
        from steps.upscale import upscale
        kx, lora, out = coll.get("kontext", {}), coll.get("lora") or {}, coll["output"]
        if params.get("prompt"):
            text = params["prompt"]
        elif "prompt" in preset:
            text = preset["prompt"]
        else:
            text = coll["prompt"][gender].format(scene=preset["scene"], outfit=preset[f"outfit_{gender}"])
        use_lora = lora.get("name") if (params.get("lora", True) and kontext.lora_available(lora.get("name"))) else None
        if use_lora and lora.get("trigger"):
            text = f"{lora['trigger']} {text}"
        total = int(params.get("steps", kx.get("steps", 20)))
        used = {"guidance": float(params.get("guidance", kx.get("guidance", 2.5))), "steps": total,
                "seed": int(params.get("seed", kx.get("seed", 1000))), "lora": use_lora,
                "loraStrength": float(params.get("lora_strength", lora.get("strength", 1.0))), "prompt": text,
                "upscale": float(params.get("upscale", out.get("upscale") or 0) or 0)}
        name = stage(src_path, "_lab")
        try:
            g = kontext.graph(name, text, f"lab_{RUN.name}", width=out["width"], height=out["height"],
                              guidance=used["guidance"], steps=total, seed=used["seed"], lora=use_lora,
                              lora_strength=used["loraStrength"])
            final, steps = diffusion(g, total, submit, decode)
        finally:
            unstage(name)
        if used["upscale"]:
            with Timer("upscale", f"업스케일 ×{used['upscale']}"):
                final = upscale(final, used["upscale"])
            # 실험실은 GPU 를 ComfyUI 에만 쓴다. 그래서 업스케일은 4x-UltraSharp 대신 Lanczos 로 떨어진다(크기는 같다).
            used["upscaleNote"] = "실험실은 CPU Lanczos (운영은 4x-UltraSharp)"
    elif engine == "inswapper":
        from engines.inswapper import prepare_source, swap_into
        opts = coll.swap_opts(preset)
        for key in ("bangs", "crop"):
            if params.get(key):
                opts[key] = params[key]
        for key in ("restore",):
            if params.get(key) is not None:
                opts[key] = float(params[key])
        if params.get("face_mask") is not None:
            opts["face_mask"] = bool(params["face_mask"])
        ref_dir = coll.reference_dir() / gender
        ref = next((p for p in ref_dir.iterdir() if p.stem == preset["key"]), None)
        if ref is None:
            raise RuntimeError(f"참고 이미지가 없습니다: {gender}/{preset['key']}")
        reference = write("reference.png", cv2.imread(str(ref)))
        with Timer("swap", "얼굴 교체"):
            fa, source_face = prepare_source(img, -1)
            if source_face is None:
                raise RuntimeError("NO_FACE: 원본에서 얼굴을 찾지 못했습니다 (buffalo_l)")
            final, note = swap_into(ref, img, src_path, source_face, fa, None, glasses_mode="off", node=NODE,
                                    gender=gender, **opts)
            if final is None:
                raise RuntimeError(f"참고 이미지 얼굴 검출 실패: {note}")
        used = {**opts, "note": note}
    elif engine == "original":
        from serving.worker import cpu_path
        with Timer("crop", "원본 크롭"):
            final = cpu_path.original(None, coll, preset, img)
    else:
        raise RuntimeError(f"실험실이 모르는 엔진: {engine}")

    write("result.png", final)

    # 3) 누끼 — 운영과 같은 BiRefNet. (inswapper 운영은 사전 계산 마스크를 먼저 쓴다)
    with Timer("cutout", "누끼"):
        from steps.cutout import cutout
        write("cutout.png", cutout(final))

    # 4) 원본과의 얼굴 유사도 (antelopev2, 운영 벤치마크와 같은 방식)
    identity = None
    with Timer("identity", "얼굴 유사도"):
        ida = face_app("antelopev2", ctx_id=-1)
        a, b = ida.get(img), ida.get(final)
        if a and b:
            identity = round(float(biggest(a).normed_embedding @ biggest(b).normed_embedding), 4)

    result = {
        "stylePreset": REQ["stylePreset"], "collection": coll.id, "engine": engine, "rarity": preset.get("rarity", coll.get("tier")),
        "analysis": analysis, "used": used, "steps": steps, "reference": reference,
        "files": {"input": "input.png", "analysis": "analysis.png", "result": "result.png", "cutout": "cutout.png"},
        "resultSize": [int(final.shape[1]), int(final.shape[0])], "identity": identity,
        "timings": TIMINGS, "totalMs": int((time.time() - T0) * 1000),
    }
    (RUN / "result.json").write_text(json.dumps(result, ensure_ascii=False, default=str), encoding="utf-8")
    status("done", done=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # 실패도 파일로 남겨 로컬 화면이 보여 준다
        status("failed", done=True, error=f"{type(exc).__name__}: {exc}", trace=traceback.format_exc()[-2000:], timings=TIMINGS)
