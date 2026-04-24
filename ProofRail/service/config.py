from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppConfig:
    store_root: Path
    raw_dir: Path
    db_path: Path
    db_url: str | None
    admin_key: str | None
    # Back-compat: used by v1 signature endpoints.
    signing_secret: bytes
    # v2+: key-id -> secret bytes
    signing_keys: Mapping[str, bytes]
    signing_key_current: str | None

    rpm: int
    ingest_ttl_seconds: int
    screen_cache_max: int

    usage_queue_max: int
    usage_batch_size: int
    usage_flush_interval_s: float

    enable_scheduler: bool

    usage_retention_days: int
    evidence_retention_days: int

    max_request_bytes: int

    webhook_timeout_s: float
    webhook_max_attempts: int
    webhook_retry_base_s: float

    s3_bucket: str | None
    s3_prefix: str
    s3_endpoint_url: str | None
    s3_region: str | None
    s3_access_key_id: str | None
    s3_secret_access_key: str | None


def load_config(environ: dict[str, str] | None = None) -> AppConfig:
    env = environ or os.environ

    store_root = Path(env.get("PROOFRAIL_STORE_DIR", "proofrail_store"))
    raw_dir = Path(env.get("PROOFRAIL_RAW_DIR", str(store_root / "raw_fetches")))
    db_path = Path(env.get("PROOFRAIL_DB_PATH", str(store_root / "proofrail.sqlite3")))
    db_url = env.get("PROOFRAIL_DB_URL")
    if db_url is not None:
        db_url = db_url.strip() or None

    admin_key = env.get("PROOFRAIL_ADMIN_KEY")
    signing_secret = env.get("PROOFRAIL_SIGNING_SECRET", "").encode("utf-8")

    # Signing key versioning (v2 bundles). Format:
    # PROOFRAIL_SIGNING_KEYS="k1:secret1,k2:secret2"
    # PROOFRAIL_SIGNING_KEY_CURRENT="k2"
    signing_keys_raw = env.get("PROOFRAIL_SIGNING_KEYS", "").strip()
    signing_keys: dict[str, bytes] = {}
    if signing_keys_raw:
        for part in signing_keys_raw.split(","):
            part = part.strip()
            if not part:
                continue
            if ":" not in part:
                continue
            kid, sec = part.split(":", 1)
            kid = kid.strip()
            sec = sec.strip()
            if kid and sec:
                signing_keys[kid] = sec.encode("utf-8")
    # Back-compat: if only PROOFRAIL_SIGNING_SECRET is set, expose it as key "v1".
    if not signing_keys and signing_secret:
        signing_keys["v1"] = signing_secret

    signing_key_current = env.get("PROOFRAIL_SIGNING_KEY_CURRENT")
    if signing_key_current is not None:
        signing_key_current = signing_key_current.strip() or None
    if signing_key_current is None and signing_keys:
        signing_key_current = next(iter(signing_keys.keys()))

    rpm = int(env.get("PROOFRAIL_RPM", "120"))
    ingest_ttl_seconds = int(env.get("PROOFRAIL_INGEST_TTL_SECONDS", "3600"))
    screen_cache_max = int(env.get("PROOFRAIL_SCREEN_CACHE_MAX", "2048"))

    usage_queue_max = int(env.get("PROOFRAIL_USAGE_QUEUE_MAX", "50000"))
    usage_batch_size = int(env.get("PROOFRAIL_USAGE_BATCH_SIZE", "200"))
    usage_flush_interval_s = float(env.get("PROOFRAIL_USAGE_FLUSH_INTERVAL_S", "1.0"))

    enable_scheduler = env.get("PROOFRAIL_ENABLE_SCHEDULER", "0") == "1"

    usage_retention_days = int(env.get("PROOFRAIL_USAGE_RETENTION_DAYS", "30"))
    evidence_retention_days = int(env.get("PROOFRAIL_EVIDENCE_RETENTION_DAYS", "30"))
    max_request_bytes = int(env.get("PROOFRAIL_MAX_REQUEST_BYTES", "1000000"))

    webhook_timeout_s = float(env.get("PROOFRAIL_WEBHOOK_TIMEOUT_S", "5.0"))
    webhook_max_attempts = int(env.get("PROOFRAIL_WEBHOOK_MAX_ATTEMPTS", "8"))
    webhook_retry_base_s = float(env.get("PROOFRAIL_WEBHOOK_RETRY_BASE_S", "2.0"))

    s3_bucket = env.get("PROOFRAIL_S3_BUCKET")
    if s3_bucket is not None:
        s3_bucket = s3_bucket.strip() or None
    s3_prefix = (env.get("PROOFRAIL_S3_PREFIX") or "").lstrip("/")
    if s3_prefix and not s3_prefix.endswith("/"):
        s3_prefix += "/"
    s3_endpoint_url = env.get("PROOFRAIL_S3_ENDPOINT_URL")
    if s3_endpoint_url is not None:
        s3_endpoint_url = s3_endpoint_url.strip() or None
    s3_region = env.get("PROOFRAIL_S3_REGION")
    if s3_region is not None:
        s3_region = s3_region.strip() or None
    s3_access_key_id = env.get("PROOFRAIL_S3_ACCESS_KEY_ID")
    if s3_access_key_id is not None:
        s3_access_key_id = s3_access_key_id.strip() or None
    s3_secret_access_key = env.get("PROOFRAIL_S3_SECRET_ACCESS_KEY")
    if s3_secret_access_key is not None:
        s3_secret_access_key = s3_secret_access_key.strip() or None

    return AppConfig(
        store_root=store_root,
        raw_dir=raw_dir,
        db_path=db_path,
        db_url=db_url,
        admin_key=admin_key,
        signing_secret=signing_secret,
        signing_keys=signing_keys,
        signing_key_current=signing_key_current,
        rpm=rpm,
        ingest_ttl_seconds=ingest_ttl_seconds,
        screen_cache_max=screen_cache_max,
        usage_queue_max=usage_queue_max,
        usage_batch_size=usage_batch_size,
        usage_flush_interval_s=usage_flush_interval_s,
        enable_scheduler=enable_scheduler,
        usage_retention_days=usage_retention_days,
        evidence_retention_days=evidence_retention_days,
        max_request_bytes=max_request_bytes,
        webhook_timeout_s=webhook_timeout_s,
        webhook_max_attempts=webhook_max_attempts,
        webhook_retry_base_s=webhook_retry_base_s,
        s3_bucket=s3_bucket,
        s3_prefix=s3_prefix,
        s3_endpoint_url=s3_endpoint_url,
        s3_region=s3_region,
        s3_access_key_id=s3_access_key_id,
        s3_secret_access_key=s3_secret_access_key,
    )
