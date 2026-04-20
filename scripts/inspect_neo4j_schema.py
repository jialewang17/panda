"""Inspect Neo4j node labels, relationship types, and predicates."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, List

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils.env_loader import get_env_config


def _load_graph_database_class() -> Any:
    """Lazy import neo4j driver to reduce import noise."""
    with contextlib.redirect_stderr(io.StringIO()):
        from neo4j import GraphDatabase as _GraphDatabase

    return _GraphDatabase


def _build_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect Neo4j schema statistics: labels, relationship types, predicates."
    )
    parser.add_argument(
        "--database",
        default="",
        help="Neo4j database name. Default: NEO4J_DATABASE env var or neo4j.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Top N predicates to show. Default: 100.",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Output JSON path. Default: sandbox/neo4j_schema_<timestamp>.json",
    )
    return parser


def main() -> int:
    parser = _build_cli_parser()
    args = parser.parse_args()

    get_env_config()
    uri = (os.getenv("NEO4J_URI") or "").strip()
    username = (os.getenv("NEO4J_USERNAME") or "").strip()
    password = (os.getenv("NEO4J_PASSWORD") or "").strip()
    database = (args.database or os.getenv("NEO4J_DATABASE") or "neo4j").strip()
    top_n = max(1, int(args.limit))
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    if args.output.strip():
        output_path = (PROJECT_ROOT / args.output).resolve()
    else:
        output_path = (PROJECT_ROOT / "sandbox" / f"neo4j_schema_{timestamp}.json").resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    missing: List[str] = []
    if not uri:
        missing.append("NEO4J_URI")
    if not username:
        missing.append("NEO4J_USERNAME")
    if not password:
        missing.append("NEO4J_PASSWORD")
    if missing:
        print(f"Missing Neo4j env vars: {', '.join(missing)}")
        return 1

    graph_database = _load_graph_database_class()
    driver = graph_database.driver(uri, auth=(username, password))
    try:
        with driver.session(database=database) as session:
            labels = list(
                session.run(
                    "MATCH (n) "
                    "UNWIND labels(n) AS label "
                    "RETURN label, count(*) AS cnt "
                    "ORDER BY cnt DESC"
                )
            )
            rel_types = list(
                session.run(
                    "MATCH ()-[r]->() "
                    "RETURN type(r) AS rel_type, count(*) AS cnt "
                    "ORDER BY cnt DESC"
                )
            )
            predicates = list(
                session.run(
                    "MATCH ()-[r]->() "
                    "WHERE r.predicate IS NOT NULL AND r.predicate <> '' "
                    "RETURN r.predicate AS predicate, count(*) AS cnt "
                    "ORDER BY cnt DESC "
                    "LIMIT $top_n",
                    top_n=top_n,
                )
            )
    except Exception as exc:
        print(f"Query failed: {type(exc).__name__}: {exc}")
        return 1
    finally:
        driver.close()

    payload = {
        "generated_at": datetime.now().isoformat(),
        "database": database,
        "predicate_limit": top_n,
        "node_labels": [
            {"label": str(row["label"]), "count": int(row["cnt"])}
            for row in labels
        ],
        "relationship_types": [
            {"type": str(row["rel_type"]), "count": int(row["cnt"])}
            for row in rel_types
        ],
        "predicates": [
            {"predicate": str(row["predicate"]), "count": int(row["cnt"])}
            for row in predicates
        ],
    }
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Schema JSON written: {output_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
