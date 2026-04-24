from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from ProofRail.service.app import create_app


def main() -> int:
    client = TestClient(create_app())
    full = client.get("/openapi.json").json()
    full.pop("servers", None)

    def filtered(prefix: str) -> dict[str, object]:
        schema = dict(full)
        paths = dict(schema.get("paths") or {})
        schema["paths"] = {
            p: v for p, v in paths.items() if p.startswith(prefix) or p in ("/healthz", "/readyz")
        }
        return schema

    out_v1 = Path(__file__).resolve().parent.parent / "tests" / "openapi.v1.snapshot.json"
    out_v2 = Path(__file__).resolve().parent.parent / "tests" / "openapi.v2.snapshot.json"
    out_v1.write_text(
        json.dumps(filtered("/v1/"), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    out_v2.write_text(
        json.dumps(filtered("/v2/"), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"wrote {out_v1}")
    print(f"wrote {out_v2}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
