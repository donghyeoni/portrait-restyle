"""화풍 추가: 카테고리 · 종류 초안.

카테고리 = manifests 컬렉션(모델 하나, 예: 직업·컨셉·웹툰), 종류 = 그 안의 프리셋(예: DOCTOR).
서비스에 넣기 전의 초안을 이 PC 의 lab-styles/ 에 둔다(git 에 올리지 않는다). 새 카테고리를 만들 수도 있고,
서비스에 이미 있는 카테고리에 종류만 더할 수도 있다.

  lab-styles/<카테고리 id>/category.json            새 카테고리만 (서비스 카테고리에 종류만 더할 때는 없다)
  lab-styles/<카테고리 id>/<종류 CODE>/style.json    + reference_male.png · reference_female.png

종류마다 남·여 reference 사진을 둔다. 얼굴 교체는 템플릿(얼굴을 넣을 그림), Kontext 는 화풍 참고로 실행에 넘기고,
PuLID 는 참고 그림을 쓰지 못해(IP-Adapter·Redux 없음) 결과 옆 비교용으로만 둔다.
"""
from __future__ import annotations

import json
import pathlib
import re
import sys
import time

import lab

STYLES_DIR = lab.HERE / "lab-styles"
_CATEGORY_ID = re.compile(r"^[a-z][a-z0-9_]{1,30}$")
_STYLE_CODE = re.compile(r"^[A-Z][A-Z0-9_]{1,39}$")
ENGINES = lab.ACTIVE_ENGINES          # 지금은 얼굴 교체만(고정). 문구·reference 처리는 다른 모델도 남겨 둔다
TEXT = {"kontext": ("prompt", "prompt_male", "prompt_female"),
        "pulid": ("subject_male", "subject_female", "scene"), "inswapper": ()}
