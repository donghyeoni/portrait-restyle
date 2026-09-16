"""매니페스트(manifests/*.yaml)를 읽어 data/input 전체를 만든다 (서버에서 실행).

  data/input/{man,woman}/NNN.png  ->  data/output/<컬렉션>/{man,woman}/NNN/<preset>.png

성별은 폴더 이름이 정한다 (man -> male, woman -> female). 판별하지 않는다.
컬렉션이 어느 엔진을 쓰는지, 프리셋이 몇 개인지는 yaml 이 정하고 여기서는 엔진별 호출만 한다.

  python runner.py                              # 활성 컬렉션 전부, 없는 장만
  python runner.py --collection jobs --who man/000 --force
  python runner.py --code DOCTOR CYBERPUNK WEBTOON --who man/000 --out outputs/_verify
  python runner.py --list                       # stylePreset 코드 목록 (백엔드에 주는 표)
"""
from __future__ import annotations

import argparse, json, os, pathlib, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor

import cv2

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from manifests import all_collections, by_code, catalog          # noqa: E402
from engines.comfy import DEFAULT_NODE, alive, decode, stage, submit, unstage   # noqa: E402

PYBIN = sys.executable
IN = ROOT / "data" / "input"
GENDER = {"man": "male", "woman": "female"}
# 할당 GPU 는 device 1 하나. 빌린 날만 PORTRAIT_NODES=8188,8189,8190,8191
NODES = ["http://127.0.0.1:%s" % p for p in os.environ.get("PORTRAIT_NODES", "8189").split(",")]
GPU_ENV = {**os.environ, "CUDA_VISIBLE_DEVICES": os.environ.get("CUDA_VISIBLE_DEVICES", "1")}


# ---------------------------------------------------------------- 공용 ----
def people(who):
    for g in ("man", "woman"):
        for p in sorted((IN / g).glob("*.*")):
            if p.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp"):
                continue
            key = f"{g}/{p.stem}"
            if who and key not in who:
                continue
            yield g, p


_FA: dict = {}


def face_of(img):
    """antelopev2 로 가장 큰 얼굴. 없으면 None."""
    from steps.faces import biggest, face_app
    if "fa" not in _FA:
        _FA["fa"] = face_app("antelopev2", ctx_id=-1)
    fs = _FA["fa"].get(img)
    return biggest(fs) if fs else None


def wears_glasses(img, face) -> bool:
    from steps.glasses import THRESH, glasses_ratio_ms
    return glasses_ratio_ms(img, face) > THRESH


def out_path(coll, out_root, g, stem, key):
    base = pathlib.Path(out_root) / coll.id if out_root else coll.output_dir()
    return base / g / stem / f"{key}.png"


# --------------------------------------------------------------- 엔진별 ----
def run_inswapper(coll, g, p, codes, out_root, node, force):
    """참고 이미지 폴더 전체(또는 --only)를 한 번의 서브프로세스로 만든다. 출력 이름 = 참고 파일명 = 프리셋 키."""
    presets = coll.presets()
    want = {presets[c]["key"] for c in codes}
    dst = out_path(coll, out_root, g, p.stem, "x").parent
    todo = [k for k in want if force or not (dst / f"{k}.png").exists()]
    if not todo:
        return 0, "완성됨"
    r = subprocess.run(
        [PYBIN, "-m", "engines.inswapper", "--src-file", str(p),
         "--ref-dir", str(coll.reference_dir() / GENDER[g]), "--only", ",".join(todo),
         "--gpu", "0", "--glasses", coll.get("glasses", "off"), "--node", node, "--out", str(dst)],
        cwd=str(ROOT), capture_output=True, text=True, timeout=3600, env=GPU_ENV)
    err = ""
    if r.returncode != 0:
        lines = r.stderr.strip().splitlines()
        err = lines[-1][:160] if lines else "실패"
    return sum((dst / f"{k}.png").exists() for k in todo), err


def run_pulid(coll, g, p, codes, out_root, node, force):
    from engines.pulid import build_graph
    presets = coll.presets()
    img = cv2.imread(str(p))
    face = face_of(img)
    if face is None:
        return 0, "얼굴 검출 실패"
    gl = wears_glasses(img, face)
    name = stage(p, f"_pl_{g}_{p.stem}")
    n = 0
    try:
        for c in codes:
            key = presets[c]["key"]
            dst = out_path(coll, out_root, g, p.stem, key)
            if not force and dst.exists():
                continue
            gr = build_graph(name, style=key, gender=GENDER[g], seed=coll.get("pulid", {}).get("seed", 1000),
                             glasses=gl, filename_prefix=f"pulid/{g}_{p.stem}_{key}")
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(submit(node, gr))
            n += 1
    finally:
        unstage(name)
    return n, ("안경" if gl else "")


