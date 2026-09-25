"""Build data/db.sqlite from data/seed.sql.

The database is a build artifact, not a source file: seed.sql is committed and the
.sqlite it produces is gitignored. Anyone cloning the repo runs this once and gets a
byte-identical database, and a change to the seed data shows up as a readable diff
rather than an opaque binary blob.

Usage:
    python scripts/build_db.py            # rebuild data/db.sqlite
    python scripts/build_db.py --out X    # rebuild somewhere else (used by tests)
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SEED = PROJECT_ROOT / "data" / "seed.sql"
DEFAULT_OUT = PROJECT_ROOT / "data" / "db.sqlite"

TABLES = ["clients", "config_params", "auth_records", "son_docs", "son_chunks", "code_snippets"]


def build(seed_path: Path, out_path: Path) -> sqlite3.Connection:
    """Rebuild the database from scratch. Safe to run repeatedly."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.unlink(missing_ok=True)

    conn = sqlite3.connect(out_path)
    conn.executescript(seed_path.read_text(encoding="utf-8"))
    conn.commit()

    violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise SystemExit(f"foreign key violations in seed data: {violations}")

    return conn


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=Path, default=DEFAULT_SEED)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    conn = build(args.seed, args.out)

    print(f"built {args.out.relative_to(PROJECT_ROOT) if args.out.is_relative_to(PROJECT_ROOT) else args.out}")
    for table in TABLES:
        count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"  {table:<15} {count:>4} rows")

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
