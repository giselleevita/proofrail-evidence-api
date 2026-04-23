from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_json_bytes(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


@dataclass(frozen=True)
class EvidencePackRef:
    evidence_pack_id: str
    path: Path


class EvidenceStore:
    """Filesystem-backed evidence store.

    Storage layout is content-addressed to enable dedup and easy caching:

    - blobs/sha256/<hash>.bin
    - packs/<pack_id>.json
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self.blobs_dir = root / "blobs" / "sha256"
        self.packs_dir = root / "packs"
        self.root.mkdir(parents=True, exist_ok=True)
        self.blobs_dir.mkdir(parents=True, exist_ok=True)
        self.packs_dir.mkdir(parents=True, exist_ok=True)

    def put_blob(self, data: bytes) -> str:
        digest = sha256_hex(data)
        path = self.blobs_dir / f"{digest}.bin"
        if not path.exists():
            path.write_bytes(data)
        return digest

    def get_blob(self, digest: str) -> bytes:
        path = self.blobs_dir / f"{digest}.bin"
        return path.read_bytes()

    def evidence_pack_path(self, evidence_pack_id: str) -> Path:
        return self.packs_dir / f"{evidence_pack_id}.json"

    def has_pack(self, evidence_pack_id: str) -> bool:
        return self.evidence_pack_path(evidence_pack_id).exists()

    def put_pack(self, pack: dict[str, Any]) -> EvidencePackRef:
        payload = canonical_json_bytes(pack)
        pack_id = sha256_hex(payload)
        path = self.evidence_pack_path(pack_id)
        if not path.exists():
            path.write_bytes(payload)
        return EvidencePackRef(evidence_pack_id=pack_id, path=path)

    def get_pack(self, evidence_pack_id: str) -> dict[str, Any]:
        path = self.evidence_pack_path(evidence_pack_id)
        payload = path.read_bytes()
        digest = sha256_hex(payload)
        if digest != evidence_pack_id:
            raise ValueError("evidence_pack_integrity_failed")
        return json.loads(payload.decode("utf-8"))

    def delete_pack(self, evidence_pack_id: str) -> bool:
        path = self.evidence_pack_path(evidence_pack_id)
        if not path.exists():
            return False
        path.unlink()
        return True

    def delete_packs_before(
        self,
        *,
        cutoff: datetime,
        customer_id: str | None = None,
        dry_run: bool = False,
    ) -> int:
        deleted = 0
        for path in self.packs_dir.glob("*.json"):
            try:
                evidence_pack_id = path.stem
                payload = path.read_bytes()
                if sha256_hex(payload) != evidence_pack_id:
                    # Don't delete on hash mismatch automatically; treat as corrupted and leave for manual triage.
                    continue
                pack = json.loads(payload.decode("utf-8"))
                if customer_id is not None and pack.get("customer_id") != customer_id:
                    continue
                created_at = pack.get("created_at")
                if not isinstance(created_at, str):
                    continue
                # stored as ISO8601 Z string
                dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                if dt >= cutoff:
                    continue
                if not dry_run:
                    path.unlink()
                deleted += 1
            except Exception:
                continue
        return deleted
