from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from ProofRail.sanctions_determinism import normalise_name
from ProofRail.service.models import IngestArtifact
from ProofRail.service.storage import EvidenceStore


def ingest_demo(
    *, store: EvidenceStore, output_dir: Path, retrieval_ts: str | None
) -> IngestArtifact:
    """
    Deterministic, offline ingest for investor demos.

    - Always produces a stable name-set blob with one known hit.
    - Does not require network access.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # Keep the demo set tiny and stable.
    demo = {"DEMO_SANCTIONS": [normalise_name("John Doe")]}
    blob_sha = store.put_blob(json.dumps(demo, sort_keys=True).encode("utf-8"))

    ts = retrieval_ts or datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    list_version = f"{ts}|demo"
    return IngestArtifact(
        retrieval_timestamp=ts,
        list_version=list_version,
        ingestion_run_id="demo-run",
        normalized_name_sets_blob_sha256=blob_sha,
        sources=[{"id": "DEMO_SANCTIONS", "name": "Demo Sanctions List"}],
        entry_counts={"DEMO_SANCTIONS": 1},
        transform_id="demo",
        model_version="demo",
    )
