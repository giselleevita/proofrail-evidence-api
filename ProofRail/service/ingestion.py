from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from ProofRail.sanctions_determinism import (
    DEFAULT_SOURCES,
    MODEL_VERSION,
    TRANSFORM_ID,
    compute_list_version,
    fetch_source,
    normalised_name_set,
    parse_eu_names,
    parse_ofac_names,
    parse_un_names,
)
from ProofRail.service.models import IngestArtifact
from ProofRail.service.storage import EvidenceStore, canonical_json_bytes
from ProofRail.service.utils import utc_now_iso


def ingest_sources(
    *,
    store: EvidenceStore,
    output_dir: Path,
    retrieval_ts: str | None = None,
    max_retries: int = 3,
) -> IngestArtifact:
    """Fetch sanction sources and persist raw payloads content-addressed."""
    from ProofRail.sanctions_determinism import SourceIngestion, build_session

    output_dir.mkdir(parents=True, exist_ok=True)
    retrieval_ts = retrieval_ts or utc_now_iso()
    ingestion_run_id = (
        f"ing_{retrieval_ts.replace(':', '').replace('-', '').replace('T', '_').replace('Z', '')}"
    )

    session = build_session(max_retries=max_retries)
    ingestions: list[dict[str, Any]] = []
    raw_payloads: dict[str, bytes] = {}

    for source_id, url in DEFAULT_SOURCES.items():
        meta, payload = fetch_source(
            session=session,
            source_id=source_id,
            url=url,
            retrieval_ts=retrieval_ts,
            ingestion_run_id=ingestion_run_id,
            output_dir=output_dir,
        )
        meta_dict = asdict(meta)
        if payload is not None:
            blob_sha = store.put_blob(payload)
            meta_dict["content_blob_sha256"] = blob_sha
        else:
            meta_dict["content_blob_sha256"] = None
        ingestions.append(meta_dict)
        if payload is not None and meta.status == "ok":
            raw_payloads[source_id] = payload

    list_version = compute_list_version(
        [
            SourceIngestion(
                source_id=item["source_id"],
                url=item["url"],
                status=item["status"],
                status_code=item["status_code"],
                retrieval_ts=item["retrieval_ts"],
                content_sha256=item["content_sha256"],
                bytes_len=item["bytes_len"],
                output_file=item["output_file"],
                error=item["error"],
                ingestion_run_id=item["ingestion_run_id"],
                transform_id=item["transform_id"],
                model_version=item["model_version"],
            )
            for item in ingestions
        ],
        retrieval_ts,
    )

    eu_names = parse_eu_names(raw_payloads.get("EU", b""))
    ofac_names = parse_ofac_names(raw_payloads.get("OFAC", b""))
    un_names = parse_un_names(raw_payloads.get("UN", b""))
    sanctions_name_sets = {
        "EU": normalised_name_set(eu_names),
        "OFAC": normalised_name_set(ofac_names),
        "UN": normalised_name_set(un_names),
    }
    norm_blob = canonical_json_bytes({k: sorted(v) for k, v in sanctions_name_sets.items()})
    norm_blob_sha = store.put_blob(norm_blob)

    return IngestArtifact(
        retrieval_timestamp=retrieval_ts,
        list_version=list_version,
        ingestion_run_id=ingestion_run_id,
        normalized_name_sets_blob_sha256=norm_blob_sha,
        sources=ingestions,
        entry_counts={"EU": len(eu_names), "OFAC": len(ofac_names), "UN": len(un_names)},
        transform_id=TRANSFORM_ID,
        model_version=MODEL_VERSION,
    )
