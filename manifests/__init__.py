"""컬렉션 매니페스트 로더.

컬렉션 = 엔진 + 프리셋(프롬프트·참고 이미지·LoRA) + 전처리·후처리 목록.
추가 예정·미사용은 `manifests/backlog/` 에 둔다 — 서비스 코드 표에 섞이지 않게. 코드가 아니라
`manifests/<id>.yaml` 한 장으로 정의한다. 새 화풍은 파일을 하나 추가하면 끝이고,
백엔드 규약(docs/BACKEND_CONTRACT.md)의 `stylePreset` 코드는 각 프리셋의 키다.

    from manifests import all_collections, by_code
    coll, preset = by_code("DOCTOR")      # -> jobs 컬렉션, {"key": "doctor", ...}
"""
from __future__ import annotations

import functools
import pathlib

import yaml

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
MODEL_VERSION = "portrait-restyle-v1"                 # 백엔드 메시지의 generation.modelVersion 과 대조
EXECUTION_TARGET = {"inswapper": "EC2_CPU", "pulid": "GPU", "kontext": "GPU", "original": "EC2_CPU"}   # 백엔드 전달서(2026-09-08): GPU 업체 중립. original=원본 카드(생성 없음)
LEGACY_TARGETS = {"CLOUD_RUN_GPU": "GPU"}                                            # 구 매니페스트 호환
RARITIES = ("N", "R", "SR", "SSR")


class Collection(dict):
    """yaml 내용 그대로의 dict 에 편의 속성만 얹은 것."""

    @property
    def id(self) -> str:
        return self["id"]

    @property
    def engine(self) -> str:
        return self["engine"]

    @property
    def enabled(self) -> bool:
        return bool(self.get("enabled", True))

    @property
    def version(self) -> str:
        return self["version"]

    def presets(self, include_disabled: bool = False) -> dict[str, dict]:
        """코드 -> 프리셋 dict. 프리셋은 항상 dict 로 정규화한다 (키·enabled 포함)."""
        out = {}
        for code, p in self["presets"].items():
            if isinstance(p, str):
                p = {"key": p}
            p = {"enabled": True, **p}
            if include_disabled or p["enabled"]:
                out[code] = p
        return out

    def preset(self, code: str) -> dict:
        return self.presets(include_disabled=True)[code]

    def output_dir(self) -> pathlib.Path:
        return ROOT / self.get("output", {}).get("dir", f"data/output/{self.id}")

    def reference_dir(self) -> pathlib.Path | None:
        r = self.get("reference")
        return ROOT / r if r else None


UNUSED = HERE / "backlog"      # 추가 예정·미사용 컬렉션. 러너·코드 표에 나오지 않는다 (--include-disabled 로만). 로컬 전용


@functools.lru_cache(maxsize=None)
def load(coll_id: str) -> Collection:
    p = HERE / f"{coll_id}.yaml"
    if not p.exists():
        p = UNUSED / f"{coll_id}.yaml"
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    assert data["id"] == coll_id, f"{p}: id 가 파일명과 다르다 ({data['id']})"
    return Collection(data)


@functools.lru_cache(maxsize=None)
def all_collections(include_disabled: bool = False) -> dict[str, Collection]:
    out = {}
    files = sorted(HERE.glob("*.yaml")) + (sorted(UNUSED.glob("*.yaml")) if include_disabled else [])
    for p in files:
        c = load(p.stem)
        if include_disabled or c.enabled:
            out[c.id] = c
    return out


def by_code(code: str) -> tuple[Collection, dict]:
    """stylePreset 코드로 (컬렉션, 프리셋) 을 찾는다. 없으면 KeyError."""
    for c in all_collections().values():
        ps = c.presets()
        if code in ps:
            return c, ps[code]
    raise KeyError(f"알 수 없는 stylePreset 코드: {code}")


def catalog() -> list[dict]:
    """백엔드에 내주는 목록 (GET /collections). 코드·컬렉션·표시 이름·해상도·엔진."""
    rows = []
    for c in all_collections().values():
        size = c.get("output", {})
        for code, p in c.presets().items():
            rows.append({"code": code, "collection": c.id, "display": c.get("display", c.id),
                         "preset": p["key"], "engine": c.engine, "version": c.version,
                         "width": size.get("width"), "height": size.get("height"),
                         "tier": c.get("tier")})
    return rows


def normalize_target(v: str) -> str:
    """구 값(CLOUD_RUN_GPU)을 새 값(GPU)으로."""
    return LEGACY_TARGETS.get(v, v)


def catalog_payload() -> dict:
    """백엔드 전달서 3절 GET /collections 응답 (flat items[], rarity 필수). GPU 를 깨우지 않고 매니페스트만 읽는다.

    catalogVersion 은 활성 컬렉션들의 (id, version, 프리셋 코드) 해시 — 매니페스트가 바뀌면 값이 바뀐다.
    """
    import hashlib, json
    items = []
    for c in all_collections().values():
        size = c.get("output", {})
        for code, p in c.presets(include_disabled=True).items():
            rarity = p.get("rarity") or c.get("tier")
            assert rarity in RARITIES, f"{c.id}/{code}: rarity 가 필요하다 (N/R/SR/SSR), 지금 {rarity!r}"
            # width/height 는 최종 IMAGE·CUTOUT 파일 크기 (백엔드 확정 2026-09-08). 업스케일이 있으면 곱한다 (WEBTOON 1792x2304)
            up = float(size.get("upscale", 1) or 1)
            items.append({"stylePreset": code, "collection": c.id, "rarity": rarity,
                          "executionTarget": EXECUTION_TARGET[c.engine], "enabled": bool(p["enabled"]),
                          "width": int(round(size["width"] * up)) if size.get("width") else None,
                          "height": int(round(size["height"] * up)) if size.get("height") else None,
                          "promptTemplateVersion": c.version, "engine": c.engine})
    sig = hashlib.sha1(json.dumps(items, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:8]
    return {"catalogVersion": sig, "modelVersion": MODEL_VERSION, "items": items}


def write_catalog(path: pathlib.Path | None = None) -> pathlib.Path:
    """정적 JSON 계약(manifests/catalog.json). 저장소에 함께 둔다."""
    import json
    path = path or HERE / "catalog.json"
    path.write_text(json.dumps(catalog_payload(), ensure_ascii=False, indent=1), encoding="utf-8")
    return path
