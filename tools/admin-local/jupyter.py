"""GPU 서버의 JupyterHub 로 실험실 실행기를 올리고 돌린다.

운영 GPU 서비스(8080)나 ComfyUI 설정은 건드리지 않는다. Jupyter 의 파일 API 로 입력과 실행기를 올리고,
커널에서 실행기를 백그라운드 프로세스로 띄운 뒤 결과 파일을 받아 온다.
토큰은 JupyterHub 의 Token 페이지에서 사용자가 직접 만들어 local.json 에 넣는다(저장소에는 올라가지 않는다).
"""
from __future__ import annotations

import base64
import json
import shlex
import threading
import urllib.error
import urllib.request
import uuid

import websocket


class JupyterError(RuntimeError):
    pass


class Jupyter:
    def __init__(self, cfg: dict):
        jup = cfg.get("jupyter") or {}
        self.base = (jup.get("url") or "").rstrip("/")
        self.token = jup.get("token") or ""
        self.gpu = cfg.get("gpu") or {}
        self.lab_dir = self.gpu.get("labDir", "lab").strip("/")
        self._kernel: str | None = None
        self._lock = threading.Lock()

    @property
    def ready(self) -> bool:
        return bool(self.base and self.token and self.gpu.get("codeRoot") and self.gpu.get("node"))

    def _request(self, method: str, path: str, body=None, raw: bool = False, timeout: int = 60):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Authorization": f"token {self.token}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as res:
                payload = res.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise JupyterError(f"Jupyter {method} {path} → {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise JupyterError(f"GPU 서버에 연결하지 못했습니다: {exc.reason}") from exc
        if raw:
            return payload
        return json.loads(payload) if payload else None

    # ── 파일 ──
    def mkdir(self, path: str):
        if self._request("GET", f"/api/contents/{path}?content=0") is None:
            self._request("PUT", f"/api/contents/{path}", {"type": "directory"})

    def put_file(self, path: str, data: bytes):
        self._request("PUT", f"/api/contents/{path}",
                      {"type": "file", "format": "base64", "content": base64.b64encode(data).decode()}, timeout=120)

    def get_file(self, path: str) -> bytes | None:
        return self._request("GET", f"/files/{path}", raw=True, timeout=120)

    # ── 커널 ──
    def _kernel_id(self) -> str:
        with self._lock:
            if self._kernel and self._request("GET", f"/api/kernels/{self._kernel}") is not None:
                return self._kernel
            self._kernel = self._request("POST", "/api/kernels", {"name": "python3"})["id"]
            return self._kernel

    def execute(self, code: str, timeout: int = 60) -> str:
        kid = self._kernel_id()
        url = self.base.replace("http://", "ws://").replace("https://", "wss://") + f"/api/kernels/{kid}/channels"
        ws = websocket.create_connection(url, header=[f"Authorization: token {self.token}"], timeout=timeout)
        msg_id = uuid.uuid4().hex
        ws.send(json.dumps({
            "header": {"msg_id": msg_id, "username": "lab", "session": uuid.uuid4().hex, "msg_type": "execute_request", "version": "5.3"},
            "parent_header": {}, "metadata": {}, "channel": "shell",
            "content": {"code": code, "silent": False, "store_history": False, "user_expressions": {}, "allow_stdin": False},
        }))
        out, error = [], None
        try:
            while True:
                msg = json.loads(ws.recv())
                if msg.get("parent_header", {}).get("msg_id") != msg_id:
                    continue
                kind = msg.get("msg_type")
                if kind == "stream":
                    out.append(msg["content"]["text"])
                elif kind == "error":
                    error = f'{msg["content"]["ename"]}: {msg["content"]["evalue"]}'
                elif kind == "status" and msg["content"]["execution_state"] == "idle":
                    break
        finally:
            ws.close()
        if error:
            raise JupyterError(error)
        return "".join(out)

    # ── 실험 ──
    def start_run(self, run_id: str, request: dict, images: dict[str, bytes], runner: bytes, runner_lib: bytes):
        """images: {"input.png": ..., "style_ref.png": ..., "template.png": ...} 중 있는 것.
        runner 는 gpu_lab.py, runner_lib 는 serving/lab/runner.py (lab_runner.py 로 옆에 둔다)."""
        runs = f"{self.lab_dir}/runs"
        self.mkdir(self.lab_dir)
        self.mkdir(runs)
        self.mkdir(f"{runs}/{run_id}")
        self.put_file(f"{self.lab_dir}/gpu_lab.py", runner)
        self.put_file(f"{self.lab_dir}/lab_runner.py", runner_lib)
        for name, data in images.items():
            self.put_file(f"{runs}/{run_id}/{name}", data)
        env = {k: self.gpu[k] for k in ("codeRoot", "pylib", "comfyInput", "node") if self.gpu.get(k)}
        self.put_file(f"{runs}/{run_id}/request.json", json.dumps({**request, "env": env}, ensure_ascii=False).encode())
        python = self.gpu.get("python", "python3")
        # 실행기는 커널과 떨어진 프로세스로 돈다. 실행 시간이 길어도 커널 호출은 바로 돌아온다.
        command = f"cd ~/{shlex.quote(self.lab_dir)} && {python} gpu_lab.py runs/{run_id} > runs/{run_id}/log.txt 2>&1"
        self.execute(
            "import subprocess\n"
            f"p = subprocess.Popen(['bash', '-lc', {json.dumps(command)}], start_new_session=True,"
            " stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)\n"
            "print(p.pid)\n")

    def run_file(self, run_id: str, name: str) -> bytes | None:
        return self.get_file(f"{self.lab_dir}/runs/{run_id}/{name}")
