"""Reset the ERP to the deterministic demo baseline (build plan part 8).

With a running backend (DEMO_MODE=true) this calls POST /api/v1/demo/reset, which also
aborts open proposals and records the reset in the audit ledger. With --offline it resets
the ERP database file directly (backend must be stopped).

    python scripts/seed_demo.py [--url http://127.0.0.1:8000] [--offline]
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--demo-token", default=None)
    a = ap.parse_args()

    if a.offline:
        sys.path.insert(0, str(ROOT / "backend"))
        from app.core.config import load_settings
        from app.erp.service import MockERP

        s = load_settings()
        s.data_dir.mkdir(parents=True, exist_ok=True)
        erp = MockERP(s.erp_db_url)
        erp.reset_world()
        world = erp.read_snapshot()
    else:
        req = urllib.request.Request(a.url + "/api/v1/demo/reset", method="POST", data=b"")
        if a.demo_token:
            req.add_header("X-Demo-Token", a.demo_token)
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
        cells = {(c["location_id"], c["product_id"]): c["on_hand"] for c in data["cells"]}
        print(f"ERP revision {data['revision']}")
        for loc in ("MUMBAI-DC", "PUNE-SITE-1", "PUNE-SITE-2"):
            print(f"  {loc:<16} XR-17 = {cells[(loc, 'XR-17')]}")
        return 0

    for loc in ("MUMBAI-DC", "PUNE-SITE-1", "PUNE-SITE-2"):
        print(f"  {loc:<16} XR-17 = {world.cell(loc, 'XR-17').on_hand}")
    print(f"ERP revision {world.revision} (offline reset)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
