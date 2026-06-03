from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from typing import Any


@dataclass(frozen=True)
class PdfExportConfig:
    max_hits: int = 50


def render_evidence_pack_pdf(
    *,
    pack: dict[str, Any],
    evidence_pack_id: str,
    request_id: str | None,
    cfg: PdfExportConfig | None = None,
) -> bytes:
    """Render an Evidence Pack to a deterministic, auditor-friendly PDF."""
    cfg = cfg or PdfExportConfig()

    # Import here so API can still start if PDF deps are missing,
    # but endpoint will fail clearly.
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfbase.pdfmetrics import stringWidth
    from reportlab.pdfgen import canvas

    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    width, height = A4

    margin_x = 18 * mm
    y = height - 20 * mm

    def h1(text: str) -> None:
        nonlocal y
        c.setFont("Helvetica-Bold", 14)
        c.drawString(margin_x, y, text)
        y -= 8 * mm

    def h2(text: str) -> None:
        nonlocal y
        c.setFont("Helvetica-Bold", 11)
        c.drawString(margin_x, y, text)
        y -= 6 * mm

    def p(label: str, value: str) -> None:
        nonlocal y
        c.setFont("Helvetica", 10)
        c.drawString(margin_x, y, f"{label}: {value}")
        y -= 5 * mm

    def bullet(text: str) -> None:
        nonlocal y
        c.setFont("Helvetica", 10)
        c.drawString(margin_x + 3 * mm, y, f"- {text}")
        y -= 5 * mm

    def ensure_space(lines: int = 1) -> None:
        nonlocal y
        if y - (lines * 5 * mm) < 15 * mm:
            c.showPage()
            y = height - 20 * mm

    def safe_str(v: Any) -> str:
        if v is None:
            return ""
        if isinstance(v, str | int | float | bool):
            return str(v)
        return str(v)

    # Header
    h1("ProofRail Evidence Pack")
    p("Evidence pack id", evidence_pack_id)
    p("Created at", safe_str(pack.get("created_at")))
    p("Customer id", safe_str(pack.get("customer_id")))
    p("Request id", safe_str(request_id))
    p("List version", safe_str(pack.get("list_version")))

    # Input
    ensure_space(2)
    h2("Input")
    subject = (
        (pack.get("input") or {}).get("subject") if isinstance(pack.get("input"), dict) else None
    )
    if isinstance(subject, dict):
        p("Subject name", safe_str(subject.get("name")))
        if subject.get("subject_id"):
            p("Subject id", safe_str(subject.get("subject_id")))
    else:
        p("Subject", safe_str(subject))

    # Result
    ensure_space(3)
    h2("Result")
    result = pack.get("result") if isinstance(pack.get("result"), dict) else {}
    p("Decision", safe_str(result.get("decision")))
    hits = result.get("hits") if isinstance(result.get("hits"), list) else []
    hits = [safe_str(h) for h in hits][: cfg.max_hits]
    p("Hits (count)", str(len(hits)))
    for h in hits:
        ensure_space(1)
        bullet(h)

    # Explainability (optional but recommended)
    match_type = safe_str(result.get("match_type"))
    score = safe_str(result.get("score"))
    reason_codes = (
        result.get("reason_codes") if isinstance(result.get("reason_codes"), list) else []
    )
    reason_codes = [safe_str(rc) for rc in reason_codes]
    if match_type or score or reason_codes:
        ensure_space(2)
        h2("Explainability")
        if match_type:
            p("Match type", match_type)
        if score:
            p("Score", score)
        if reason_codes:
            p("Reason codes", ", ".join(reason_codes[:20]))

    # Why (simple, stable explanation)
    ensure_space(3)
    h2("Why")
    decision = safe_str(result.get("decision"))
    if decision == "block":
        p("Reason", "One or more sanctions list matches were found.")
    elif decision == "review":
        p("Reason", "Manual review required by policy.")
    else:
        p("Reason", "No matches were found for the normalized subject name.")
    p("Match basis", "Name normalization + exact set membership (deterministic).")

    # Review decision (optional)
    review = pack.get("review_decision") if isinstance(pack.get("review_decision"), dict) else None
    if isinstance(review, dict) and review.get("outcome"):
        ensure_space(3)
        h2("Review decision")
        p("Outcome", safe_str(review.get("outcome")))
        if review.get("decided_at"):
            p("Decided at", safe_str(review.get("decided_at")))
        note = review.get("note")
        if isinstance(note, str) and note.strip():
            # Wrap long notes to avoid clipping.
            max_w = width - (2 * margin_x)
            c.setFont("Helvetica", 10)
            words = note.strip().split()
            line = ""
            lines: list[str] = []
            for w in words:
                cand = (line + " " + w).strip()
                if stringWidth(cand, "Helvetica", 10) <= max_w:
                    line = cand
                else:
                    if line:
                        lines.append(line)
                    line = w
            if line:
                lines.append(line)
            ensure_space(1 + len(lines))
            p("Note", lines[0] if lines else "")
            for extra in lines[1:]:
                ensure_space(1)
                c.drawString(margin_x, y, extra)
                y -= 5 * mm

    # Case timeline (optional)
    timeline = pack.get("case_timeline") if isinstance(pack.get("case_timeline"), list) else None
    if isinstance(timeline, list) and timeline:
        ensure_space(3)
        h2("Case timeline")
        for ev in timeline[:50]:
            if not isinstance(ev, dict):
                continue
            ts = safe_str(ev.get("ts"))
            actor = safe_str(ev.get("actor"))
            et = safe_str(ev.get("event_type"))
            note = safe_str(ev.get("note"))
            ensure_space(1)
            bullet(f"{ts} {actor} {et}{(' — ' + note) if note else ''}")

    # Ingestion summary
    ensure_space(3)
    h2("Ingestion")
    ingestion = pack.get("ingestion") if isinstance(pack.get("ingestion"), dict) else {}
    p("Retrieval timestamp", safe_str(ingestion.get("retrieval_timestamp")))
    p("Ingestion run id", safe_str(ingestion.get("ingestion_run_id")))
    p("Normalized name sets blob", safe_str(ingestion.get("normalized_name_sets_blob_sha256")))
    entry_counts = (
        ingestion.get("entry_counts") if isinstance(ingestion.get("entry_counts"), dict) else {}
    )
    if entry_counts:
        p(
            "Entry counts",
            ", ".join(f"{k}={safe_str(entry_counts[k])}" for k in sorted(entry_counts.keys())),
        )

    sources = ingestion.get("sources") if isinstance(ingestion.get("sources"), list) else []
    if sources:
        ensure_space(2)
        h2("Sources")
        for s in sources:
            if not isinstance(s, dict):
                continue
            sid = safe_str(s.get("source_id"))
            sha = safe_str(s.get("content_sha256"))
            status = safe_str(s.get("status"))
            line = f"{sid} status={status} sha256={sha}"
            ensure_space(1)
            # Simple wrapping for long lines
            c.setFont("Helvetica", 9)
            max_w = width - 2 * margin_x
            if stringWidth(line, "Helvetica", 9) <= max_w:
                c.drawString(margin_x, y, line)
                y -= 5 * mm
            else:
                # wrap at a safe point
                parts: list[str] = []
                cur = ""
                for token in line.split(" "):
                    nxt = (cur + " " + token).strip()
                    if stringWidth(nxt, "Helvetica", 9) > max_w and cur:
                        parts.append(cur)
                        cur = token
                    else:
                        cur = nxt
                if cur:
                    parts.append(cur)
                for part in parts:
                    ensure_space(1)
                    c.drawString(margin_x, y, part)
                    y -= 5 * mm

    # Determinism / proof
    ensure_space(2)
    h2("Determinism")
    determinism = pack.get("determinism") if isinstance(pack.get("determinism"), dict) else {}
    p("Canonical pack hash", safe_str(determinism.get("canonical_pack_hash")))

    # Footer
    c.setFont("Helvetica", 8)
    c.drawString(margin_x, 10 * mm, "Generated by ProofRail Evidence API")
    c.showPage()
    c.save()
    return buf.getvalue()
