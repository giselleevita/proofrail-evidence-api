from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from ProofRail.service.app import create_app


def main() -> int:
    client = TestClient(create_app())
    schema = client.get("/openapi.json").json()
    schema.pop("servers", None)

    out = Path(__file__).resolve().parent.parent / "tests" / "openapi.snapshot.json"
    out.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
