"""Generate WS JSON Schema files from Pydantic models (SPEC §6).

Run: python -m backend.app.schemas.generate_ws_schemas
Output: backend/app/schemas/ws/*.schema.json
"""

from __future__ import annotations

import json
from pathlib import Path

from backend.app.schemas.ws_models import ResyncRequest, SnapshotMessage, UpdateMessage

OUT_DIR = Path(__file__).resolve().parent / "ws"


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for model, name in [
        (SnapshotMessage, "snapshot.schema.json"),
        (UpdateMessage, "update.schema.json"),
        (ResyncRequest, "resync_request.schema.json"),
    ]:
        schema = model.model_json_schema()
        (OUT_DIR / name).write_text(json.dumps(schema, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"wrote {OUT_DIR / name}")


if __name__ == "__main__":
    main()
