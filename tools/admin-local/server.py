"""카드 생성 모니터 (로컬 전용).

    .venv/Scripts/python tools/admin-local/server.py        # http://127.0.0.1:8765

이 PC 의 127.0.0.1 에만 붙는다. 운영 서비스에는 아무것도 배포하지 않는다.
운영 DB 는 EC2 에서 읽기 전용으로만 읽고, 이미지는 EC2 워커 컨테이너를 거쳐 받는다(remote.py).
회원 원본 사진을 열면 logs/media-access.log 에 한 줄씩 남긴다.
"""
from __future__ import annotations

import argparse
import base64
import getpass
import json
import mimetypes
import pathlib
import secrets
import sys
import threading
import time
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import lab  # noqa: E402
import queries  # noqa: E402
from jupyter import Jupyter, JupyterError  # noqa: E402
from remote import Remote, RemoteError  # noqa: E402

STATIC = HERE / "static"
LOGS = HERE / "logs"
MEDIA_TTL = 300  # 초. 서비스의 서명 주소와 같은 5분


class MediaCache:
    """받아 온 이미지를 짧게 들고 있다가 /media/<token> 으로 내준다. 디스크에는 쓰지 않는다."""

    def __init__(self):
        self._items: dict[str, tuple[float, str, bytes]] = {}
        self._lock = threading.Lock()

    def put(self, content_type: str, data: bytes) -> str:
        token = secrets.token_urlsafe(18)
        with self._lock:
            self._sweep()
            self._items[token] = (time.time() + MEDIA_TTL, content_type, data)
        return token

    def get(self, token: str):
        with self._lock:
            self._sweep()
            hit = self._items.get(token)
        return None if hit is None else (hit[1], hit[2])

    def _sweep(self):
        now = time.time()
        for token in [t for t, (exp, _, _) in self._items.items() if exp < now]:
            del self._items[token]


def load_config() -> dict:
    path = HERE / "local.json"
    if not path.exists():
        sys.exit(f"설정 파일이 없습니다: {path}\nlocal.example.json 을 복사해 값을 채우세요.")
    return json.loads(path.read_text(encoding="utf-8"))


def log_access(entry: dict):
    LOGS.mkdir(exist_ok=True)
    with (LOGS / "media-access.log").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


TEAM_ASSET_ROOTS = ("motion-card-lab", "assets", "fonts", "brand")
CONTENT_TYPES = {
    ".html": "text/html", ".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css",
    ".json": "application/json", ".svg": "image/svg+xml", ".webp": "image/webp", ".png": "image/png",
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".woff2": "font/woff2", ".woff": "font/woff",
}


MAX_UPLOAD = 15 * 1024 * 1024


def source_photo(remote: Remote, item_id: str, purpose: str) -> tuple[dict, bytes]:
    """모니터의 생성 항목에서 회원 원본을 받아 온다. 받을 때마다 열람 기록을 남긴다."""
    detail = remote.query_json(queries.detail(item_id))
    if detail is None:
        raise queries.Invalid("생성 항목을 찾을 수 없습니다")
    source = next((m for m in detail.get("media") or [] if m["role"] == "SOURCE"), None)
    if source is None:
        raise queries.Invalid("이 항목에는 원본 사진이 없습니다")
    got = remote.fetch_objects([{"bucket": source["bucket"], "key": source["key"]}]).get(source["key"])
    if got is None:
        raise RemoteError("원본 사진을 받지 못했습니다")
    item = detail.get("item") or {}
    log_access({
        "at": datetime.now(timezone.utc).isoformat(), "viewer": getpass.getuser(), "itemId": item.get("itemId"),
        "subjectUserId": (item.get("subject") or {}).get("userId"), "mediaFileId": source["mediaFileId"],
        "variant": source["variant"], "purpose": purpose,
    })
    return detail, got[1]


