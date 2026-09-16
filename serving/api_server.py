"""FastAPI 로드밸런서 — L40S 4장 ComfyUI 인스턴스 앞단 (명세서 7절).

명세서의 예시는 라운드로빈(`itertools.cycle`)이다. 그것은 GPU 가 바쁜지
확인하지 않으므로, 요청마다 소요가 다르면(5초 vs 30초) 무거운 작업이
몰린 인스턴스에 계속 쌓인다.

**여기서는 최소 부하(least-loaded) 방식을 쓴다.**
ComfyUI 의 `/queue` 로 각 인스턴스의 대기열 길이를 읽어 가장 짧은 곳에 보낸다.
상태 조회는 짧은 TTL 로 캐시해 매 요청마다 4번 왕복하는 비용을 없앤다.

  - 헬스체크 실패한 인스턴스는 자동 제외, 복구되면 자동 복귀
  - 제출 실패 시 다른 인스턴스로 재시도
  - 동시 요청 수를 세마포어로 제한(인스턴스당 큐가 무한히 쌓이지 않게)

실행:
    python -m uvicorn serving.api_server:app --host 0.0.0.0 --port 8000   (저장소 루트에서)
"""
from __future__ import annotations

import asyncio
import logging
import os
import pathlib
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse, Response

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from steps.faces import biggest, face_app   # noqa: E402

log = logging.getLogger("portrait-restyle")

# 할당 GPU 는 device 1 하나 -> 기본 8189 한 대. 더 쓸 때만 PORTRAIT_NODES 로 포트를 준다.
#   PORTRAIT_NODES=8189 python -m uvicorn serving.api_server:app ...  (저장소 루트에서)
_PORTS = [int(x) for x in os.environ.get("PORTRAIT_NODES", "8189").split(",")]  # 할당 GPU 1 -> 8189 한 대
NODES = [f"http://127.0.0.1:{p}" for p in _PORTS]
HEALTH_TTL = 3.0          # 대기열 캐시 유효시간(초)
MAX_INFLIGHT = 2          # 인스턴스당 동시 제출 상한
SUBMIT_TIMEOUT = 30.0
POLL_TIMEOUT = 600.0

app = FastAPI(title="Photo AI Load Balancer")


@dataclass
class Node:
    url: str
    healthy: bool = True
    pending: int = 0            # ComfyUI 큐 길이(캐시)
    inflight: int = 0           # 이 서버가 보낸 뒤 아직 안 끝난 수
    checked_at: float = 0.0
    sem: asyncio.Semaphore = field(default_factory=lambda: asyncio.Semaphore(MAX_INFLIGHT))

    @property
    def load(self) -> int:
        return self.pending + self.inflight


nodes: list[Node] = [Node(u) for u in NODES]
client: httpx.AsyncClient | None = None


@app.on_event("startup")
async def _startup() -> None:
    global client
    client = httpx.AsyncClient(timeout=httpx.Timeout(POLL_TIMEOUT, connect=5.0))
    asyncio.create_task(_health_loop())


@app.on_event("shutdown")
async def _shutdown() -> None:
    if client:
        await client.aclose()


async def _refresh(n: Node) -> None:
    """대기열 길이와 생존 여부를 갱신한다."""
    try:
        r = await client.get(f"{n.url}/queue", timeout=3.0)
        r.raise_for_status()
        q = r.json()
        n.pending = len(q.get("queue_running", [])) + len(q.get("queue_pending", []))
        n.healthy = True
    except Exception:
        n.healthy = False
    n.checked_at = time.monotonic()


async def _health_loop() -> None:
    while True:
        await asyncio.gather(*(_refresh(n) for n in nodes), return_exceptions=True)
        await asyncio.sleep(HEALTH_TTL)


async def _pick() -> Node:
    """가장 한가한 인스턴스. 캐시가 만료됐으면 먼저 갱신한다."""
    now = time.monotonic()
    stale = [n for n in nodes if now - n.checked_at > HEALTH_TTL]
    if stale:
        await asyncio.gather(*(_refresh(n) for n in stale), return_exceptions=True)
    alive = [n for n in nodes if n.healthy]
    if not alive:
        raise HTTPException(503, "가용한 GPU 인스턴스가 없습니다")
    return min(alive, key=lambda n: (n.load, n.url))


