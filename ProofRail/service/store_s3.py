from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from ProofRail.service.storage import canonical_json_bytes, sha256_hex


@dataclass(frozen=True)
class S3StoreConfig:
    bucket: str
    prefix: str = ""
    endpoint_url: str | None = None
    region: str | None = None
    access_key_id: str | None = None
    secret_access_key: str | None = None


class EvidenceStoreS3:
    """
    S3-compatible evidence store.

    Keys:
    - {prefix}blobs/sha256/<digest>.bin
    - {prefix}packs/<pack_id>.json
    """

    def __init__(self, cfg: S3StoreConfig) -> None:
        self.cfg = cfg
        self.bucket = cfg.bucket
        self.prefix = cfg.prefix or ""

        import boto3
        from botocore.config import Config

        session = boto3.session.Session(
            aws_access_key_id=cfg.access_key_id,
            aws_secret_access_key=cfg.secret_access_key,
            region_name=cfg.region,
        )
        # MinIO and many S3-compatible providers require path-style addressing.
        self._s3 = session.client(
            "s3",
            endpoint_url=cfg.endpoint_url,
            config=Config(s3={"addressing_style": "path"}),
        )

    def _key(self, suffix: str) -> str:
        return f"{self.prefix}{suffix}"

    def put_blob(self, data: bytes) -> str:
        digest = sha256_hex(data)
        key = self._key(f"blobs/sha256/{digest}.bin")
        try:
            self._s3.head_object(Bucket=self.bucket, Key=key)
            return digest
        except Exception:  # noqa: BLE001
            pass
        self._s3.put_object(Bucket=self.bucket, Key=key, Body=data)
        return digest

    def get_blob(self, digest: str) -> bytes:
        key = self._key(f"blobs/sha256/{digest}.bin")
        obj = self._s3.get_object(Bucket=self.bucket, Key=key)
        return obj["Body"].read()

    def has_blob(self, digest: str) -> bool:
        key = self._key(f"blobs/sha256/{digest}.bin")
        try:
            self._s3.head_object(Bucket=self.bucket, Key=key)
            return True
        except Exception:  # noqa: BLE001
            return False

    def delete_blob(self, digest: str) -> bool:
        key = self._key(f"blobs/sha256/{digest}.bin")
        self._s3.delete_object(Bucket=self.bucket, Key=key)
        return True

    def has_pack(self, evidence_pack_id: str) -> bool:
        key = self._key(f"packs/{evidence_pack_id}.json")
        try:
            self._s3.head_object(Bucket=self.bucket, Key=key)
            return True
        except Exception:  # noqa: BLE001
            return False

    def put_pack(self, pack: dict[str, Any]):  # noqa: ANN001
        payload = canonical_json_bytes(pack)
        pack_id = sha256_hex(payload)
        key = self._key(f"packs/{pack_id}.json")
        try:
            self._s3.head_object(Bucket=self.bucket, Key=key)
        except Exception:  # noqa: BLE001
            self._s3.put_object(Bucket=self.bucket, Key=key, Body=payload)
        return type("EvidencePackRef", (), {"evidence_pack_id": pack_id})()

    def get_pack(self, evidence_pack_id: str) -> dict[str, Any]:
        key = self._key(f"packs/{evidence_pack_id}.json")
        obj = self._s3.get_object(Bucket=self.bucket, Key=key)
        payload = obj["Body"].read()
        if sha256_hex(payload) != evidence_pack_id:
            raise ValueError("evidence_pack_integrity_failed")
        return json.loads(payload.decode("utf-8"))
