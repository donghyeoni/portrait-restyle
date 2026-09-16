"""인물 특징 반영 강도 스윕.

API 를 거치지 않고 ComfyUI 노드에 직접 넣는다 — 실험 파라미터를 위해
API 계약을 넓히지 않기 위해서다.

  python eval/scripts/sweep_identity.py --axis pulid_weight=0.9,1.0 --axis guidance=2.5,3.2,4.0
"""
from __future__ import annotations

import argparse, itertools, json, pathlib, shutil, sys, time, urllib.request, uuid

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "eval" / "scripts"))
from engines.pulid import build_graph   # noqa: E402

SRC = ROOT / "data" / "faces" / "normalized"
GENDERS = json.loads((ROOT / "data" / "faces" / "gender.json").read_text(encoding="utf-8"))


def submit(node: str, graph: dict) -> dict:
    req = urllib.request.Request(
        node + "/prompt",
        data=json.dumps({"prompt": graph, "client_id": uuid.uuid4().hex}).encode(),
        headers={"Content-Type": "application/json"})
    pid = json.loads(urllib.request.urlopen(req, timeout=60).read())["prompt_id"]
    while True:
        h = json.loads(urllib.request.urlopen(f"{node}/history/{pid}", timeout=30).read())
        if pid in h:
            st = h[pid].get("status", {})
            if st.get("status_str") == "error":
                raise RuntimeError(f"실행 실패 {pid}")
            if h[pid].get("outputs"):
                return h[pid]
        time.sleep(0.3)


def main(a) -> int:
    axes = {}
    for spec in a.axis:
        k, v = spec.split("=", 1)
        axes[k] = [float(x) if "." in x or k in ("guidance", "pulid_weight") else int(x)
                   for x in v.split(",")]
    names = a.inputs.split(",")
    combos = [dict(zip(axes, c)) for c in itertools.product(*axes.values())]
    out = pathlib.Path(a.out); shutil.rmtree(out, ignore_errors=True); out.mkdir(parents=True)

    print("{}개 조합 x {}명 = {}장".format(len(combos), len(names), len(combos) * len(names)))
    for n in names:
        shutil.copy(SRC / f"{n}.png", ROOT / "ComfyUI" / "input" / f"{n}.png")

    t0 = time.perf_counter()
    for i, cfg in enumerate(combos):
        tag = "_".join(f"{k}{v}" for k, v in cfg.items())
        for n in names:
            g = build_graph(f"{n}.png", style=a.style, seed=a.seed,
                            gender=GENDERS[n], filename_prefix=f"sweep/{tag}_{n}", **cfg)
            hist = submit(a.node, g)
            im = list(hist["outputs"].values())[0]["images"][0]
            data = urllib.request.urlopen(
                f"{a.node}/view?filename={im['filename']}&subfolder={im['subfolder']}&type=output",
                timeout=120).read()
            (out / f"{n}__{tag}.png").write_bytes(data)
        print("  [{}/{}] {}  ({:.0f}s 경과)".format(i + 1, len(combos), tag,
                                                    time.perf_counter() - t0), flush=True)
    print("총 {:.0f}s -> {}".format(time.perf_counter() - t0, out))
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--axis", action="append", required=True, help="예: guidance=2.5,3.2")
    p.add_argument("--inputs", default="test1,test2,test5,test6,test8,test10")
    p.add_argument("--style", default="vampire")
    p.add_argument("--seed", type=int, default=1000)
    p.add_argument("--node", default="http://127.0.0.1:8189")
    p.add_argument("--out", default="outputs/style/sweep")
    raise SystemExit(main(p.parse_args()))