@app.get("/health")
async def health() -> dict[str, Any]:
    await asyncio.gather(*(_refresh(n) for n in nodes), return_exceptions=True)
    return {
        "status": "ok" if any(n.healthy for n in nodes) else "degraded",
        "nodes": [{"url": n.url, "healthy": n.healthy,
                   "pending": n.pending, "inflight": n.inflight} for n in nodes],
    }


async def _submit(node: Node, graph: dict, cid: str) -> str:
    r = await client.post(f"{node.url}/prompt",
                          json={"prompt": graph, "client_id": cid},
                          timeout=SUBMIT_TIMEOUT)
    if r.status_code != 200:
        raise RuntimeError(f"{node.url} 제출 실패 {r.status_code}: {r.text[:300]}")
    return r.json()["prompt_id"]


async def _wait(node: Node, pid: str) -> dict:
    t0 = time.monotonic()
    delay = 0.25
    while time.monotonic() - t0 < POLL_TIMEOUT:
        r = await client.get(f"{node.url}/history/{pid}", timeout=15.0)
        h = r.json()
        if pid in h:
            st = h[pid].get("status", {})
            if st.get("status_str") == "error":
                # ComfyUI 는 status 에 전체 실행 로그를 담아 준다.
                # 그대로 올리면 응답에 내부 구조가 통째로 노출되므로
                # 사람이 읽을 한 줄만 뽑고 전문은 서버 로그로 보낸다.
                detail = ""
                for kind, payload in st.get("messages", []):
                    if kind == "execution_error":
                        detail = "{}: {}".format(payload.get("node_type", "?"),
                                                 payload.get("exception_message", ""))[:200]
                        break
                log.error("노드 실행 실패 %s: %s", pid, st)
                raise RuntimeError(detail or "워크플로우 실행 실패")
            return h[pid]
        await asyncio.sleep(delay)
        delay = min(delay * 1.3, 2.0)     # 점진적 백오프
    raise TimeoutError(f"{pid} 타임아웃")


# --- 성별 판별 -------------------------------------------------------------
# 요구사항 b(의상을 화풍에 맞게)를 지키려면 남/여 복식을 갈라야 한다.
# gender 는 호출자가 필수로 준다. auto 를 명시하면 InsightFace genderage 로
# 판별하는데, 이 표본에서 정확도는 11/12 였다 — test8(여성)을 male 로 냈다.
# 검출 실패가 아니라 자신 있게 틀린 경우라 폴백 규칙으로는 못 걸러낸다.
# 그래서 기본값을 두지 않는다. 아무것도 안 해도 굴러가면 조용히 틀린다.
def _inspect_face(data: bytes) -> tuple[int, str]:
    """(검출된 얼굴 수, 가장 큰 얼굴의 성별) 을 돌려준다.

    성별 판별만 하던 것을 입력 검증까지 겸하게 했다.
    검증 없이 두면 얼굴 없는 사진에도 PuLID 가 그냥 아무 얼굴이나 그려낸다 —
    사용자는 200 응답을 받고 남의 얼굴을 돌려받는다.
    이미지가 깨져 열리지 않으면 예외를 그대로 올려 400 으로 처리한다.
    """
    import cv2
    import numpy as np
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("이미지를 읽을 수 없습니다")
    faces = face_app('antelopev2', modules=['detection', 'genderage']).get(img)
    if not faces:
        return 0, "male"
    return len(faces), ("female" if biggest(faces).sex == "F" else "male")


@app.get("/styles")
async def list_styles() -> JSONResponse:
    """사용 가능한 화풍. 클라이언트가 목록을 따로 들고 있으면 반드시 어긋난다 —
    실제로 scripts/gen.py 가 전통의상 3종 추가를 못 따라가 400 을 냈다."""
    from engines.pulid import STYLES
    return JSONResponse({"styles": sorted(STYLES)})


