"""파일 저장소. 서버가 여러 대면 모든 서버가 같은 곳을 봐야 한다.

  local  config.STORAGE_DIR 아래 (한 서버, 또는 모든 서버가 마운트한 공유 NAS 경로)
  s3     S3 호환 오브젝트 스토리지 (AWS S3, MinIO, 클라우드 사업자 Object Storage). 서버 쪽 암호화(SSE) 사용.
         인터넷·클라우드 장애로 S3에 닿지 않으면 이 서버의 임시 폴더(MM_STORAGE_SPOOL_DIR)에 두고 업무를 계속한다.
         배치 storage_flush가 연결이 돌아오면 올린다 (SpoolingStorage).

키 형식: attachments/<무작위이름>.<확장자>, uploads/<토큰>.json, forms/<양식키>.xlsx — 사용자 입력은 키에 들어가지 않는다.
"""

import logging
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

    def check(self) -> str:
        self.root.mkdir(parents=True, exist_ok=True)
        probe = self.root / ".health"
        probe.write_text("ok")
        probe.unlink()
        return "ok"


class S3Storage:
    name = "s3"

    def __init__(self, bucket: str, prefix: str, endpoint: str | None, region: str):
        import boto3                        # s3를 쓸 때만 필요
        if not bucket:
            raise RuntimeError("MM_S3_BUCKET이 설정되지 않았습니다.")
        self.bucket, self.prefix = bucket, prefix
        from botocore.config import Config
        # 인증 정보는 코드에 두지 않는다: 서버 역할(IAM Role) 또는 AWS_ACCESS_KEY_ID 등 표준 환경변수
        # 연결이 안 될 때 화면이 오래 멈추지 않게 짧게 기다린다(기본값은 60초 × 재시도) → 임시 폴더로 넘긴다
        self.client = boto3.client("s3", endpoint_url=endpoint, region_name=region,
                                   config=Config(connect_timeout=5, read_timeout=30, retries={"max_attempts": 2}))

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


log = logging.getLogger(__name__)
SPOOL_PREFIXES = ("attachments/", "uploads/", "forms/")


class SpoolingStorage:
    """S3 + 이 서버의 임시 폴더. S3가 안 되면 쓰기는 임시 폴더로, 읽기는 임시 폴더를 먼저 본다.

    - 임시 폴더에 있는 파일은 이 서버에서만 보인다. 서버가 여러 대면 MM_STORAGE_SPOOL_DIR를 공유 폴더로 두거나
      로드밸런서 고정 세션을 쓴다(그렇지 않으면 다른 서버는 올라갈 때까지 그 파일을 못 읽는다).
    - 지우기가 S3에 닿지 않으면 표시(.deletes/)를 남겨 두었다가 flush()에서 지운다.
    """

    name = "s3"

    def __init__(self, remote: S3Storage, spool_dir: Path):
        self.remote = remote
        self.spool = LocalStorage(spool_dir)
        self.tombs = Path(spool_dir) / ".deletes"

    def _tomb(self, key: str) -> Path:
        return self.tombs / _check(key).replace("/", "__")

    def put(self, key: str, data: bytes) -> None:
        _check(key)
        try:
            self.remote.put(key, data)
        except Exception:
            log.warning("S3에 저장하지 못해 임시 폴더에 둡니다: %s", key, exc_info=True)
            self.spool.put(key, data)
            return
        self._tomb(key).unlink(missing_ok=True)
        self.spool.delete(key)

    def get(self, key: str) -> bytes | None:
        data = self.spool.get(key)
        if data is not None:
            return data
        if self._tomb(key).exists():
            return None
        return self.remote.get(key)

    def exists(self, key: str) -> bool:
        return self.spool.exists(key) or (not self._tomb(key).exists() and self.remote.exists(key))

    def delete(self, key: str) -> None:
        self.spool.delete(key)
        try:
            self.remote.delete(key)
        except Exception:
            log.warning("S3에서 지우지 못해 나중에 지웁니다: %s", key, exc_info=True)
            self.tombs.mkdir(parents=True, exist_ok=True)
            self._tomb(key).write_text("")

    def keys(self, prefix: str) -> list[str]:
        local = set(self.spool.keys(prefix))
        try:
            remote = set(self.remote.keys(prefix))
        except Exception:
            remote = set()
        gone = {t.name.replace("__", "/") for t in self.tombs.iterdir()} if self.tombs.exists() else set()
        return sorted(local | (remote - gone))

    def modified(self, key: str) -> float:
        return self.spool.modified(key) if self.spool.exists(key) else self.remote.modified(key)

    def pending(self) -> int:
        n = sum(len(self.spool.keys(p)) for p in SPOOL_PREFIXES)
        return n + (len(list(self.tombs.iterdir())) if self.tombs.exists() else 0)

    def check(self) -> str:
        """S3가 되면 'ok', 안 되지만 임시 폴더에 쓸 수 있으면 'degraded'(업무는 계속됨), 둘 다 안 되면 예외."""
        try:
            self.remote.check()
            return "ok"
        except Exception:
            self.spool.check()
            return "degraded"

    def flush(self) -> str:
        """임시 폴더의 파일을 S3로 올리고, 미뤄 둔 지우기를 처리한다. 실패한 것은 다음 주기에 다시."""
        sent = deleted = failed = 0
        for prefix in SPOOL_PREFIXES:
            for key in self.spool.keys(prefix):
                try:
                    self.remote.put(key, self.spool.get(key) or b"")
                    self.spool.delete(key)
                    sent += 1
                except Exception:
                    failed += 1
        if self.tombs.exists():
            for t in list(self.tombs.iterdir()):
                try:
                    self.remote.delete(t.name.replace("__", "/"))
                    t.unlink()
                    deleted += 1
                except Exception:
                    failed += 1
        if failed:
            raise RuntimeError(f"S3 연결 안 됨 — 올림 {sent} · 지움 {deleted} · 남음 {failed}")
        return f"올림 {sent} · 지움 {deleted}"


_instance = None
_instance_key = None
_lock = threading.Lock()


def get():
    """현재 설정에 맞는 저장소 (설정이 바뀌면 새로 만든다 — 테스트용)."""
    global _instance, _instance_key
    key = (config.STORAGE, str(config.STORAGE_DIR), config.S3_BUCKET, config.S3_PREFIX, config.S3_ENDPOINT,
           str(config.STORAGE_SPOOL_DIR))
    with _lock:
        if _instance is None or _instance_key != key:
            _instance = (SpoolingStorage(S3Storage(config.S3_BUCKET, config.S3_PREFIX, config.S3_ENDPOINT,
                                                   config.S3_REGION), config.STORAGE_SPOOL_DIR)
                         if config.STORAGE == "s3" else LocalStorage(config.STORAGE_DIR))
            _instance_key = key
        return _instance
