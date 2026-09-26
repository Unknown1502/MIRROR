"""Verify the audit ledger's hash chain (build plan part 38: verify_chain()).

Offline mode reads the runtime database directly and recomputes every hash; it does not
trust the running service. Exit code 0 = intact, 1 = broken.

    python scripts/verify_ledger.py [--db data/mirror.db]
    python scripts/verify_ledger.py --url http://127.0.0.1:8000
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
    ap.add_argument("--db", type=Path, default=None)
    ap.add_argument("--url", default=None)
    a = ap.parse_args()

    if a.url:
        with urllib.request.urlopen(a.url + "/api/v1/audit/verify", timeout=10) as r:
            report = json.loads(r.read())
    else:
        sys.path.insert(0, str(ROOT / "backend"))
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session

        from app.audit.ledger import AuditLedger
        from app.core.config import load_settings

        db_path = a.db or (load_settings().data_dir / "mirror.db")
        if not db_path.exists():
            print(f"No ledger database at {db_path}")
            return 1
        engine = create_engine(f"sqlite:///{db_path.as_posix()}")
        with Session(engine) as s:
            report = AuditLedger().verify_chain(s).as_dict()

    print(json.dumps(report, indent=2))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    sys.exit(main())
