"""JupyterHub 원격 실행 헬퍼.

L40S 서버에 코드를 보내고 결과를 받는다. 커널 WebSocket 을 쓴다
(터미널보다 출력 파싱이 안정적).

    python scripts/remote/jup.py "print('hi')"
    python scripts/remote/jup.py --file local_script.py
"""
from __future__ import annotations
import sys as _sys
if hasattr(_sys.stdout, "reconfigure"):
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import argparse, json, os, pathlib, urllib.request, uuid

HOST = os.environ["JUP_HOST"]       # 예: 10.0.0.5
USER = os.environ["JUP_USER"]       # JupyterHub 계정
TOKEN = os.environ.get("JUP_TOKEN", "")
BASE = f"http://{HOST}/user/{USER}"


def api(path: str, method: str = "GET", body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{BASE}{path}", data=data, method=method,
                                 headers={"Authorization": f"token {TOKEN}",
                                          "Content-Type": "application/json"})
    r = urllib.request.urlopen(req, timeout=60).read()
    return json.loads(r) if r else None


def run(code: str, timeout: float = 300) -> str:
    """커널을 띄워 코드를 실행하고 stdout/stderr 를 합쳐 반환."""
    from websocket import create_connection  # websocket-client

    k = api("/api/kernels", "POST", {"name": "python3"})
    kid = k["id"]
    try:
        ws = create_connection(
            f"ws://{HOST}/user/{USER}/api/kernels/{kid}/channels",
            header=[f"Authorization: token {TOKEN}"], timeout=timeout)
        msg_id = uuid.uuid4().hex
        ws.send(json.dumps({
            "header": {"msg_id": msg_id, "username": "x", "session": uuid.uuid4().hex,
                       "msg_type": "execute_request", "version": "5.3"},
            "parent_header": {}, "metadata": {},
            "content": {"code": code, "silent": False, "store_history": False,
                        "user_expressions": {}, "allow_stdin": False,
                        "stop_on_error": True},
        }))
        out = []
        while True:
            m = json.loads(ws.recv())
            if m.get("parent_header", {}).get("msg_id") != msg_id:
                continue
            t = m["msg_type"]
            c = m.get("content", {})
            if t == "stream":
                out.append(c.get("text", ""))
            elif t in ("execute_result", "display_data"):
                out.append(c.get("data", {}).get("text/plain", ""))
            elif t == "error":
                out.append("\n".join(c.get("traceback", [])))
            elif t == "status" and c.get("execution_state") == "idle":
                break
        ws.close()
        return "".join(out)
    finally:
        try: api(f"/api/kernels/{kid}", "DELETE")
        except Exception: pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("code", nargs="?")
    ap.add_argument("--file")
    ap.add_argument("--timeout", type=float, default=300)
    a = ap.parse_args()
    if not TOKEN:
        print("JUP_TOKEN 환경변수가 없다"); return 1
    code = pathlib.Path(a.file).read_text(encoding="utf-8-sig") if a.file else a.code
    if not code:
        print("실행할 코드가 없다"); return 1
    print(run(code, a.timeout))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
