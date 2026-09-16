"""S3 입출력.

EC2 워커: boto3 (IAM 역할). 결과 업로드는 **native SHA-256 체크섬**을 붙인다 — 백엔드가 HeadObject 의 ChecksumSHA256
으로 완료를 검증한다 (전달서 7절). 프리사인 URL 은 소비·claim 뒤에 만들고 큐·DB·로그에 남기지 않는다.
GPU 서비스: AWS 자격 증명 없이 프리사인 URL 로만 GET/PUT (requests). 프리사인 PUT 에 체크섬을 얹는 방식은 POC 전이라
기본 경로는 GPU 결과 바이트를 EC2 가 받아 SDK 로 올리는 것(relay)이다.
"""
from __future__ import annotations

import os
import time

import requests

DEFAULT_TTL = int(os.environ.get("PRESIGN_TTL_SEC", "1800"))     # 30분
_UA = {"User-Agent": "portrait-restyle-worker/1"}


# ------------------------------------------------------------- 프리사인 URL 만 쓰는 쪽 ----
def download(url: str, timeout: float = 120.0, retries: int = 3) -> bytes:
    last = None
    for i in range(retries):
        try:
            r = requests.get(url, timeout=timeout, headers=_UA)
            if r.status_code == 200:
                return r.content
            last = RuntimeError(f"GET {r.status_code}")
            if 400 <= r.status_code < 500:
                break
        except requests.RequestException as e:
            last = e
        time.sleep(2 * (i + 1))
    raise RuntimeError(f"다운로드 실패: {last}")


def upload(url: str, data: bytes, content_type: str, timeout: float = 120.0, retries: int = 3) -> None:
    last = None
    for i in range(retries):
        try:
            r = requests.put(url, data=data, headers={"Content-Type": content_type, **_UA}, timeout=timeout)
            if r.status_code in (200, 201, 204):
                return
            last = RuntimeError(f"PUT {r.status_code} {r.text[:120]}")
            if 400 <= r.status_code < 500 and r.status_code != 429:
                break
        except requests.RequestException as e:
            last = e
        time.sleep(2 * (i + 1))
    raise RuntimeError(f"업로드 실패: {last}")


# ------------------------------------------------------------------ boto3 (EC2) ----
class S3:
    """bucketType(ORIGINAL / AI_PROCESSED) -> 실제 버킷 이름은 환경변수 ORIGINAL_BUCKET / AI_PROCESSED_BUCKET."""

    def __init__(self, buckets: dict[str, str] | None = None, region: str | None = None):
        import boto3
        from botocore.config import Config
        self.buckets = buckets or {"ORIGINAL": os.environ["ORIGINAL_BUCKET"],
                                   "AI_PROCESSED": os.environ["AI_PROCESSED_BUCKET"]}
        # SDK 자동 재시도로 실행 횟수가 늘지 않게 표준 재시도 1회로 제한 (전달서 4절)
        self.client = boto3.client("s3", region_name=region or os.environ.get("AWS_REGION"),
                                   config=Config(retries={"max_attempts": 2, "mode": "standard"}))

    def bucket(self, bucket_type: str) -> str:
        try:
            return self.buckets[bucket_type]
        except KeyError:
            raise KeyError(f"알 수 없는 bucketType {bucket_type!r}") from None

    def presign_get(self, bucket_type: str, key: str, ttl: int = DEFAULT_TTL) -> str:
        return self.client.generate_presigned_url("get_object", Params={"Bucket": self.bucket(bucket_type), "Key": key}, ExpiresIn=ttl)

    def presign_put(self, bucket_type: str, key: str, content_type: str, ttl: int = DEFAULT_TTL) -> str:
        return self.client.generate_presigned_url(
            "put_object", Params={"Bucket": self.bucket(bucket_type), "Key": key, "ContentType": content_type}, ExpiresIn=ttl)

    def head(self, bucket_type: str, key: str) -> dict | None:
        try:
            return self.client.head_object(Bucket=self.bucket(bucket_type), Key=key, ChecksumMode="ENABLED")
        except self.client.exceptions.ClientError as e:
            if e.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return None
            raise

    def get(self, bucket_type: str, key: str) -> bytes | None:
        try:
            return self.client.get_object(Bucket=self.bucket(bucket_type), Key=key)["Body"].read()
        except self.client.exceptions.ClientError as e:
            if e.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return None
            raise

    def put(self, bucket_type: str, key: str, data: bytes, content_type: str, checksum_b64: str | None = None) -> None:
        """native SHA-256 체크섬을 함께 올린다 (백엔드 HeadObject 검증용)."""
        kw = {"Bucket": self.bucket(bucket_type), "Key": key, "Body": data, "ContentType": content_type}
        if checksum_b64:
            kw["ChecksumSHA256"] = checksum_b64
        else:
            kw["ChecksumAlgorithm"] = "SHA256"
        self.client.put_object(**kw)
