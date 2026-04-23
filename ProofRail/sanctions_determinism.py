#!/usr/bin/env python3
import argparse
import hashlib
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

OUTPUT_DIR = Path("spike2_artifacts")
DEFAULT_SOURCES = {
    "EU": "https://webgate.ec.europa.eu/fsd/fsf/public/files/xmlFullSanctionsList_1_1/content?token=dG9rZW4tMjAxNw",
    "UN": "https://main.un.org/securitycouncil/en/content/un-sc-consolidated-list",
    "OFAC": "https://www.treasury.gov/ofac/downloads/sdn.xml",
}
TRANSFORM_ID = "t_sanctions_determinism_v2"
MODEL_VERSION = "none"


@dataclass
class SourceIngestion:
    source_id: str
    url: str
    status: str
    status_code: int | None
    retrieval_ts: str
    content_sha256: str | None
    bytes_len: int
    output_file: str | None
    error: str | None
    ingestion_run_id: str
    transform_id: str
    model_version: str


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def normalise_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", " ", name.upper()).strip()
    return re.sub(r"\s+", " ", cleaned)


def normalised_name_set(names: list[str]) -> set[str]:
    out: set[str] = set()
    for name in names:
        normalised = normalise_name(name)
        if normalised:
            out.add(normalised)
    return out


def build_session(max_retries: int) -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=max_retries,
        backoff_factor=1.0,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def fetch_source(
    session: requests.Session,
    source_id: str,
    url: str,
    retrieval_ts: str,
    ingestion_run_id: str,
    output_dir: Path,
) -> tuple[SourceIngestion, bytes | None]:
    try:
        response = session.get(url, timeout=120, allow_redirects=True)
        content = response.content
        status = "ok" if response.status_code == 200 else "http_error"
        output_file = output_dir / f"{source_id.lower()}_raw.bin"
        output_file.write_bytes(content)
        return (
            SourceIngestion(
                source_id=source_id,
                url=url,
                status=status,
                status_code=response.status_code,
                retrieval_ts=retrieval_ts,
                content_sha256=sha256_bytes(content),
                bytes_len=len(content),
                output_file=str(output_file.resolve()),
                error=None if status == "ok" else f"HTTP_{response.status_code}",
                ingestion_run_id=ingestion_run_id,
                transform_id=TRANSFORM_ID,
                model_version=MODEL_VERSION,
            ),
            content,
        )
    except Exception as exc:
        return (
            SourceIngestion(
                source_id=source_id,
                url=url,
                status="exception",
                status_code=None,
                retrieval_ts=retrieval_ts,
                content_sha256=None,
                bytes_len=0,
                output_file=None,
                error=str(exc),
                ingestion_run_id=ingestion_run_id,
                transform_id=TRANSFORM_ID,
                model_version=MODEL_VERSION,
            ),
            None,
        )


def parse_eu_names(xml_bytes: bytes) -> list[str]:
    names: list[str] = []
    if not xml_bytes:
        return names
    try:
        root = ET.fromstring(xml_bytes)
        for node in root.iter():
            if node.tag.endswith("nameAlias"):
                whole_name = node.attrib.get("wholeName")
                if whole_name:
                    names.append(whole_name)
    except ET.ParseError:
        pass
    if names:
        return names
    for match in re.finditer(rb'wholeName="([^"]+)"', xml_bytes):
        names.append(match.group(1).decode("utf-8", errors="ignore"))
    return names


def parse_ofac_names(xml_bytes: bytes) -> list[str]:
    names: list[str] = []
    if not xml_bytes:
        return names
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return names
    for entry in root.iter():
        if entry.tag.endswith("sdnEntry"):
            first_name = ""
            last_name = ""
            for child in entry:
                if child.tag.endswith("firstName") and child.text:
                    first_name = child.text.strip()
                elif child.tag.endswith("lastName") and child.text:
                    last_name = child.text.strip()
            full_name = f"{first_name} {last_name}".strip()
            if full_name:
                names.append(full_name)
    return names


