"""파일 저장소. 서버가 여러 대면 모든 서버가 같은 곳을 봐야 한다.

  local  config.STORAGE_DIR 아래 (한 서버, 또는 모든 서버가 마운트한 공유 NAS 경로)
  s3     S3 호환 오브젝트 스토리지 (AWS S3, MinIO, 클라우드 사업자 Object Storage). 서버 쪽 암호화(SSE) 사용.

키 형식: attachments/<무작위이름>.<확장자>, uploads/<토큰>.json, forms/<양식키>.xlsx — 사용자 입력은 키에 들어가지 않는다.
"""

import re
import threading
from pathlib import Path

import config

KEY_RE = re.compile(r"^(attachments|uploads|forms)/[A-Za-z0-9._-]+$")


def _check(key: str) -> str:
    if not KEY_RE.match(key) or ".." in key:
        raise ValueError(f"잘못된 저장소 키: {key!r}")
    return key


class LocalStorage:
    name = "local"

    def __init__(self, root: Path):
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        return self.root / _check(key)

    def put(self, key: str, data: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".part")
        tmp.write_bytes(data)
        tmp.replace(path)                     # 다 쓴 뒤 이름을 바꿔 반쯤 쓴 파일이 보이지 않게

    def get(self, key: str) -> bytes | None:
        path = self._path(key)
        return path.read_bytes() if path.exists() else None

    def exists(self, key: str) -> bool:
        return self._path(key).exists()

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def keys(self, prefix: str) -> list[str]:
        folder = self.root / prefix.rstrip("/")
        if not folder.exists():
            return []
        return sorted(f"{prefix.rstrip('/')}/{p.name}" for p in folder.iterdir()
                      if p.is_file() and not p.name.endswith(".part"))

    def modified(self, key: str) -> float:
        return self._path(key).stat().st_mtime

    def check(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        probe = self.root / ".health"
        probe.write_text("ok")
        probe.unlink()


class S3Storage:
    name = "s3"

    def __init__(self, bucket: str, prefix: str, endpoint: str | None, region: str):
        import boto3                        # s3를 쓸 때만 필요
        if not bucket:
            raise RuntimeError("MM_S3_BUCKET이 설정되지 않았습니다.")
        self.bucket, self.prefix = bucket, prefix
        # 인증 정보는 코드에 두지 않는다: 서버 역할(IAM Role) 또는 AWS_ACCESS_KEY_ID 등 표준 환경변수
        self.client = boto3.client("s3", endpoint_url=endpoint, region_name=region)

    def _k(self, key: str) -> str:
        return self.prefix + _check(key)

    def put(self, key: str, data: bytes) -> None:
        self.client.put_object(Bucket=self.bucket, Key=self._k(key), Body=data, ServerSideEncryption="AES256")

    def get(self, key: str) -> bytes | None:
        try:
            return self.client.get_object(Bucket=self.bucket, Key=self._k(key))["Body"].read()
        except self.client.exceptions.NoSuchKey:
            return None

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=self._k(key))
            return True
        except Exception:
            return False

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=self._k(key))

    def keys(self, prefix: str) -> list[str]:
        out, token = [], None
        while True:
            kw = {"Bucket": self.bucket, "Prefix": self.prefix + prefix}
            if token:
                kw["ContinuationToken"] = token
            res = self.client.list_objects_v2(**kw)
            out += [o["Key"][len(self.prefix):] for o in res.get("Contents", [])]
            if not res.get("IsTruncated"):
                return sorted(out)
            token = res["NextContinuationToken"]

    def modified(self, key: str) -> float:
        return self.client.head_object(Bucket=self.bucket, Key=self._k(key))["LastModified"].timestamp()

    def check(self) -> None:
        self.client.head_bucket(Bucket=self.bucket)


_instance = None
_instance_key = None
_lock = threading.Lock()


def get():
    """현재 설정에 맞는 저장소 (설정이 바뀌면 새로 만든다 — 테스트용)."""
    global _instance, _instance_key
    key = (config.STORAGE, str(config.STORAGE_DIR), config.S3_BUCKET, config.S3_PREFIX, config.S3_ENDPOINT)
    with _lock:
        if _instance is None or _instance_key != key:
            _instance = (S3Storage(config.S3_BUCKET, config.S3_PREFIX, config.S3_ENDPOINT, config.S3_REGION)
                         if config.STORAGE == "s3" else LocalStorage(config.STORAGE_DIR))
            _instance_key = key
        return _instance
