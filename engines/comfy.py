"""ComfyUI 호출 공용. 세 엔진(pulid·kontext·inswapper 의 안경 단계)이 같이 쓴다.

  submit(node, graph) -> bytes    그래프를 넣고 첫 출력 이미지의 PNG 바이트를 돌려준다
  stage(src) -> str               입력 파일을 ComfyUI/input 에 고유 이름으로 복사, 이름 반환
  alive(node) -> bool
"""
from __future__ import annotations

import json, pathlib, shutil, time, urllib.request, uuid

import cv2
import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
COMFY_IN = ROOT / "ComfyUI" / "input"
DEFAULT_NODE = "http://127.0.0.1:8189"     # 할당 GPU 1


def alive(node: str) -> bool:
    try:
        urllib.request.urlopen(node + "/system_stats", timeout=3)
        return True
    except Exception:
        return False


def submit(node: str, graph: dict, timeout_s: float = 600.0) -> bytes:
    req = urllib.request.Request(
        node + "/prompt",
        data=json.dumps({"prompt": graph, "client_id": uuid.uuid4().hex}).encode(),
        headers={"Content-Type": "application/json"})
    pid = json.loads(urllib.request.urlopen(req, timeout=60).read())["prompt_id"]
    t0 = time.time()
    while True:
        h = json.loads(urllib.request.urlopen(node + "/history/" + pid, timeout=30).read())
        if pid in h:
            st = h[pid].get("status", {})
            if st.get("status_str") == "error":
                msg = [m for m in st.get("messages", []) if m[0] == "execution_error"]
                raise RuntimeError(str(msg or st.get("messages"))[:600])
            if h[pid].get("outputs"):
                im = list(h[pid]["outputs"].values())[0]["images"][0]
                url = "%s/view?filename=%s&subfolder=%s&type=output" % (
                    node, im["filename"], im["subfolder"])
                return urllib.request.urlopen(url, timeout=300).read()
        if time.time() - t0 > timeout_s:
            raise TimeoutError(f"ComfyUI {node} 응답 없음 ({timeout_s:.0f}s)")
        time.sleep(0.3)


def stage(src, prefix: str = "_in") -> str:
    """입력(경로 또는 BGR 배열)을 ComfyUI/input 에 고유 이름으로 놓고 그 이름을 돌려준다."""
    COMFY_IN.mkdir(parents=True, exist_ok=True)
    name = f"{prefix}_{uuid.uuid4().hex[:10]}.png"
    if isinstance(src, (str, pathlib.Path)):
        shutil.copy(src, COMFY_IN / name)
    else:
        cv2.imwrite(str(COMFY_IN / name), src)
    return name


def unstage(name: str) -> None:
    try:
        (COMFY_IN / name).unlink()
    except OSError:
        pass


def decode(data: bytes):
    return cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