@app.post("/generate")
async def generate(
    prompt: str = Form(""),   # 비우면 화풍 프리셋의 프롬프트 사용
    image: UploadFile = File(...),
    style: str = Form("vampire"),
    seed: int = Form(0),
    gender: str = Form(...),   # 필수. male|female|auto
) -> JSONResponse:
    """증명사진 + 프롬프트 -> 컨셉 이미지.

    워크플로우 그래프는 workflows/flux_pulid.py 에서 만든다.
    """
    from engines.pulid import build_graph   # 지연 임포트

    from engines.pulid import STYLES

    if style not in STYLES:
        # 예전엔 조용히 vampire 로 떨어졌다. 오타가 다른 화풍으로 둔갑한다.
        raise HTTPException(400, f"알 수 없는 화풍 '{style}'. "
                                 f"가능한 값: {', '.join(sorted(STYLES))}")
    if gender not in ("male", "female", "auto"):
        # 필수 필드다. 자동 판별을 쓰려면 auto 를 명시해야 한다.
        # 기본값으로 두었더니 test8(여성)이 male 로 판별돼 남성 복식이 입혀졌고,
        # 호출자는 그런 일이 있었는지도 몰랐다. 표본 정확도는 11/12 였다.
        raise HTTPException(400, f"gender 는 male|female|auto 여야 합니다 (받은 값: '{gender}'). "
                                 "자동 판별을 쓰려면 auto 를 명시하세요")

    data = await image.read()
    if not data:
        raise HTTPException(400, "빈 이미지입니다")

    try:
        n_faces, detected = await asyncio.to_thread(_inspect_face, data)
    except Exception as e:
        log.warning("입력 이미지 처리 실패: %s", e)
        raise HTTPException(400, "이미지를 읽을 수 없습니다. PNG/JPEG 파일인지 확인하세요")
    if n_faces == 0:
        raise HTTPException(400, "얼굴을 찾지 못했습니다. "
                                 "정면·단색 배경·상반신 증명사진을 넣어 주세요")
    if n_faces > 1:
        log.info("얼굴 %d개 검출 — 가장 큰 얼굴을 씁니다", n_faces)

    sex = gender if gender in ("male", "female") else detected

    cid = uuid.uuid4().hex
    last_err: Exception | None = None

    # 최대 3개 인스턴스까지 순차 재시도
    for _ in range(min(3, len(nodes))):
        node = await _pick()
        async with node.sem:
            node.inflight += 1
            try:
                files = {"image": (image.filename or "input.png", data,
                                   image.content_type or "image/png")}
                up = await client.post(f"{node.url}/upload/image", files=files,
                                       data={"overwrite": "true"}, timeout=60.0)
                if up.status_code != 200:
                    raise RuntimeError(f"업로드 실패 {up.status_code}")
                name = up.json()["name"]

                # 인스턴스들이 output 디렉터리를 공유하므로 요청별 접두사가 필요하다
                graph = build_graph(face_image=name, prompt=prompt, style=style,
                                    seed=seed or None, gender=sex,
                                    filename_prefix=f"flux_pulid/{cid[:8]}")
                pid = await _submit(node, graph, cid)
                hist = await _wait(node, pid)

                imgs = []
                for out in hist.get("outputs", {}).values():
                    imgs += out.get("images", [])
                if not imgs:
                    raise RuntimeError("결과 이미지 없음")
                first = imgs[0]
                return JSONResponse({
                    "status": "success",
                    "node": node.url,
                    "gender": sex,
                    "gender_source": "auto" if gender == "auto" else "caller",
                    "prompt_id": pid,
                    "images": [{"filename": i["filename"],
                                "subfolder": i.get("subfolder", ""),
                                "url": f"/image?node={node.url}"
                                       f"&filename={i['filename']}"
                                       f"&subfolder={i.get('subfolder','')}"}
                               for i in imgs],
                    "preview": first["filename"],
                })
            except Exception as e:      # noqa: BLE001
                last_err = e
                node.healthy = False    # 다음 픽에서 제외, 헬스루프가 복구 판단
            finally:
                node.inflight -= 1

    log.error("전 인스턴스 실패: %r", last_err)
    raise HTTPException(502, f"생성에 실패했습니다: {last_err}")


@app.get("/image")
async def get_image(node: str, filename: str, subfolder: str = "") -> Response:
    """생성 결과 이미지를 프록시로 내려준다."""
    if node not in NODES:
        raise HTTPException(400, "알 수 없는 노드")
    r = await client.get(f"{node}/view",
                         params={"filename": filename, "subfolder": subfolder,
                                 "type": "output"}, timeout=60.0)
    if r.status_code != 200:
        raise HTTPException(404, "이미지를 찾을 수 없습니다")
    return Response(r.content, media_type=r.headers.get("content-type", "image/png"))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
