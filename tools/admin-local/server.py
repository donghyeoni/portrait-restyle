"""카드 생성 모니터 (로컬 전용).

    .venv/Scripts/python tools/admin-local/server.py        # http://127.0.0.1:8765

이 PC 의 127.0.0.1 에만 붙는다. 운영 서비스에는 아무것도 배포하지 않는다.
운영 DB 는 EC2 에서 읽기 전용으로만 읽고, 이미지는 EC2 워커 컨테이너를 거쳐 받는다(remote.py).
회원 원본 사진을 열면 logs/media-access.log 에 한 줄씩 남긴다.
"""
from __future__ import annotations

import argparse
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
import queries  # noqa: E402
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


def make_handler(remote: Remote, cache: MediaCache, team_public: pathlib.Path | None):
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
            except queries.Invalid as exc:
                self._json(400, {"error": str(exc)})
            except RemoteError as exc:
                self._json(502, {"error": f"EC2 조회 실패: {exc}"})
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
            self._json(404, {"error": "없는 경로"})

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
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(remote, MediaCache(), team_public))
    print(f"카드 생성 모니터: http://127.0.0.1:{args.port}  (Ctrl+C 로 종료)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