def parse_un_names(payload_bytes: bytes) -> list[str]:
    names: list[str] = []
    if not payload_bytes:
        return names
    try:
        root = ET.fromstring(payload_bytes)
    except ET.ParseError:
        return names
    for node in root.iter():
        if node.tag.endswith("FIRST_NAME") and node.text:
            names.append(node.text.strip())
    return names


def build_subjects(reference_names: list[str], max_subjects: int) -> list[dict[str, str]]:
    unique: list[str] = []
    seen: set[str] = set()
    for name in reference_names:
        normalised = normalise_name(name)
        if normalised and normalised not in seen:
            seen.add(normalised)
            unique.append(name)
        if len(unique) >= max_subjects:
            break
    subjects = [{"subject_id": f"sub_{idx:04d}", "name": name} for idx, name in enumerate(unique)]
    subjects.extend(
        [
            {"subject_id": "sub_neg_0001", "name": "ALICE EXAMPLE"},
            {"subject_id": "sub_neg_0002", "name": "BOB EXAMPLE"},
            {"subject_id": "sub_neg_0003", "name": "SCANDINAVIAN TEST PERSON"},
        ]
    )
    return subjects


def deterministic_match(
    subjects: list[dict[str, str]],
    sanctions_names: dict[str, set[str]],
    list_version: str,
    ingestion_run_id: str,
) -> tuple[list[dict[str, Any]], str]:
    output: list[dict[str, Any]] = []
    for subject in subjects:
        normalised = normalise_name(subject["name"])
        hits = [source_id for source_id, name_set in sanctions_names.items() if normalised in name_set]
        output.append(
            {
                "subject_id": subject["subject_id"],
                "name": subject["name"],
                "name_norm": normalised,
                "hits": sorted(hits),
                "list_version": list_version,
                "provenance": {
                    "source_id": "sanctions_matcher",
                    "ingestion_run_id": ingestion_run_id,
                    "transform_id": TRANSFORM_ID,
                    "model_version": MODEL_VERSION,
                },
            }
        )
    output = sorted(output, key=lambda item: item["subject_id"])
    encoded = json.dumps(output, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return output, sha256_bytes(encoded)


def compute_list_version(ingestions: list[SourceIngestion], retrieval_ts: str) -> str:
    source_hash_material = []
    for source in sorted(ingestions, key=lambda item: item.source_id):
        source_hash_material.append(f"{source.source_id}:{source.content_sha256 or 'NONE'}:{source.status}")
    combined_hash = sha256_bytes("|".join(source_hash_material).encode("utf-8"))
    return f"{retrieval_ts}|hash:{combined_hash}"


def run_match_pipeline(
    retrieval_ts: str | None = None,
    list_version_override: str | None = None,
    max_subjects: int = 100,
    output_dir: Path = OUTPUT_DIR,
    max_retries: int = 3,
    source_overrides: dict[str, bytes] | None = None,
) -> dict[str, Any]:
    output_dir.mkdir(exist_ok=True)
    if retrieval_ts is None:
        retrieval_ts = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    ingestion_run_id = f"ing_{retrieval_ts.replace(':', '').replace('-', '').replace('T', '_').replace('Z', '')}"
    ingestions: list[SourceIngestion] = []
    raw_payloads: dict[str, bytes] = {}
    session = build_session(max_retries=max_retries)

    for source_id, url in DEFAULT_SOURCES.items():
        if source_overrides and source_id in source_overrides:
            payload = source_overrides[source_id]
            output_file = output_dir / f"{source_id.lower()}_raw.bin"
            output_file.write_bytes(payload)
            ingestion = SourceIngestion(
                source_id=source_id,
                url=url,
                status="ok",
                status_code=200,
                retrieval_ts=retrieval_ts,
                content_sha256=sha256_bytes(payload),
                bytes_len=len(payload),
                output_file=str(output_file.resolve()),
                error=None,
                ingestion_run_id=ingestion_run_id,
                transform_id=TRANSFORM_ID,
                model_version=MODEL_VERSION,
            )
            ingestions.append(ingestion)
            raw_payloads[source_id] = payload
            continue
        metadata, payload = fetch_source(session, source_id, url, retrieval_ts, ingestion_run_id, output_dir)
        ingestions.append(metadata)
        if payload is not None and metadata.status == "ok":
            raw_payloads[source_id] = payload

    eu_names = parse_eu_names(raw_payloads.get("EU", b""))
    ofac_names = parse_ofac_names(raw_payloads.get("OFAC", b""))
    un_names = parse_un_names(raw_payloads.get("UN", b""))

    sanctions_names = {
        "EU": normalised_name_set(eu_names),
        "OFAC": normalised_name_set(ofac_names),
        "UN": normalised_name_set(un_names),
    }
    subjects = build_subjects(eu_names + ofac_names + un_names, max_subjects=max_subjects)
    list_version = list_version_override or compute_list_version(ingestions, retrieval_ts)
    run1, run1_hash = deterministic_match(subjects, sanctions_names, list_version, ingestion_run_id)
    run2, run2_hash = deterministic_match(subjects, sanctions_names, list_version, ingestion_run_id)
    successful_sources = sum(1 for ingestion in ingestions if ingestion.status == "ok")
    if successful_sources == len(ingestions):
        run_status = "complete"
    elif successful_sources == 0:
        run_status = "failed"
    else:
        run_status = "partial"

    return {
        "retrieval_timestamp": retrieval_ts,
        "run_status": run_status,
        "ingestion_run_id": ingestion_run_id,
        "sources": [asdict(source) for source in ingestions],
        "entry_counts": {
            "EU_name_candidates": len(eu_names),
            "OFAC_name_candidates": len(ofac_names),
            "UN_name_candidates": len(un_names),
        },
        "normalised_name_set_sizes": {source_id: len(name_set) for source_id, name_set in sanctions_names.items()},
        "list_version": list_version,
        "subjects_count": len(subjects),
        "determinism": {
            "run1_hash": run1_hash,
            "run2_hash": run2_hash,
            "identical": run1_hash == run2_hash and run1 == run2,
            "non_determinism_causes": [] if run1_hash == run2_hash and run1 == run2 else [
                "unordered data structure iteration",
                "time-dependent fields in matcher output",
            ],
            "fixes_applied": [
                "sorted output by subject_id",
                "stable JSON serialisation for result hashing",
                "fixed list_version reused across both runs",
            ],
        },
        "sample_matches": run1[:10],
        "match_provenance": {
            "source_id": "sanctions_matcher",
            "ingestion_run_id": ingestion_run_id,
            "transform_id": TRANSFORM_ID,
            "model_version": MODEL_VERSION,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--retrieval-ts", default=None)
    parser.add_argument("--list-version", default=None)
    parser.add_argument("--max-subjects", type=int, default=100)
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--output", default=str(OUTPUT_DIR / "spike2_results.json"))
    parser.add_argument("--reference-hash-path", default=None)
    args = parser.parse_args()

    report = run_match_pipeline(
        retrieval_ts=args.retrieval_ts,
        list_version_override=args.list_version,
        max_subjects=args.max_subjects,
        max_retries=args.max_retries,
    )
    if args.reference_hash_path:
        reference_path = Path(args.reference_hash_path)
        reference_hash = reference_path.read_text(encoding="utf-8").strip() if reference_path.exists() else None
        report["cross_run_comparison"] = {
            "reference_hash_path": str(reference_path.resolve()),
            "reference_hash": reference_hash,
            "current_hash": report["determinism"]["run1_hash"],
            "matches_reference": reference_hash == report["determinism"]["run1_hash"] if reference_hash else None,
        }
        reference_path.write_text(report["determinism"]["run1_hash"], encoding="utf-8")

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    print(f"Wrote {output_path.resolve()}")


if __name__ == "__main__":
    main()