def _weather_frame(coll, img):
    """사계절 전처리: 피부 리터치 -> 작은 얼굴 상반신 크롭 -> 작은 이미지 업스케일."""
    from steps.crop import upper_body_box
    from steps.retouch import smooth_skin
    from steps.upscale import upscale
    pre = coll.get("pre", {})
    W_, H_ = coll["output"]["width"], coll["output"]["height"]
    H, W = img.shape[:2]
    face = face_of(img)
    if face is not None:
        if pre.get("retouch", 0) > 0:
            img = smooth_skin(img, face, pre["retouch"])
        b = face.bbox
        if (b[3] - b[1]) / H < pre.get("min_face_ratio", 0):
            x0, y0, x1, y1 = upper_body_box(W, H, b, above=0.8, below=1.6, ar=W_ / H_)
            img = img[y0:y1, x0:x1]
    if min(img.shape[:2]) < pre.get("min_side", 0):
        img = upscale(img, 2.0)
    return img


def run_kontext(coll, g, p, codes, out_root, node, force):
    from engines.kontext import edit
    from steps.upscale import upscale
    presets = coll.presets()
    kx = coll.get("kontext", {})
    lora = coll.get("lora") or {}
    out = coll["output"]
    gender = GENDER[g]
    src = cv2.imread(str(p))
    if "pre" in coll:
        src = _weather_frame(coll, src)
    n = 0
    for c in codes:
        pr = presets[c]
        dst = out_path(coll, out_root, g, p.stem, pr["key"])
        if not force and dst.exists():
            continue
        if "prompt" in pr:                       # 프리셋이 문구를 직접 가짐 (ani)
            text = pr["prompt"]
        else:                                    # 컬렉션 템플릿 + 프리셋 값 (weather)
            tmpl = coll["prompt"][gender]
            text = tmpl.format(scene=pr["scene"], outfit=pr[f"outfit_{gender}"])
        img = edit(src, text, width=out["width"], height=out["height"], guidance=kx.get("guidance", 2.5),
                   steps=kx.get("steps", 20), seed=kx.get("seed", 1000), lora=lora.get("name"),
                   lora_strength=lora.get("strength", 1.0), trigger=lora.get("trigger"), node=node,
                   tag=f"{coll.id}_{g}_{p.stem}_{pr['key']}")
        if out.get("upscale"):
            img = upscale(img, float(out["upscale"]))
        dst.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(dst), img)
        n += 1
    return n, ""


RUN = {"inswapper": run_inswapper, "pulid": run_pulid, "kontext": run_kontext}


# ------------------------------------------------------------------ main ----
def main(a) -> int:
    if a.list:
        print(json.dumps(catalog(), ensure_ascii=False, indent=1))
        return 0
    colls = all_collections(include_disabled=a.include_disabled)
    # 어느 컬렉션의 어느 코드를 만들지
    plan: dict[str, list[str]] = {}
    if a.code:
        for code in a.code:
            c, _ = by_code(code)
            plan.setdefault(c.id, []).append(code)
    else:
        for cid, c in colls.items():
            if a.collection and cid not in a.collection:
                continue
            plan[cid] = list(c.presets())
    who = set(a.who) if a.who else None
    todo = list(people(who))
    nodes = [n for n in NODES if alive(n)] or [DEFAULT_NODE]
    print(f"입력 {len(todo)}명  컬렉션 {', '.join(f'{k}({len(v)})' for k, v in plan.items())}  ComfyUI {len(nodes)}대")
    t_all = time.perf_counter()

    for cid, codes in plan.items():
        coll = colls.get(cid) or all_collections(include_disabled=True)[cid]
        fn = RUN[coll.engine]
        print(f"\n=== {cid}  [{coll.engine}] {coll.version} ===")

        def job(i_gp):
            i, (g, p) = i_gp
            t = time.perf_counter()
            try:
                n, note = fn(coll, g, p, codes, a.out, nodes[i % len(nodes)], a.force)
            except Exception as e:                       # noqa: BLE001
                n, note = 0, f"실패 {type(e).__name__}: {str(e)[:120]}"
            return f"  {g}/{p.stem:<5} {n:>3}장  {time.perf_counter()-t:6.1f}s  {note}"

        # inswapper 는 CPU/서브프로세스라 직렬, ComfyUI 엔진은 노드 수만큼 병렬
        workers = 1 if coll.engine == "inswapper" else max(1, len(nodes))
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for line in ex.map(job, enumerate(todo)):
                print(line)

    print(f"\n총 {time.perf_counter()-t_all:.0f}s")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--collection", nargs="*", help="jobs zodiac concept ani 처럼. 비우면 활성 컬렉션 전부")
    ap.add_argument("--code", nargs="*", help="stylePreset 코드로 골라 만들기 (DOCTOR WEBTOON ...)")
    ap.add_argument("--who", nargs="*", help="man/008 처럼. 비우면 전부")
    ap.add_argument("--out", default="", help="출력 루트를 바꾼다 (검증용). 기본은 매니페스트의 output.dir")
    ap.add_argument("--force", action="store_true", help="이미 있는 장도 다시 만든다")
    ap.add_argument("--include-disabled", action="store_true", help="enabled: false 컬렉션도 포함")
    ap.add_argument("--list", action="store_true", help="코드 목록(JSON) 출력")
    raise SystemExit(main(ap.parse_args()))
