"""운영 EC2 에 SSH 로 붙어 DB 를 읽고 S3 이미지를 받아 온다.

비밀 값은 이 PC 로 가져오지 않는다.
- DB: EC2 의 Postgres 컨테이너 안에서 psql 을 돌린다. 모든 쿼리는 READ ONLY 트랜잭션 안에서 돌고 끝나면 되돌린다.
- S3: 이미 S3 를 쓰는 워커 컨테이너가 자기 환경의 자격으로 객체를 읽어 base64 로 넘긴다.
접속 정보(호스트·키 경로·컨테이너 이름)는 저장소에 올리지 않는 local.json 에 둔다.
"""
from __future__ import annotations

import base64
import json
import re
import subprocess
import threading

_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")

# 워커 컨테이너 안에서 돈다. stdin 으로 [{bucket, key}] 를 받아 {key: {ct, b64} | {error}} 를 낸다.
_FETCH = r"""
import sys, json, os, base64, boto3
req = json.load(sys.stdin)
s3 = boto3.client("s3")
out = {}
for o in req:
    bucket = os.environ["ORIGINAL_BUCKET" if o["bucket"] == "ORIGINAL" else "AI_PROCESSED_BUCKET"]
    try:
        r = s3.get_object(Bucket=bucket, Key=o["key"])
        out[o["key"]] = {"ct": r.get("ContentType"), "b64": base64.b64encode(r["Body"].read()).decode()}
    except Exception as e:
        out[o["key"]] = {"error": type(e).__name__}
json.dump(out, sys.stdout)
"""


class RemoteError(RuntimeError):
    pass


class Remote:
    def __init__(self, cfg: dict):
        ec2 = cfg["ec2"]
        self.target = f'{ec2["user"]}@{ec2["host"]}'
        self.key = ec2["key"]
        self.pg = cfg["postgresContainer"]
        self.s3 = cfg["s3Container"]
        for name in (self.pg, self.s3):
            if not _NAME.match(name):
                raise ValueError(f"컨테이너 이름이 올바르지 않습니다: {name}")
        self._pg_login: tuple[str, str] | None = None
        self._lock = threading.Lock()

    def _ssh(self, command: str, stdin: bytes = b"", timeout: int = 60) -> bytes:
        try:
            done = subprocess.run(
                ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-i", self.key, self.target, command],
                input=stdin, capture_output=True, timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise RemoteError("EC2 응답 시간 초과") from exc
        if done.returncode != 0:
            message = done.stderr.decode("utf-8", "replace").strip().splitlines()
            raise RemoteError(message[-1] if message else f"ssh 종료 코드 {done.returncode}")
        return done.stdout

    def _login(self) -> tuple[str, str]:
        # DB 사용자·이름은 비밀이 아니다. 한 번 읽어 두고 psql 인자로만 쓴다.
        with self._lock:
            if self._pg_login is None:
                out = self._ssh(f"sudo -n docker exec {self.pg} printenv POSTGRES_USER POSTGRES_DB").decode().split()
                if len(out) != 2 or not all(_NAME.match(v) for v in out):
                    raise RemoteError("Postgres 컨테이너에서 사용자·DB 이름을 읽지 못했습니다")
                self._pg_login = (out[0], out[1])
            return self._pg_login

    def query_json(self, json_sql: str):
        """json 을 돌려주는 SELECT 식 하나를 읽기 전용으로 실행한다."""
        user, db = self._login()
        script = (
            "BEGIN READ ONLY;\n"
            "SET LOCAL statement_timeout = '20s';\n"
            f"SELECT COALESCE(({json_sql})::text, 'null');\n"
            "ROLLBACK;\n"
        )
        out = self._ssh(
            f"sudo -n docker exec -i {self.pg} psql -U {user} -d {db} -qAt -v ON_ERROR_STOP=1",
            script.encode("utf-8"),
        )
        text = out.decode("utf-8").strip()
        return json.loads(text) if text else None

    def fetch_objects(self, objects: list[dict]) -> dict:
        """[{bucket: ORIGINAL|AI_PROCESSED, key}] → {key: (content_type, bytes) | None}"""
        if not objects:
            return {}
        code = base64.b64encode(_FETCH.encode()).decode()
        out = self._ssh(
            f"sudo -n docker exec -i {self.s3} python -c \"import base64;exec(base64.b64decode('{code}'))\"",
            json.dumps(objects).encode(), timeout=120,
        )
        result = {}
        for key, value in json.loads(out.decode()).items():
            result[key] = (value.get("ct") or "application/octet-stream", base64.b64decode(value["b64"])) if "b64" in value else None
        return result
