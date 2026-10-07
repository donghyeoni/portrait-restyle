"""화풍 실험실 실행기 (운영 관리자 화면에서 온 실험 한 건).

운영 카드 생성과 같은 엔진 함수를 쓰되, 관리자가 고른 값과 참고 이미지로 돌리고 중간 결과를 남긴다.
운영 관리자 실험 워커(serving/lab/worker.py)와 로컬 도구(tools/admin-local/lab/gpu_lab.py)가 함께 쓴다.
운영 카드 생성에 없는 것이 두 가지 있다.

- 화풍 참고 이미지(Kontext): 원본과 함께 참조 잠재로 넣는다 (with_style_ref, chain 또는 stitch).
- 템플릿/코스튬 이미지(얼굴 교체): 프리셋 참고 그림 대신 올린 그림에 얼굴을 넣는다.

  result = run(request, workdir, node="http://127.0.0.1:8191")

request (메시지 payload 중 실행에 쓰는 부분):
  engine        inswapper | pulid | kontext | original(로컬 도구만)
  stylePreset   프리셋 코드. 없으면 직접 입력(kontext 는 prompt, inswapper 는 template 필수)
  style         (선택) 아직 카탈로그에 없는 화풍 정의 — 로컬 도구 "화풍 추가"의 초안. stylePreset 대신 쓴다
                {code, name, prompt, prompt_male, prompt_female}            kontext
                {code, name, subject_male, subject_female, scene}            pulid (화질 문구는 concept 공통값)
                inswapper 는 workdir 의 template_male.png · template_female.png
  gender        male | female | auto
  params        엔진별 조절값 (PARAM_FIELDS)
  captureSteps  디퓨전 중간 단계 수 (0~6)
workdir 에 input.png 가 있어야 하고, 있으면 style_ref.png · style_ref_{male,female}.png · template.png ·
template_{male,female}.png 를 쓴다(성별 그림이 먼저).
남기는 파일: analysis.png, reference.png(있을 때), step_N.png, result.png, cutout.png(BGRA)
"""
from __future__ import annotations

import copy
import pathlib
import time
from typing import Callable

import cv2
import numpy as np

MAX_CAPTURE = 6
ENGINES = ("inswapper", "pulid", "kontext", "original")

# 엔진별로 바꿀 수 있는 값. 화면·백엔드는 이 이름만 보낸다 (그 밖의 키는 버린다).
PARAM_FIELDS: dict[str, dict[str, tuple]] = {
    "pulid": {"seed": ("int", 0, 2**31 - 1), "steps": ("int", 4, 60), "guidance": ("float", 1, 8),
              "pulid_weight": ("float", 0, 3), "pulid_start": ("float", 0, 1), "pulid_end": ("float", 0, 1),
              "glasses": ("enum", "auto", "on", "off"), "prompt": ("text",)},
    "kontext": {"seed": ("int", 0, 2**31 - 1), "steps": ("int", 4, 60), "guidance": ("float", 1, 8),
                "lora": ("bool",), "lora_strength": ("float", 0, 2), "upscale": ("float", 0, 4), "prompt": ("text",),
                "style_mode": ("enum", "chain", "stitch")},
    "inswapper": {"bangs": ("enum", "keep", "drop"), "crop": ("enum", "upper", "fit"),
                  "restore": ("float", 0, 1), "face_mask": ("bool",)},
}


