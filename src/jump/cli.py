"""`jump` command line. Each subcommand lazily imports the module that owns it (see docs/architecture.md)."""
import argparse
import json
import sys

from . import db


def _watch(conn, args):
    from .agents import watcher
    print(json.dumps(watcher.run_once(conn), indent=2))


def _plan(conn, args):
    from .agents import planner
    print(json.dumps(planner.run_once(conn), indent=2))


def _crawl(conn, args):
    from .agents import crawler
    print(json.dumps(crawler.run_pending(conn, max_jobs=args.max_jobs), indent=2))


def _name(conn, args):
    from .agents import namer
    print(json.dumps(namer.run_once(conn), indent=2))


def _places(conn, args):
    rows = conn.execute(
        "SELECT p.scope, p.status, p.revisit, p.budget, COUNT(g.id) AS pages, p.features FROM places p "
        "LEFT JOIN pages g ON g.place_id = p.id WHERE p.status IN (" + ",".join("?" * len(args.status)) + ") "
        "GROUP BY p.id ORDER BY p.revisit DESC LIMIT ?",
        (*args.status, args.limit),
    ).fetchall()
    for r in rows:
        print(f"{r['revisit']:.2f}  {r['status']:<9} {r['pages']:>4} pages  budget {r['budget']:>3}  {r['scope']}")
        if args.why and r["features"]:
            print("      " + r["features"])


def _search(conn, args):
    from .search import pipeline
    results = pipeline.search(conn, args.query, use_jev=not args.local)
    if args.json:
        print(json.dumps(results))
        return
    for i, r in enumerate(results, 1):
        conf = f"  ({r['probability']:.2f})" if r.get("probability") is not None else ""
        print(f"{i}. {r['label']}{conf}\n   {r['url']}")
    if args.open and results:
        choice = int(input("open which? [1] ") or 1)
        pipeline.record_pick(conn, args.query, results[choice - 1]["page_id"])
        pipeline.open_in_browser(results[choice - 1]["url"])


def _monitor(conn, args):
    import threading
    from .monitor import Monitor
    print("Watching the active Chrome tab. Ctrl-C to stop.")
    stop = threading.Event()
    try:
        Monitor(conn).run(stop)
    except KeyboardInterrupt:
        stop.set()


def _pick(conn, args):
    from .search import pipeline
    pipeline.record_pick(conn, args.query, args.page_id)


def _daemon(conn, args):
    from . import daemon
    daemon.run(conn)


def _service(conn, args):
    from . import service
    if args.action == "install":
        path = service.install(load=args.load)
        print(f"wrote {path}" + ("" if args.load else f"\nstart it with: launchctl load {path}"))
    else:
        service.uninstall()
        print("removed launchd agent")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="jump", description="Say a phrase, land on the page.")
    ap.add_argument("--db", help="database path (default ~/.jump/graph.db)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("pick", help="record that a search result was opened (used by Raycast)")
    p.add_argument("page_id", type=int)
    p.add_argument("--query", required=True)
    p.set_defaults(fn=_pick)
    sub.add_parser("monitor", help="record what you look at in Chrome (foreground)").set_defaults(fn=_monitor)
    sub.add_parser("watch", help="regroup and rescore places from recorded visits").set_defaults(fn=_watch)
    sub.add_parser("plan", help="assign crawl budgets and queue crawl jobs").set_defaults(fn=_plan)
    p = sub.add_parser("crawl", help="run pending crawl jobs")
    p.add_argument("--max-jobs", type=int, default=None)
    p.set_defaults(fn=_crawl)
    sub.add_parser("name", help="give pages short names from their own titles/headings").set_defaults(fn=_name)
    p = sub.add_parser("places", help="list places and their revisit scores")
    p.add_argument("--status", nargs="+", default=["active", "probation"])
    p.add_argument("--limit", type=int, default=30)
    p.add_argument("--why", action="store_true", help="show the features behind each score")
    p.set_defaults(fn=_places)
    p = sub.add_parser("search", help="find the page you mean")
    p.add_argument("query")
    p.add_argument("--local", action="store_true", help="skip Jev, use local ranking only")
    p.add_argument("--open", action="store_true", help="pick a result and open it in Chrome")
    p.add_argument("--json", action="store_true", help="print results as JSON (used by the Raycast command)")
    p.set_defaults(fn=_search)
    sub.add_parser("daemon", help="run all background agents").set_defaults(fn=_daemon)
    p = sub.add_parser("service", help="install/uninstall the launchd agent that keeps the daemon running")
    p.add_argument("action", choices=["install", "uninstall"])
    p.add_argument("--load", action="store_true", help="also start it now with launchctl")
    p.set_defaults(fn=_service)

    args = ap.parse_args(argv)
    conn = db.connect(args.db)
    args.fn(conn, args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
