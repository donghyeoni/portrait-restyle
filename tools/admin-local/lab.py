"""실험실: 프리셋 목록·기본 파라미터, 실행 기록 보관.

프리셋과 기본값은 이 저장소의 manifests 를 읽는다(서비스 카탈로그의 원본). GPU 서버에서 실제로 쓰는 코드와
어긋나면 결과 화면의 "적용된 값"이 기준이다 — 실행기가 실제로 쓴 값을 돌려준다.
실행 기록(입력·결과 이미지)은 이 PC 의 lab-runs/ 에만 둔다(git 에 올리지 않는다).
"""
from __future__ import annotations

import json
import pathlib
import re
import secrets
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[1]
RUNS = HERE / "lab-runs"
RUNNER = HERE / "lab" / "gpu_lab.py"
_RUN_ID = re.compile(r"^[a-z0-9_-]{6,64}$")
_FILE = re.compile(r"^[a-z0-9_]{1,40}\.(png|json)$")

# 엔진별로 화면에서 바꿀 수 있는 값. kind: number | text | select | bool
FIELDS = {
    "pulid": [
        {"key": "seed", "label": "시드", "kind": "number", "step": 1},
        {"key": "steps", "label": "스텝", "kind": "number", "step": 1, "min": 4, "max": 60},
        {"key": "guidance", "label": "guidance", "kind": "number", "step": 0.1, "min": 1, "max": 8},
        {"key": "pulid_weight", "label": "PuLID weight", "kind": "number", "step": 0.05, "min": 0, "max": 3},
        {"key": "pulid_start", "label": "PuLID 시작", "kind": "number", "step": 0.05, "min": 0, "max": 1},
        {"key": "pulid_end", "label": "PuLID 끝", "kind": "number", "step": 0.05, "min": 0, "max": 1},
        {"key": "glasses", "label": "안경 문구", "kind": "select", "options": ["auto", "on", "off"]},
        {"key": "prompt", "label": "추가 프롬프트(주체 뒤)", "kind": "text"},
    ],
    "kontext": [
        {"key": "seed", "label": "시드", "kind": "number", "step": 1},
        {"key": "steps", "label": "스텝", "kind": "number", "step": 1, "min": 4, "max": 60},
        {"key": "guidance", "label": "guidance", "kind": "number", "step": 0.1, "min": 1, "max": 8},
        {"key": "lora_strength", "label": "LoRA 강도", "kind": "number", "step": 0.05, "min": 0, "max": 2},
        {"key": "lora", "label": "LoRA 사용", "kind": "bool"},
        {"key": "upscale", "label": "업스케일 배율(0=끔)", "kind": "number", "step": 0.01, "min": 0, "max": 4},
        {"key": "prompt", "label": "프롬프트(비우면 프리셋 문구)", "kind": "text"},
    ],
    "inswapper": [
        {"key": "bangs", "label": "앞머리", "kind": "select", "options": ["keep", "drop"]},
        {"key": "crop", "label": "크롭", "kind": "select", "options": ["upper", "fit"]},
        {"key": "restore", "label": "GFPGAN 비율", "kind": "number", "step": 0.05, "min": 0, "max": 1},
        {"key": "face_mask", "label": "얼굴만 교체", "kind": "bool"},
    ],
    "original": [],
}


def _defaults(coll, preset: dict) -> dict:
    engine = coll.engine
    if engine == "pulid":
        params = preset.get("params", {})
        return {"seed": coll.get("pulid", {}).get("seed", 1000), "steps": 22, "guidance": params.get("guidance", 3.2),
                "pulid_weight": params.get("pulid_weight", 1.0), "pulid_start": params.get("pulid_start", 0.0),
                "pulid_end": params.get("pulid_end", 1.0), "glasses": "auto", "prompt": ""}
    if engine == "kontext":
        kx, lora, out = coll.get("kontext", {}), coll.get("lora") or {}, coll.get("output", {})
        return {"seed": kx.get("seed", 1000), "steps": kx.get("steps", 20), "guidance": kx.get("guidance", 2.5),
                "lora_strength": lora.get("strength", 1.0), "lora": bool(lora.get("name")),
                "upscale": out.get("upscale") or 0, "prompt": ""}
    if engine == "inswapper":
        return dict(coll.swap_opts(preset))
    return {}


def presets() -> list[dict]:
    sys.path.insert(0, str(REPO))
    import manifests
    out = []
    for coll in manifests.all_collections().values():
        for code, preset in coll.presets().items():
            out.append({
                "stylePreset": code, "collection": coll.id, "display": coll.get("display", coll.id),
                "engine": coll.engine, "rarity": preset.get("rarity", coll.get("tier")),
                "defaults": _defaults(coll, preset), "fields": FIELDS.get(coll.engine, []),
                "captureSteps": coll.engine in ("pulid", "kontext"),
            })
    return out


def clean_params(engine: str, params: dict) -> dict:
    """화면에서 온 값을 엔진이 아는 키·형식으로만 거른다."""
    out = {}
    for field in FIELDS.get(engine, []):
        key, value = field["key"], (params or {}).get(field["key"])
        if value in (None, ""):
            continue
        if field["kind"] == "number":
            number = float(value)
            if "min" in field and number < field["min"] or "max" in field and number > field["max"]:
                raise ValueError(f"{field['label']} 범위를 벗어났습니다")
            out[key] = int(number) if field.get("step") == 1 else number
        elif field["kind"] == "bool":
            out[key] = bool(value)
        elif field["kind"] == "select":
            if value not in field["options"]:
                raise ValueError(f"{field['label']} 값이 올바르지 않습니다")
            out[key] = value
        else:
            text = str(value)
            if len(text) > 2000:
                raise ValueError(f"{field['label']} 이 너무 깁니다")
            out[key] = text
    return out


# ── 실행 기록 ──

def new_run_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)


def run_dir(run_id: str) -> pathlib.Path:
    if not _RUN_ID.match(run_id or ""):
        raise ValueError("실행 번호가 올바르지 않습니다")
    return RUNS / run_id


def save_request(run_id: str, request: dict, image: bytes):
    d = run_dir(run_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "request.json").write_text(json.dumps(request, ensure_ascii=False, indent=1), encoding="utf-8")
    (d / "input.png").write_bytes(image)


def read_json(run_id: str, name: str):
    p = run_dir(run_id) / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def write_file(run_id: str, name: str, data: bytes):
    if not _FILE.match(name):
        raise ValueError("파일 이름이 올바르지 않습니다")
    (run_dir(run_id) / name).write_bytes(data)


def file_path(run_id: str, name: str) -> pathlib.Path | None:
    if not _FILE.match(name or ""):
        return None
    p = run_dir(run_id) / name
    return p if p.is_file() else None


def history(limit: int = 60) -> list[dict]:
    if not RUNS.exists():
        return []
    rows = []
    for d in sorted((p for p in RUNS.iterdir() if p.is_dir() and _RUN_ID.match(p.name)), reverse=True)[:limit]:
        req = read_json(d.name, "request.json") or {}
        result = read_json(d.name, "result.json")
        status = read_json(d.name, "status.json") or {}
        rows.append({
            "runId": d.name, "stylePreset": req.get("stylePreset"), "gender": req.get("gender"),
            "params": req.get("params"), "label": req.get("label"), "source": req.get("source"),
            "state": "done" if result else ("failed" if status.get("error") else status.get("stage", "queued")),
            "identity": result.get("identity") if result else None, "totalMs": result.get("totalMs") if result else None,
        })
    return rows
