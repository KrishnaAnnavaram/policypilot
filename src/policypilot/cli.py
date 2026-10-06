"""Command-line entry point: ``policypilot``."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__


def _seed(args: argparse.Namespace) -> None:
    from .config import Settings
    from .data.seed import build_documents, write_jsonl, write_sqlite

    settings = Settings.from_env()
    if args.csv:
        from .data.cleaning import load_csv

        data, report = load_csv(args.csv)
        print(f"read {report.rows_read} rows, dropped {report.duplicates_dropped} duplicate IDs, "
              f"rejected {report.rows_rejected}")
    else:
        from .data.synthetic import generate

        data = generate(n_customers=args.customers, seed=args.seed)
    path = write_sqlite(data, args.sqlite or settings.sqlite_path)
    print(f"SQLite: {path} {data.summary()}")
    docs = build_documents(data)
    if args.jsonl:
        print(f"documents: {write_jsonl(docs, args.jsonl)} ({len(docs)} docs, for mongoimport)")
    if args.mongo:
        from .data.seed import seed_mongo

        uri = args.mongo_admin_uri or settings.mongo_uri
        n = seed_mongo(docs, uri, settings.mongo_db, settings.mongo_collection)
        print(f"MongoDB: {settings.mongo_db}.{settings.mongo_collection} now holds {n} documents")
    if args.postgres:
        from .data.seed import write_postgres

        write_postgres(data, args.postgres)
        print("PostgreSQL: tables created and filled")


def _ask(args: argparse.Namespace) -> None:
    from .service import build_service

    service = build_service()
    response = service.ask(args.question)
    if args.json:
        print(json.dumps(response.to_dict(), indent=2, ensure_ascii=False, default=str))
        return
    a = response.answer
    print(f"[route: {response.decision.route} via {response.decision.source}]")
    print(a.text)
    if a.query:
        print(f"\nquery:\n{a.query}")
    if a.result is not None and a.result.rows:
        print("\n" + a.result.to_markdown())
    for s in a.sources:
        print(f"[{s.ref}] {s.source}")


def _eval(args: argparse.Namespace) -> None:
    from .evaluation import evaluate, format_report, load_gold
    from .evaluation.runner import write_report
    from .service import build_service

    report = evaluate(build_service(), load_gold(args.gold), k=args.k)
    print(format_report(report))
    if args.out:
        print(f"\nfull report: {write_report(report, args.out)}")


def _serve(args: argparse.Namespace) -> None:  # pragma: no cover - starts a server
    import uvicorn

    uvicorn.run("policypilot.api:create_app", factory=True, host=args.host, port=args.port)


def _ui(_: argparse.Namespace) -> None:  # pragma: no cover - starts a server
    import subprocess

    app = Path(__file__).parent / "ui" / "app.py"
    sys.exit(subprocess.call([sys.executable, "-m", "streamlit", "run", str(app)]))


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="policypilot", description="Multi-source insurance QA (SQL, documents, PDFs)")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("seed", help="create the database(s) from synthetic data or the public CSV")
    s.add_argument("--customers", type=int, default=300)
    s.add_argument("--seed", type=int, default=7)
    s.add_argument("--csv", help="path to car_insurance_claim.csv (cleaned into the canonical model)")
    s.add_argument("--sqlite", help="output SQLite path (default: SQLITE_PATH)")
    s.add_argument("--jsonl", help="also write the nested documents as JSON lines for mongoimport")
    s.add_argument("--mongo", action="store_true", help="also load MongoDB (needs an admin URI)")
    s.add_argument("--mongo-admin-uri", help="admin connection for seeding; the app must use a read-only user")
    s.add_argument("--postgres", metavar="OWNER_DSN", help="also create and fill PostgreSQL tables")
    s.set_defaults(func=_seed)

    a = sub.add_parser("ask", help="ask one question")
    a.add_argument("question")
    a.add_argument("--json", action="store_true")
    a.set_defaults(func=_ask)

    e = sub.add_parser("eval", help="run the evaluation harness")
    e.add_argument("--gold", help="gold questions JSON (default: the bundled set)")
    e.add_argument("--k", type=int, default=None, help="retrieval cut-off (default: RAG_TOP_K)")
    e.add_argument("--out", help="write the full JSON report here")
    e.set_defaults(func=_eval)

    v = sub.add_parser("serve", help="run the HTTP API (needs the 'api' extra)")
    v.add_argument("--host", default="127.0.0.1")
    v.add_argument("--port", type=int, default=8000)
    v.set_defaults(func=_serve)

    u = sub.add_parser("ui", help="run the Streamlit UI (needs the 'ui' extra)")
    u.set_defaults(func=_ui)

    args = p.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")      # e.g. CJK answers on a cp1252 console
        except (AttributeError, ValueError):
            pass
    args.func(args)


if __name__ == "__main__":
    main()