REFERENCES = {"male": "reference_male.png", "female": "reference_female.png"}
RUN_NAMES = {"inswapper": "template_{}.png", "kontext": "style_ref_{}.png", "pulid": None}   # 실행기가 아는 이름
RARITIES = ("N", "R", "SR", "SSR")


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _write_json(path: pathlib.Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def _read_json(path: pathlib.Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def check_category_id(cid: str) -> str:
    if not _CATEGORY_ID.match(cid or ""):
        raise ValueError("카테고리 id 는 영문 소문자로 시작하는 소문자·숫자·밑줄 2~31자입니다 (예: sports)")
    return cid


def check_code(code: str) -> str:
    if not _STYLE_CODE.match(code or ""):
        raise ValueError("종류 코드는 영문 대문자로 시작하는 대문자·숫자·밑줄 2~40자입니다 (예: NEON_WEBTOON)")
    return code


def _text(engine: str, body: dict) -> dict:
    out = {}
    for key in TEXT[engine]:
        value = (body.get(key) or "").strip()
        if len(value) > 2000:
            raise ValueError(f"{key} 문구가 너무 깁니다")
        if value:
            out[key] = value
    return out


# ── 카테고리 ──

def _service() -> dict[str, dict]:
    sys.path.insert(0, str(lab.REPO))
    import manifests
    return {c.id: {"id": c.id, "name": c.get("display", c.id), "engine": c.engine, "rarity": c.get("tier"),
                   "service": True, "presets": list(c.presets())}
            for c in manifests.all_collections().values() if c.engine in ENGINES}


def _kind(cid: str, code: str) -> dict | None:
    d = STYLES_DIR / cid / code
    if not (d / "style.json").exists():
        return None
    kind = _read_json(d / "style.json")
    kind["references"] = [g for g, n in REFERENCES.items() if (d / n).exists()]
    return kind


def _kinds(cid: str, include_archived: bool) -> list[dict]:
    d = STYLES_DIR / cid
    if not d.is_dir():
        return []
    rows = [k for k in (_kind(cid, p.name) for p in sorted(d.iterdir()) if p.is_dir() and _STYLE_CODE.match(p.name)) if k]
    return [k for k in rows if include_archived or not k.get("archived")]


def get_category(cid: str, include_archived: bool = True) -> dict | None:
    check_category_id(cid)
    service = _service().get(cid)
    p = STYLES_DIR / cid / "category.json"
    if service is None and not p.exists():
        return None
    cat = dict(service) if service else {**_read_json(p), "service": False, "presets": []}
    cat["kinds"] = _kinds(cid, include_archived)
    return cat


def categories(include_archived: bool = False) -> list[dict]:
    """새 카테고리(초안) 먼저, 그다음 서비스 카테고리."""
    drafts = []
    if STYLES_DIR.exists():
        for d in sorted(STYLES_DIR.iterdir()):
            if (d / "category.json").exists() and _CATEGORY_ID.match(d.name):
                cat = get_category(d.name, include_archived)
                if include_archived or not cat.get("archived"):
                    drafts.append(cat)
    drafts.sort(key=lambda c: c.get("updatedAt", ""), reverse=True)
    return drafts + [get_category(cid, include_archived) for cid in _service()]


def save_category(body: dict) -> dict:
    cid = check_category_id((body.get("id") or "").strip().lower())
    if cid in _service():
        raise ValueError(f"{cid} 는 서비스에 있는 카테고리입니다. 종류만 더할 수 있습니다")
    p = STYLES_DIR / cid / "category.json"
    previous = _read_json(p) if p.exists() else None
    if previous and body.get("isNew"):
        raise ValueError(f"{cid} 카테고리가 이미 있습니다. 목록에서 골라 고치세요")
    engine = body.get("engine") or ENGINES[0]
    if engine not in ENGINES:
        raise ValueError("지금은 얼굴 교체 카테고리만 만들 수 있습니다")
    if previous and previous["engine"] != engine and _kinds(cid, True):
        raise ValueError("종류가 있는 카테고리는 모델을 바꿀 수 없습니다 (종류의 문구·참고 사진이 모델마다 다릅니다)")
    name = (body.get("name") or "").strip()
    if not 1 <= len(name) <= 40:
        raise ValueError("이름은 1~40자입니다")
    if body.get("rarity") not in RARITIES:
        raise ValueError("등급을 골라 주세요")
    now = _now()
    _write_json(p, {"id": cid, "name": name, "engine": engine, "rarity": body["rarity"],
                    "memo": (body.get("memo") or "")[:500], "archived": bool((previous or {}).get("archived")),
                    "createdAt": (previous or {}).get("createdAt", now), "updatedAt": now})
    return get_category(cid)


# ── 종류 ──

def _code_owner(code: str) -> str | None:
    """이 코드를 이미 쓰는 곳. stylePreset 코드는 카테고리를 넘어 하나뿐이어야 한다."""
    if any(p["stylePreset"] == code for p in lab.presets()):
        return "서비스"
    if STYLES_DIR.exists():
        for d in STYLES_DIR.iterdir():
            if (d / code / "style.json").exists():
                return f"{d.name} 카테고리"
    return None


def get_kind(cid: str, code: str) -> dict | None:
    return _kind(check_category_id(cid), check_code(code))


def save_kind(cid: str, body: dict, images: dict[str, bytes], remove: list[str]) -> dict:
    """종류를 만들거나 고친다. images: {"male"|"female": 바이트}, remove: 뺄 성별 reference."""
    cat = get_category(cid)
    if cat is None:
        raise ValueError("없는 카테고리입니다")
    code = check_code((body.get("code") or "").strip().upper())
    d = STYLES_DIR / cid / code
    previous = _kind(cid, code)
    if previous is None:
        owner = _code_owner(code)
        if owner:
            raise ValueError(f"{code} 는 이미 {owner}에 있는 코드입니다")
    elif body.get("isNew"):
        raise ValueError(f"{code} 종류가 이미 있습니다. 목록에서 골라 고치세요")
    engine = cat["engine"]
    name = (body.get("name") or "").strip()
    if not 1 <= len(name) <= 40:
        raise ValueError("이름은 1~40자입니다")
    rarity = body.get("rarity") or cat.get("rarity")
    if rarity not in RARITIES:
        raise ValueError("등급을 골라 주세요")
    text = _text(engine, body)
    params = lab.clean_params(engine, {k: v for k, v in (body.get("params") or {}).items() if k != "prompt"})
    have = {g for g, n in REFERENCES.items() if (d / n).exists() and g not in remove} | (set(images) & set(REFERENCES))
    if engine == "kontext" and not text:
        raise ValueError("Kontext 종류는 프롬프트(공통 또는 성별)가 필요합니다")
    if engine == "pulid" and not ({"subject_male", "subject_female"} & set(text) and "scene" in text):
        raise ValueError("PuLID 종류는 주체(남·여 중 하나 이상)와 장면 문구가 필요합니다")
    if engine == "inswapper" and not have:
        raise ValueError("얼굴 교체 종류는 reference 사진(남·여 중 하나 이상)이 필요합니다 — 얼굴을 넣을 템플릿입니다")
    d.mkdir(parents=True, exist_ok=True)
    for g in remove:
        if g in REFERENCES and (d / REFERENCES[g]).exists():
            (d / REFERENCES[g]).unlink()       # 화면에서 뺀 reference. 실행 기록(lab-runs)에는 쓴 사진이 따로 남는다
    for g, data in images.items():
        if g in REFERENCES:
            (d / REFERENCES[g]).write_bytes(data)
    now = _now()
    _write_json(d / "style.json", {
        "code": code, "category": cid, "name": name, "engine": engine, "rarity": rarity,
        "memo": (body.get("memo") or "")[:500], **text, "params": params,
        "archived": bool((previous or {}).get("archived")),
        "createdAt": (previous or {}).get("createdAt", now), "updatedAt": now})
    return get_kind(cid, code)


def archive(cid: str, code: str | None, archived: bool) -> dict:
    """목록에서 숨기거나 되돌린다. 파일은 지우지 않는다. code 가 없으면 카테고리."""
    check_category_id(cid)
    p = STYLES_DIR / cid / check_code(code) / "style.json" if code else STYLES_DIR / cid / "category.json"
    if not p.exists():
        raise ValueError("서비스 카테고리는 보관할 수 없습니다" if not code and cid in _service() else "없는 항목입니다")
    data = _read_json(p)
    data["archived"] = bool(archived)
    _write_json(p, data)
    return get_kind(cid, code) if code else get_category(cid)


def reference_file(cid: str, code: str, name: str) -> pathlib.Path | None:
    if name not in REFERENCES.values():
        return None
    p = STYLES_DIR / check_category_id(cid) / check_code(code) / name
    return p if p.is_file() else None


# ── 화풍 테스트와 잇기 ──

def draft_styles() -> list[dict]:
    """화풍 테스트에 나오는 종류(보관한 것 빼고). value 는 draft:<카테고리>/<코드>."""
    return [{**kind, "categoryName": cat["name"], "categoryService": cat["service"], "value": f"draft:{cat['id']}/{kind['code']}"}
            for cat in categories() for kind in cat["kinds"]]


def find_draft(value: str) -> dict | None:
    try:
        cid, code = value[len("draft:"):].split("/", 1)
        return get_kind(cid, code)
    except ValueError:
        return None


def draft_images(kind: dict) -> tuple[dict[str, bytes], dict[str, bytes]]:
    """(GPU 로 보낼 그림, 이 PC 기록에만 둘 그림). 보낼 그림 이름은 실행기가 아는 이름."""
    send, local = {}, {}
    for g in kind.get("references", []):
        data = reference_file(kind["category"], kind["code"], REFERENCES[g]).read_bytes()
        pattern = RUN_NAMES[kind["engine"]]
        if pattern:
            send[pattern.format(g)] = data
        else:
            local[REFERENCES[g]] = data
    return send, local


def run_style(kind: dict) -> dict:
    """실행기(runner)에 넘기는 화풍 정의."""
    return {k: kind[k] for k in ("code", "name", "category", *TEXT[kind["engine"]]) if kind.get(k)}


# ── 서비스에 합칠 때 ──

def manifest_preview(cat: dict) -> str:
    """새 카테고리는 manifests/<id>.yaml 전체, 서비스 카테고리는 presets 에 더할 조각."""
    import yaml
    engine = cat["engine"]
    kinds = [k for k in cat["kinds"] if not k.get("archived")]
    presets_out: dict = {}
    notes: list[str] = []
    for k in kinds:
        key, params = k["code"].lower(), dict(k.get("params") or {})
        p: dict = {"key": key}
        if k.get("rarity") and k["rarity"] != cat.get("rarity"):
            p["rarity"] = k["rarity"]
        if engine == "kontext":
            p["prompt"] = k.get("prompt") or k.get("prompt_male") or k.get("prompt_female")
            if k.get("prompt_male") or k.get("prompt_female"):
                notes.append(f"{k['code']}: 성별 문장은 프리셋에 없다 — 컬렉션 prompt.male/female 와 scene/outfit 으로 옮기거나 하나로 합친다")
            if k.get("references"):
                notes.append(f"{k['code']}: 화풍 참고 사진은 운영 Kontext 에 아직 없다(실험실 전용). 운영은 문장·LoRA 만 쓴다")
        elif engine == "pulid":
            p.update({f: k[f] for f in ("subject_male", "subject_female", "scene") if k.get(f)})
            pp = {f: params[f] for f in ("pulid_weight", "guidance", "steps", "pulid_start", "pulid_end") if f in params}
            if pp:
                p["params"] = pp
        else:
            p.update({f: params[f] for f in ("bangs", "crop", "restore", "face_mask") if f in params})
            if k.get("references"):
                notes.append(f"{k['code']}: 템플릿을 " + " · ".join(f"data/reference/{cat['id']}/{g}/{key}.png" for g in k["references"]) + " 로 복사한다")
        presets_out[k["code"]] = p
    where = f"manifests/{cat['id']}.yaml 의 presets: 아래에 더한다" if cat["service"] else f"manifests/{cat['id']}.yaml 로 새로 만든다"
    head = [f"# {cat['name']} ({cat['id']}) — {where}"] + [f"# {n}" for n in notes]
    if cat["service"]:
        body = {"presets": presets_out}
    else:
        body = {"id": cat["id"], "display": cat["name"], "tier": cat["rarity"], "version": f"{cat['id']}-v1",
                "engine": engine, "enabled": True}
        if engine in ("inswapper", "kontext"):
            body["reference"] = f"data/reference/{cat['id']}"
        body["output"] = {"width": 896, "height": 1152, "dir": f"data/output/{cat['id']}"}
        if engine == "pulid":
            head.append("# PuLID 컬렉션은 common(quality·modest·neck·glasses)·pulid 기본값이 필요하다 — concept.yaml 에서 옮긴다")
        if engine == "kontext":
            head.append("# Kontext 컬렉션은 kontext(guidance·steps·seed)·lora 블록이 필요하다 — ani.yaml 참고")
        body["presets"] = presets_out
    head.append("# 서비스 공개는 카탈로그(catalog.json)·백엔드 허용 목록도 함께 바꿔야 한다")
    return "\n".join(head) + "\n" + yaml.safe_dump(body, allow_unicode=True, sort_keys=False, width=100)