def make_handler(remote: Remote, cache: MediaCache, team_public: pathlib.Path | None, jupyter: Jupyter):
    class Handler(BaseHTTPRequestHandler):
        server_version = "motion-admin-local"

        def log_message(self, fmt, *args):  # 조용히. 오류만 stderr 로
            pass

        def _send(self, status: int, body: bytes, content_type: str, extra: dict | None = None):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, payload):
            self._send(status, json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8"),
                       "application/json; charset=utf-8")

        def _guard(self) -> bool:
            # 다른 사이트가 브라우저를 시켜 이 도구를 부르지 못하게 한다(DNS 재바인딩·CSRF).
            host = (self.headers.get("Host") or "").split(":")[0]
            origin = self.headers.get("Origin")
            if host not in ("127.0.0.1", "localhost") or (origin and urlparse(origin).hostname not in ("127.0.0.1", "localhost")):
                self._json(403, {"error": "로컬에서만 쓸 수 있습니다"})
                return False
            return True

        def _api(self, fn):
            try:
                self._json(200, {"data": fn()})
            except (queries.Invalid, ValueError) as exc:
                self._json(400, {"error": str(exc)})
            except RemoteError as exc:
                self._json(502, {"error": f"EC2 조회 실패: {exc}"})
            except JupyterError as exc:
                self._json(502, {"error": f"GPU 서버 실패: {exc}"})
            except Exception as exc:  # 도구가 죽지 않게 한다
                self._json(500, {"error": f"{type(exc).__name__}: {exc}"})

        def do_GET(self):
            if not self._guard():
                return
            url = urlparse(self.path)
            params = {k: v[0] for k, v in parse_qs(url.query).items()}
            path = url.path
            if path == "/api/summary":
                return self._api(lambda: remote.query_json(queries.summary(params.get("hours"))))
            if path == "/api/items":
                return self._api(lambda: self._items(params))
            if path.startswith("/api/items/"):
                return self._api(lambda: self._detail(path.rsplit("/", 1)[1]))
            if path == "/api/users":
                return self._api(lambda: remote.query_json(queries.users(params.get("q", ""))))
            if path == "/api/lab/status":
                return self._api(lambda: {"ready": jupyter.ready, "node": jupyter.gpu.get("node")})
            if path == "/api/lab/presets":
                return self._api(lab.presets)
            if path == "/api/lab/runs":
                return self._api(lab.history)
            if path.startswith("/api/lab/runs/"):
                return self._api(lambda: self._lab_run(path.rsplit("/", 1)[1]))
            if path.startswith("/lab-files/"):
                parts = path.split("/")
                target = lab.file_path(parts[2], parts[3]) if len(parts) == 4 else None
                if target is None:
                    return self._json(404, {"error": "없는 파일"})
                return self._send(200, target.read_bytes(), CONTENT_TYPES.get(target.suffix, "application/octet-stream"))
            if path.startswith("/media/"):
                hit = cache.get(path.rsplit("/", 1)[1])
                if hit is None:
                    return self._json(404, {"error": "만료된 이미지 주소입니다(5분)"})
                return self._send(200, hit[1], hit[0])
            return self._static(path)

        def do_POST(self):
            if not self._guard():
                return
            path = urlparse(self.path).path
            if path.startswith("/api/items/") and path.endswith("/media"):
                return self._api(lambda: self._media(path.split("/")[3]))
            if path == "/api/lab/runs":
                return self._api(self._lab_start)
            self._json(404, {"error": "없는 경로"})

        def _body(self) -> dict:
            size = int(self.headers.get("Content-Length") or 0)
            if size > MAX_UPLOAD * 2:
                raise ValueError("요청이 너무 큽니다")
            return json.loads(self.rfile.read(size) or b"{}")

        def _lab_start(self):
            if not jupyter.ready:
                raise ValueError("local.json 에 jupyter(url, token)·gpu(codeRoot, node) 설정이 필요합니다. README 참고")
            body = self._body()
            code = queries._code(body.get("stylePreset"), "stylePreset")
            preset = next((p for p in lab.presets() if p["stylePreset"] == code), None)
            if preset is None:
                raise ValueError("알 수 없는 프리셋입니다")
            gender = body.get("gender") or "auto"
            if gender not in ("auto", "male", "female"):
                raise ValueError("성별 값이 올바르지 않습니다")
            capture = int(body.get("captureSteps") or 0)
            if not 0 <= capture <= 8:
                raise ValueError("중간 스텝 수는 0~8 입니다")
            source = None
            if body.get("fromItem"):
                detail, image = source_photo(remote, str(body["fromItem"]), "LAB_INPUT_LOCAL")
                source = {"itemId": detail["item"]["itemId"], "stylePreset": detail["item"]["stylePreset"]}
            else:
                data_url = body.get("image") or ""
                if "," not in data_url:
                    raise ValueError("사진을 골라 주세요")
                image = base64.b64decode(data_url.split(",", 1)[1])
            if len(image) > MAX_UPLOAD:
                raise ValueError("사진이 15MB 를 넘습니다")
            request = {"stylePreset": code, "gender": gender, "params": lab.clean_params(preset["engine"], body.get("params")),
                       "captureSteps": capture if preset["captureSteps"] else 0, "label": (body.get("label") or "")[:80],
                       "source": source}
            run_id = lab.new_run_id()
            lab.save_request(run_id, request, image)
            gpu_request = {k: request[k] for k in ("stylePreset", "gender", "params", "captureSteps")}
            jupyter.start_run(run_id, gpu_request, image, lab.RUNNER.read_bytes())
            return {"runId": run_id}

        def _lab_run(self, run_id):
            result = lab.read_json(run_id, "result.json")
            status = lab.read_json(run_id, "status.json")
            if result is None and not (status or {}).get("error"):
                raw = jupyter.run_file(run_id, "status.json")
                status = json.loads(raw) if raw else {"stage": "queued"}
                if status.get("done") and not status.get("error"):
                    raw_result = jupyter.run_file(run_id, "result.json")
                    if raw_result:
                        result = json.loads(raw_result)
                        names = ["analysis.png", "result.png", "cutout.png"] + [s["file"] for s in result.get("steps") or []]
                        if result.get("reference"):
                            names.append(result["reference"])
                        for name in names:
                            data = jupyter.run_file(run_id, name)
                            if data:
                                lab.write_file(run_id, name, data)
                        lab.write_file(run_id, "result.json", raw_result)
                if status.get("error") or result is not None:
                    lab.write_file(run_id, "status.json", json.dumps(status, ensure_ascii=False).encode())
            request = lab.read_json(run_id, "request.json")
            if request is None:
                raise ValueError("없는 실행입니다")
            return {"runId": run_id, "request": request, "status": status, "result": result, "files": f"/lab-files/{run_id}/"}

        def _items(self, params):
            sql, limit = queries.items(params)
            rows = remote.query_json(sql) or []
            next_cursor = rows[limit - 1]["itemId"] if len(rows) > limit else None
            return {"items": rows[:limit], "nextCursor": next_cursor}

        def _detail(self, item_id):
            detail = remote.query_json(queries.detail(item_id))
            if detail is None:
                raise queries.Invalid("생성 항목을 찾을 수 없습니다")
            for media in detail.get("media") or []:
                media.pop("bucket", None)
                media.pop("key", None)
            return detail

        def _media(self, item_id):
            detail = remote.query_json(queries.detail(item_id))
            if detail is None:
                raise queries.Invalid("생성 항목을 찾을 수 없습니다")
            media = detail.get("media") or []
            fetched = remote.fetch_objects([{"bucket": m["bucket"], "key": m["key"]} for m in media])
            out = []
            for m in media:
                got = fetched.get(m["key"])
                if got is None:
                    out.append({"mediaFileId": m["mediaFileId"], "role": m["role"], "variant": m["variant"], "url": None})
                    continue
                token = cache.put(m.get("contentType") or got[0], got[1])
                if m["bucket"] == "ORIGINAL":
                    item = detail.get("item") or {}
                    log_access({
                        "at": datetime.now(timezone.utc).isoformat(),
                        "viewer": getpass.getuser(),
                        "itemId": item.get("itemId"),
                        "subjectUserId": (item.get("subject") or {}).get("userId"),
                        "mediaFileId": m["mediaFileId"],
                        "variant": m["variant"],
                        "purpose": "GENERATION_MONITOR_LOCAL",
                    })
                out.append({"mediaFileId": m["mediaFileId"], "role": m["role"], "variant": m["variant"], "url": f"/media/{token}"})
            return {"media": out, "expiresAt": datetime.fromtimestamp(time.time() + MEDIA_TTL, timezone.utc).isoformat()}

        def _static(self, path):
            name = "index.html" if path in ("", "/") else path.lstrip("/")
            root = STATIC
            # 완성 카드 미리보기가 쓰는 배경·테두리·글꼴은 팀 프런트 저장소의 public 에서 그대로 읽는다.
            if team_public and name.split("/", 1)[0] in TEAM_ASSET_ROOTS:
                root = team_public
            target = (root / name).resolve()
            if root not in target.parents or not target.is_file():
                return self._json(404, {"error": "없는 경로"})
            # Windows 레지스트리는 .js 를 text/plain 으로 알려 줄 때가 있어 모듈 스크립트가 막힌다. 직접 정한다.
            content_type = CONTENT_TYPES.get(target.suffix.lower()) or mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            if content_type.startswith("text/") or content_type.endswith("javascript"):
                content_type += "; charset=utf-8"
            self._send(200, target.read_bytes(), content_type)

    return Handler


def main():
    parser = argparse.ArgumentParser(description="카드 생성 모니터 (로컬 전용)")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    config = load_config()
    remote = Remote(config)
    team_public = None
    if config.get("teamFrontend"):
        candidate = (pathlib.Path(config["teamFrontend"]) / "public").resolve()
        team_public = candidate if candidate.is_dir() else None
    if not (STATIC / "cardface" / "cardface.js").exists():
        print("완성 카드 미리보기 모듈이 없습니다. node tools/admin-local/cardface/build.mjs 로 만들 수 있습니다.")
    jupyter = Jupyter(config)
    if not jupyter.ready:
        print("실험실을 쓰려면 local.json 에 jupyter·gpu 설정을 넣으세요(README). 모니터는 그대로 쓸 수 있습니다.")
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(remote, MediaCache(), team_public, jupyter))
    print(f"카드 생성 모니터: http://127.0.0.1:{args.port}  (Ctrl+C 로 종료)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
