"""파일 저장소 — 서버 여러 대가 같은 파일을 보도록 저장 위치를 한 곳으로 모은다.

  SALES_STORAGE=local (기본)  SALES_STORAGE_DIR=data/ 또는 NAS 공유 경로(\\\\nas\\sales, /mnt/sales)
  SALES_STORAGE=s3            S3 호환 오브젝트 스토리지(AWS S3, MinIO, 사내 오브젝트 스토리지)
      SALES_S3_BUCKET, SALES_S3_PREFIX, SALES_S3_ENDPOINT(MinIO 등), SALES_S3_REGION,
      AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY (또는 인스턴스 역할), SALES_S3_SSE=AES256|aws:kms
      S3 가 안 되면 SALES_STORAGE_SPOOL_DIR(기본 data/spool)에 임시 보관 → 배치 storage.flush 가 다시 올림

키는 '영역/경로' 형식이다.  documents/2026/09/<난수>.png, uploads/<토큰>.csv
업무 코드는 put/get/delete/exists 만 쓰므로 저장 방식을 바꿔도 수정할 필요가 없다.
"""
from __future__ import annotations

import os
import threading
from pathlib import Path, PurePosixPath
from typing import Optional

BASE_DIR = Path(__file__).resolve().parents[1]


def _clean_key(key: str) -> str:
    """'..' · 절대경로 · 역슬래시로 저장소 밖을 가리키지 못하게 한다."""
    parts = [p for p in PurePosixPath(key.replace("\\", "/")).parts if p not in ("", ".", "/")]
    if not parts or any(p == ".." for p in parts):
        raise ValueError(f"잘못된 파일 키입니다: {key!r}")
    return "/".join(parts)


class LocalStorage:
    name = "local"

    def __init__(self, root: str | os.PathLike):
        self.root = Path(root).resolve()

    def _path(self, key: str) -> Path:
        path = (self.root / _clean_key(key)).resolve()
        if self.root not in path.parents:
            raise PermissionError("저장소 밖의 경로입니다.")
        return path

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".part")
        tmp.write_bytes(data)
        tmp.replace(path)                      # 다른 서버가 반쯤 쓴 파일을 읽지 않도록 원자적으로 교체
        return _clean_key(key)

    def get(self, key: str) -> bytes:
        path = self._path(key)
        if not path.exists():
            raise FileNotFoundError(key)
        return path.read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).exists()

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def describe(self) -> str:
        return str(self.root)


class S3Storage:
    name = "s3"

    def __init__(self, bucket: str, prefix: str = "", endpoint: str | None = None,
                 region: str | None = None, sse: str | None = None):
        import boto3
        if not bucket:
            raise ValueError("SALES_S3_BUCKET 이 설정되지 않았습니다.")
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self.sse = sse
        self.client = boto3.client("s3", endpoint_url=endpoint or None, region_name=region or None)

    def _key(self, key: str) -> str:
        key = _clean_key(key)
        return f"{self.prefix}/{key}" if self.prefix else key

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        extra = {"ServerSideEncryption": self.sse} if self.sse else {}
        self.client.put_object(Bucket=self.bucket, Key=self._key(key), Body=data,
                               ContentType=content_type, **extra)
        return _clean_key(key)

    def get(self, key: str) -> bytes:
        from botocore.exceptions import ClientError
        try:
            return self.client.get_object(Bucket=self.bucket, Key=self._key(key))["Body"].read()
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
                raise FileNotFoundError(key) from exc
            raise

    def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError
        try:
            self.client.head_object(Bucket=self.bucket, Key=self._key(key))
            return True
        except ClientError:
            return False

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=self._key(key))

    def describe(self) -> str:
        return f"s3://{self.bucket}/{self.prefix}"


class SpoolingStorage:
    """S3 가 잠깐 안 될 때: 올리지 못한 파일을 이 서버의 임시 폴더(SALES_STORAGE_SPOOL_DIR)에 두고 업무는 계속한다.
    배치 storage.flush(5분)가 다시 올리고 임시 파일을 지운다. 수동: python manage.py storage-flush.
    임시 보관 중인 파일도 읽을 수 있다(이 서버에서). 서버가 여러 대면 다른 서버는 올라간 뒤부터 읽는다."""

    def __init__(self, inner, spool_dir: str | os.PathLike):
        self.inner = inner
        self.spool = LocalStorage(spool_dir)
        self.name = f"{inner.name}+spool"

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        try:
            return self.inner.put(key, data, content_type)
        except (ValueError, PermissionError):
            raise
        except Exception as exc:                     # noqa: BLE001 - 저장소 장애: 임시 보관하고 업무는 계속
            import logging
            logging.getLogger("sales.storage").warning("저장소 장애 — 임시 보관: %s (%s)", key, exc)
            return self.spool.put(key, data, content_type)

    def get(self, key: str) -> bytes:
        if self.spool.exists(key):
            return self.spool.get(key)
        return self.inner.get(key)

    def exists(self, key: str) -> bool:
        return self.spool.exists(key) or self.inner.exists(key)

    def delete(self, key: str) -> None:
        self.spool.delete(key)
        try:
            self.inner.delete(key)
        except Exception:                            # noqa: BLE001 - 임시 보관만 있던 파일
            pass

    def spooled(self) -> list[str]:
        root = self.spool.root
        if not root.exists():
            return []
        return [p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file() and not p.name.endswith(".part")]

    def spooled_count(self) -> int:
        return len(self.spooled())

    def flush(self) -> dict:
        """임시 보관 파일을 원래 저장소로 올린다. 하나라도 실패하면 남겨 두고 다음에 다시."""
        import mimetypes
        done, failed = 0, 0
        for key in self.spooled():
            try:
                ctype = mimetypes.guess_type(key)[0] or "application/octet-stream"
                self.inner.put(key, self.spool.get(key), ctype)
                self.spool.delete(key)
                done += 1
            except Exception:                        # noqa: BLE001 - 아직 장애 중
                failed += 1
        return {"uploaded": done, "left": failed}

    def describe(self) -> str:
        n = self.spooled_count()
        return self.inner.describe() + (f" (임시 보관 {n}개)" if n else "")


_storage = None
_lock = threading.Lock()


def get_storage():
    """설정에 따른 저장소 (프로세스당 하나)."""
    global _storage
    with _lock:
        if _storage is None:
            kind = os.environ.get("SALES_STORAGE", "local")
            if kind == "s3":
                s3 = S3Storage(os.environ.get("SALES_S3_BUCKET", ""), os.environ.get("SALES_S3_PREFIX", "sales"),
                               os.environ.get("SALES_S3_ENDPOINT"), os.environ.get("SALES_S3_REGION"),
                               os.environ.get("SALES_S3_SSE"))
                _storage = SpoolingStorage(s3, os.environ.get("SALES_STORAGE_SPOOL_DIR", BASE_DIR / "data" / "spool"))
            elif kind == "local":
                _storage = LocalStorage(os.environ.get("SALES_STORAGE_DIR", BASE_DIR / "data"))
            else:
                raise ValueError(f"알 수 없는 저장소 종류입니다: {kind}")
        return _storage


def reset_storage() -> Optional[object]:
    """설정을 바꾼 뒤(테스트 등) 저장소를 다시 만들게 한다."""
    global _storage
    with _lock:
        old, _storage = _storage, None
        return old
