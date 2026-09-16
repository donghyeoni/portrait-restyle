"""로드밸런서 엔드투엔드 검증: 동시 요청이 여러 노드로 분배되는지 확인."""
import asyncio, pathlib, sys, time

import httpx

API = "http://127.0.0.1:8000"
SRC = pathlib.Path("data/faces/normalized")
OUT = pathlib.Path("outputs/api_test")


async def one(client, name, style, seed):
    t0 = time.perf_counter()
    data = (SRC / f"{name}.png").read_bytes()
    r = await client.post(
        f"{API}/generate",
        data={"prompt": "", "style": style, "seed": str(seed)},
        files={"image": (f"{name}.png", data, "image/png")},
        timeout=900,
    )
    dt = time.perf_counter() - t0
    if r.status_code != 200:
        return name, style, None, dt, r.text[:200]
    j = r.json()
    img = j["images"][0]
    ir = await client.get(
        f"{API}/image",
        params={"node": j["node"], "filename": img["filename"], "subfolder": img["subfolder"]},
        timeout=120,
    )
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{name}__{style}.png").write_bytes(ir.content)
    return name, style, j["node"], dt, None


async def main(names, style):
    async with httpx.AsyncClient() as c:
        t0 = time.perf_counter()
        res = await asyncio.gather(*[one(c, n, style, 1000) for n in names])
        total = time.perf_counter() - t0

    head = "{:<8}{:<10}{:<26}{:>8}".format("입력", "화풍", "배정 노드", "소요")
    print(head)
    print("-" * 56)
    for n, s, node, dt, err in res:
        line = "{:<8}{:<10}{:<26}{:>7.1f}s".format(n, s, node or "실패", dt)
        if err:
            line += "  " + err
        print(line)
    nodes = {r[2] for r in res if r[2]}
    print()
    print("동시 {}건 총 {:.1f}s, 사용된 노드 {}개".format(len(res), total, len(nodes)))
    return 0 if all(r[2] for r in res) else 1


if __name__ == "__main__":
    names = sys.argv[1].split(",") if len(sys.argv) > 1 else ["test1", "test2", "test4", "test5"]
    style = sys.argv[2] if len(sys.argv) > 2 else "vampire"
    raise SystemExit(asyncio.run(main(names, style)))