class LabError(Exception):
    """화면에 그대로 보여 줄 실패. code 는 대문자 식별자."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def clean_params(engine: str, params: dict | None) -> dict:
    """알려진 키만, 형식과 범위를 확인해 남긴다. 범위를 벗어나면 LabError."""
    out: dict = {}
    for key, spec in PARAM_FIELDS.get(engine, {}).items():
        value = (params or {}).get(key)
        if value is None or value == "":
            continue
        kind = spec[0]
        try:
            if kind in ("int", "float"):
                number = int(value) if kind == "int" else float(value)
                if not spec[1] <= number <= spec[2]:
                    raise ValueError
                out[key] = number
            elif kind == "bool":
                if not isinstance(value, bool):
                    raise ValueError
                out[key] = value
            elif kind == "enum":
                if value not in spec[1:]:
                    raise ValueError
                out[key] = value
            else:
                text = str(value).strip()
                if len(text) > 2000:
                    raise ValueError
                if text:
                    out[key] = text
        except (TypeError, ValueError):
            raise LabError("PARAM_INVALID", f"값이 올바르지 않습니다: {key}") from None
    return out


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


def with_style_ref(graph: dict, image_name: str, mode: str = "chain") -> dict:
    """Kontext 그래프에 화풍 참고 그림을 참조로 더한다. 운영 engines/kontext.graph 는 그대로 두고 실험에서만 덧붙인다.

    chain : 원본 참조 뒤에 참고 그림을 두 번째 ReferenceLatent 로 잇는다. 원본 인물·구도를 지킨다(기본).
    stitch: 원본 오른쪽에 참고 그림을 붙여(ImageStitch) 참조 하나로 넣는다(steps/glasses_kontext.py 방식).
            2026-10-07 시험에서 결과가 참고 그림의 인물을 그대로 베꼈다. 비교용으로 남긴다.
    참고 그림이 실사 인물 사진이면 chain 도 그 인물을 따라간다 — 그림체가 드러난 그림을 쓴다.
    """
    g = copy.deepcopy(graph)
    g["styleImg"] = {"class_type": "LoadImage", "inputs": {"image": image_name}}
    if mode == "stitch":
        g["stitch"] = {"class_type": "ImageStitch", "inputs": {"image1": ["img", 0], "image2": ["styleImg", 0],
                       "direction": "right", "match_image_size": True, "spacing_width": 0, "spacing_color": "white"}}
        g["scale"]["inputs"]["image"] = ["stitch", 0]
        return g
    g["styleScale"] = {"class_type": "FluxKontextImageScale", "inputs": {"image": ["styleImg", 0]}}
    g["styleEnc"] = {"class_type": "VAEEncode", "inputs": {"pixels": ["styleScale", 0], "vae": ["vae", 0]}}
    g["styleRef"] = {"class_type": "ReferenceLatent", "inputs": {"conditioning": ["ref", 0], "latent": ["styleEnc", 0]}}
    g["guid"]["inputs"]["conditioning"] = ["styleRef", 0]
    return g


def style_prompt(style: dict | None, gender: str) -> str | None:
    """초안 화풍의 Kontext 문장. 성별 문장이 있으면 그것, 없으면 공통 문장."""
    if not style:
        return None
    return (style.get(f"prompt_{gender}") or style.get("prompt") or "").strip() or None


def pulid_positive(style: dict, gender: str, common: dict, *, glasses_text: str | None = None,
                   extra: str = "") -> str:
    """초안 화풍의 PuLID 문장. engines.pulid.build_graph 와 같은 순서: 주체, (안경), (추가), 장면, 화질.
    common 은 concept.yaml common 을 대문자 키로 바꾼 것 — 주체의 {MODEST} {NECK} 자리를 채운다."""
    subject = style.get(f"subject_{gender}") or style.get("subject_male") or style.get("subject_female")
    scene = (style.get("scene") or "").strip()
    if not subject or not scene:
        raise LabError("STYLE_INVALID", "PuLID 화풍은 주체(남·여 중 하나 이상)와 장면 문구가 필요합니다")
    try:
        subject = subject.strip().format(**common)
    except (KeyError, IndexError, ValueError):
        raise LabError("STYLE_INVALID", "주체 문구의 {...} 자리는 {MODEST} {NECK} 만 쓸 수 있습니다") from None
    parts = [subject, scene, common["QUALITY"]]
    if glasses_text:
        parts.insert(1, glasses_text)
    if extra:
        parts.insert(1, extra)
    return ", ".join(parts)


def capture_points(total: int, count: int) -> list[int]:
    count = max(0, min(MAX_CAPTURE, int(count)))
    if count == 0 or total <= 1:
        return []
    points = sorted({max(1, round(total * (i + 1) / (count + 1))) for i in range(count)})
    return [p for p in points if p < total]


class _Run:
    def __init__(self, workdir: pathlib.Path, on_stage: Callable[[str, str], None] | None):
        self.dir, self.on_stage = workdir, on_stage
        self.t0 = time.time()
        self.timings: list[dict] = []

    def stage(self, name: str, label: str):
        run = self

        class _T:
            def __enter__(self):
                if run.on_stage:
                    run.on_stage(name, label)
                self.t = time.time()

            def __exit__(self, *exc):
                run.timings.append({"stage": name, "label": label, "ms": int((time.time() - self.t) * 1000)})
        return _T()

    def write(self, name: str, img) -> str:
        cv2.imwrite(str(self.dir / name), img)
        return name


def _read(path: pathlib.Path, what: str):
    img = cv2.imread(str(path))
    if img is None:
        raise LabError("IMAGE_INVALID", f"{what} 이미지를 읽지 못했습니다")
    return img


def run(req: dict, workdir: pathlib.Path, *, node: str, ctx_id: int = -1,
        on_stage: Callable[[str, str], None] | None = None) -> dict:
    import manifests
    from engines.comfy import decode, stage, submit, unstage
    from serving.common import preprocess as P
    from serving.common.item import ItemError
    from steps.faces import biggest, face_app

    engine = req.get("engine")
    if engine not in ENGINES:
        raise LabError("ENGINE_INVALID", f"모르는 모델입니다: {engine}")
    params = clean_params(engine, req.get("params"))
    code = req.get("stylePreset") or None
    style = req.get("style") if not code else None          # 초안 화풍(카탈로그에 아직 없음)
    coll = preset = None
    if code:
        try:
            coll, preset = manifests.by_code(code)
        except KeyError:
            raise LabError("PRESET_INVALID", f"모르는 화풍입니다: {code}") from None
        if coll.engine != engine:
            raise LabError("PRESET_INVALID", f"{code} 는 {coll.engine} 화풍입니다")
    r = _Run(workdir, on_stage)
    src_path = workdir / "input.png"
    img = _read(src_path, "입력")
    template_path = workdir / "template.png"
    has_style = any(workdir.glob("style_ref*.png"))
    has_template = template_path.exists()

    # 1) 사진 분석 — 운영과 같은 antelopev2 검출 + 안경 판별
    with r.stage("analyze", "사진 분석"):
        try:
            info = P.analyze(img, ctx_id=ctx_id)
        except ItemError as e:
            raise LabError(e.code, "입력 사진에서 얼굴을 찾지 못했습니다" if e.code == "NO_FACE" else str(e)) from None
        faces = face_app("buffalo_l", ctx_id=ctx_id).get(img)
        face = biggest(faces) if faces else None
        gender = req.get("gender") or "auto"
        if gender not in ("male", "female"):
            gender = "female" if face is not None and getattr(face, "sex", "M") == "F" else "male"
        overlay = img.copy()
        x1, y1, x2, y2 = [int(v) for v in info["bbox"]]
        cv2.rectangle(overlay, (x1, y1), (x2, y2), (120, 230, 120), max(2, img.shape[1] // 300))
        for x, y in info["kps"]:
            cv2.circle(overlay, (int(x), int(y)), max(3, img.shape[1] // 200), (80, 160, 255), -1)
        r.write("analysis.png", overlay)
    analysis = {"faces": info["faces"], "detScore": round(info["det_score"], 3), "glasses": info["glasses"],
                "glassesRatio": info["glasses_ratio"], "gender": gender,
                "faceHeightPct": round(100 * (y2 - y1) / img.shape[0], 1), "imageSize": info["image_size"]}

    # 화풍 참고: 성별 그림(style_ref_<gender>.png, 화풍 추가의 남·여 reference)이 있으면 그것, 없으면 공통 그림
    style_path = workdir / f"style_ref_{gender}.png"
    if not style_path.exists():
        style_path = workdir / "style_ref.png"
    if has_style and not style_path.exists():
        has_style = False
        notes_pre = [f"이 화풍에 {'남성' if gender == 'male' else '여성'} 화풍 참고 그림이 없어 문장만으로 돌렸습니다"]
    else:
        notes_pre = []

    steps: list[dict] = []
    reference = None
    used: dict = {}
    notes: list[str] = notes_pre

    def diffusion(graph: dict, total: int) -> np.ndarray:
        for k in capture_points(total, req.get("captureSteps", 0)):
            with r.stage(f"step_{k}", f"중간 단계 {k}/{total}"):
                frame = decode(submit(node, advanced(graph, k, total)))
            steps.append({"step": k, "total": total, "file": r.write(f"step_{k}.png", frame)})
        with r.stage("generate", f"생성 {total}단계"):
            return decode(submit(node, graph))

    if engine == "pulid":
        if preset is None and not style:
            raise LabError("PRESET_REQUIRED", "PuLID 는 화풍(프리셋)을 골라야 합니다")
        if has_style or has_template:
            notes.append("PuLID 는 참고 이미지를 쓰지 않습니다 (서버에 IP-Adapter·Redux 모델이 없음)")
        from engines import pulid
        name = stage(src_path, "_lab")
        try:
            kw = {k: params[k] for k in ("pulid_weight", "guidance", "steps", "pulid_start", "pulid_end") if k in params}
            seed_default = coll.get("pulid", {}).get("seed", 1000) if coll is not None else 1000
            seed = int(params.get("seed", seed_default))
            choice = params.get("glasses", "auto")
            glasses = bool(info.get("glasses")) if choice == "auto" else choice == "on"
            if preset is not None:
                g = pulid.build_graph(name, prompt=params.get("prompt", ""), style=preset["key"], gender=gender,
                                      seed=seed, glasses=glasses, filename_prefix=f"lab/{workdir.name}", **kw)
            else:
                positive = pulid_positive(style, gender, pulid._COMMON, glasses_text=pulid.GLASSES if glasses else None,
                                          extra=params.get("prompt", ""))
                g = pulid.build(pulid.FluxPulidConfig(face_image=name, positive=positive, seed=seed,
                                                      filename_prefix=f"lab/{workdir.name}", **kw))
            total = next(n["inputs"]["steps"] for n in g.values() if n["class_type"] == "KSampler")
            used = {"seed": seed, "steps": total, "glasses": glasses, **kw,
                    "prompt": next(n["inputs"]["text"] for k, n in g.items() if k == "pos")}
            final = diffusion(g, total)
        finally:
            unstage(name)
    elif engine == "kontext":
        from engines import kontext
        from steps.upscale import upscale
        base = coll or next((c for c in manifests.all_collections().values() if c.engine == "kontext"), None)
        if base is None:
            raise LabError("PRESET_INVALID", "Kontext 화풍이 없습니다")
        kx, lora, out = base.get("kontext", {}), base.get("lora") or {}, base.get("output", {})
        if params.get("prompt"):
            text = params["prompt"]
        elif style_prompt(style, gender):
            text = style_prompt(style, gender)
        elif preset is not None and "prompt" in preset:
            text = preset["prompt"]
        elif preset is not None:
            text = base["prompt"][gender].format(scene=preset["scene"], outfit=preset[f"outfit_{gender}"])
        else:
            raise LabError("PROMPT_REQUIRED", "직접 입력 화풍은 프롬프트가 필요합니다")
        want_lora = params.get("lora", bool(lora.get("name")) and preset is not None)
        use_lora = lora.get("name") if (want_lora and kontext.lora_available(lora.get("name"))) else None
        if want_lora and not use_lora:
            notes.append("LoRA 파일이 서버에 없어 LoRA 없이 돌렸습니다")
        if use_lora and lora.get("trigger"):
            text = f"{lora['trigger']} {text}"
        total = int(params.get("steps", kx.get("steps", 20)))
        used = {"guidance": float(params.get("guidance", kx.get("guidance", 2.5))), "steps": total,
                "seed": int(params.get("seed", kx.get("seed", 1000))), "lora": use_lora,
                "loraStrength": float(params.get("lora_strength", lora.get("strength", 1.0))), "prompt": text,
                "upscale": float(params.get("upscale", out.get("upscale") or 0) or 0), "styleReference": has_style}
        if has_style:
            used["styleMode"] = params.get("style_mode", "chain")
        name = stage(src_path, "_lab")
        style_name = stage(style_path, "_labref") if has_style else None
        try:
            if style_name:
                reference = r.write("reference.png", _read(style_path, "화풍 참고"))
            g = kontext.graph(name, text, f"lab_{workdir.name}", width=out.get("width", 896),
                              height=out.get("height", 1152), guidance=used["guidance"], steps=total,
                              seed=used["seed"], lora=use_lora, lora_strength=used["loraStrength"])
            if style_name:
                g = with_style_ref(g, style_name, used["styleMode"])
            final = diffusion(g, total)
        finally:
            unstage(name)
            if style_name:
                unstage(style_name)
        if used["upscale"]:
            with r.stage("upscale", f"업스케일 ×{used['upscale']}"):
                final = upscale(final, used["upscale"])
            if ctx_id < 0:
                notes.append("GPU 를 ComfyUI 에만 써서 업스케일은 CPU Lanczos 입니다 (운영은 4x-UltraSharp, 크기는 같음)")
    elif engine == "original":
        if preset is None:
            raise LabError("PRESET_REQUIRED", "원본 크롭은 화풍(프리셋)을 골라야 합니다")
        from serving.worker import cpu_path
        with r.stage("crop", "원본 크롭"):
            final = cpu_path.original(None, coll, preset, img)
    else:  # inswapper
        from engines.inswapper import prepare_source, swap_into
        if has_style:
            notes.append("얼굴 교체는 화풍 참고 이미지를 쓰지 않습니다 (템플릿 이미지를 쓰세요)")
        gendered = workdir / f"template_{gender}.png"
        if gendered.exists():
            ref, has_template = gendered, True
        elif has_template:
            ref = template_path
        elif style:
            raise LabError("TEMPLATE_MISSING", f"이 화풍에 {'남성' if gender == 'male' else '여성'} 템플릿이 없습니다")
        elif preset is not None:
            ref_dir = coll.reference_dir() / gender
            ref = next((p for p in ref_dir.iterdir() if p.stem == preset["key"]), None) if ref_dir.exists() else None
            if ref is None:
                raise LabError("TEMPLATE_MISSING", f"이 화풍의 {gender} 템플릿이 없습니다")
        else:
            raise LabError("TEMPLATE_REQUIRED", "직접 입력 화풍은 템플릿 이미지가 필요합니다")
        opts = dict(coll.swap_opts(preset)) if coll is not None else \
            {"bangs": "keep", "crop": "upper", "restore": 0.0, "face_mask": False}
        opts.update({k: params[k] for k in ("bangs", "crop", "restore", "face_mask") if k in params})
        reference = r.write("reference.png", _read(ref, "템플릿"))
        with r.stage("swap", "얼굴 교체"):
            fa, source_face = prepare_source(img, ctx_id)
            if source_face is None:
                raise LabError("NO_FACE", "입력 사진에서 얼굴을 찾지 못했습니다 (buffalo_l)")
            final, note = swap_into(ref, img, src_path, source_face, fa, None, glasses_mode="off", node=node,
                                    gender=gender, **opts)
            if final is None:
                raise LabError("TEMPLATE_NO_FACE", f"템플릿에서 얼굴을 찾지 못했습니다: {note}")
        used = {**opts, "template": "업로드" if has_template else preset["key"], "note": note}

    r.write("result.png", final)

    # 누끼 — 운영과 같은 BiRefNet
    with r.stage("cutout", "누끼"):
        from steps.cutout import cutout
        r.write("cutout.png", cutout(final))

    # 입력과의 얼굴 유사도 (antelopev2, 운영 벤치마크와 같은 방식)
    identity = None
    with r.stage("identity", "얼굴 유사도"):
        ida = face_app("antelopev2", ctx_id=ctx_id)
        a, b = ida.get(img), ida.get(final)
        if a and b:
            identity = round(float(biggest(a).normed_embedding @ biggest(b).normed_embedding), 4)

    return {
        "engine": engine, "stylePreset": code, "collection": coll.id if coll is not None else None,
        "style": {k: style.get(k) for k in ("code", "name")} if style else None,
        "rarity": (preset.get("rarity", coll.get("tier")) if preset is not None else None),
        "analysis": analysis, "used": used, "notes": notes, "steps": steps, "reference": reference,
        "resultSize": [int(final.shape[1]), int(final.shape[0])], "identity": identity,
        "timings": r.timings, "totalMs": int((time.time() - r.t0) * 1000),
    }
