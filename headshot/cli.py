"""
Command line:

    python -m headshot.cli run photo.jpg                 # uses HEADSHOT_MODE (default: mock)
    python -m headshot.cli run photo.jpg --mode live     # real APIs, billed
    python -m headshot.cli run photo.jpg --scenario retry_then_deliver
    python -m headshot.cli runs                          # recent runs
    python -m headshot.cli show <run_id>                 # one run with every call
    python -m headshot.cli stats --days 30               # cost and outcome summary
    python -m headshot.cli scenarios                     # mock scenarios
    python -m headshot.cli check-store                   # write, read back and delete a test run
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .imaging import ImageTooSmall, InvalidImage
from .mock_data import SCENARIOS
from .pipeline import LiveUnavailable, create_run, current_mode, execute_run
from .settings import Settings
from .store import build_store

EXIT = {"delivered": 0, "manual_review": 1, "rejected": 1, "error": 2}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="headshot", description="Malameh headshot pipeline")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Run the pipeline on one photo")
    run.add_argument("image", type=Path)
    run.add_argument("--mode", choices=["mock", "live"], help="Default: the saved admin choice, else HEADSHOT_MODE")
    run.add_argument("--scenario", help="Mock scenario (see `scenarios`)")
    run.add_argument("--json", action="store_true", help="Print the full run as JSON")

    runs = sub.add_parser("runs", help="List recent runs")
    runs.add_argument("--limit", type=int, default=20)

    show = sub.add_parser("show", help="Show one run with all calls")
    show.add_argument("run_id")

    stats = sub.add_parser("stats", help="Cost and outcome summary")
    stats.add_argument("--days", type=int, default=30)

    sub.add_parser("scenarios", help="List mock scenarios")
    sub.add_parser("check-store", help="Write, read back and delete a test run in the configured trace store")

    args = parser.parse_args(argv)
    settings = Settings.from_env()
    store = build_store(settings)

    if args.command == "run":
        return _run(settings, store, args)
    if args.command == "runs":
        rows, total = store.list_runs(limit=args.limit)
        print(f"{total} run(s)")
        for r in rows:
            cost = f"${r['cost_usd']:.4f}{' est' if r['cost_estimated'] else ''}"
            print(f"{r['created_at'][:19]}  {r['id']}  {r['mode']:4}  {r['status']:13}  attempts={r['attempts']}  {cost}")
        return 0
    if args.command == "show":
        run = store.get_run(args.run_id)
        if run is None:
            print("Run not found", file=sys.stderr)
            return 2
        print(json.dumps({"run": run, "spans": store.list_spans(run["id"])}, indent=2, ensure_ascii=False))
        return 0
    if args.command == "stats":
        since = (datetime.now(timezone.utc) - timedelta(days=args.days)).isoformat(timespec="seconds")
        print(json.dumps(store.stats(since), indent=2))
        return 0
    if args.command == "scenarios":
        for sc in SCENARIOS.values():
            print(f"{sc.name:20} {sc.description}")
        return 0
    if args.command == "check-store":
        return check_store(store)
    return 2


def check_store(store) -> int:
    """Exercise every store operation the pipeline uses, then clean up. Exit 0 when all pass."""
    import io

    from PIL import Image

    from .store import new_id, utcnow

    print(f"Store: {store.kind}")
    run_id = new_id()
    image = io.BytesIO()
    Image.new("RGB", (8, 8), (200, 200, 200)).save(image, format="JPEG")
    jpeg = image.getvalue()

    steps = [
        ("create run", lambda: store.create_run({
            "id": run_id, "created_at": utcnow(), "updated_at": utcnow(), "mode": "mock", "source": "check",
            "status": "queued", "attempts": 0, "cost_usd": 0, "cost_estimated": True,
        })),
        ("update run", lambda: store.update_run(run_id, {"status": "error", "error": "store check", "finished_at": utcnow()})),
        ("add span", lambda: store.add_span({
            "id": new_id(), "run_id": run_id, "seq": 1, "mode": "mock", "stage": "analyze", "attempt": 0,
            "provider": "mock", "model": "check", "status": "ok", "started_at": utcnow(), "ended_at": utcnow(),
            "duration_ms": 1, "request": {"check": True}, "response": {"ok": True}, "usage": {"input_tokens": 1},
            "cost_usd": 0.000001, "cost_estimated": True,
        })),
        ("upload file", lambda: store.put_file(run_id, "input.jpg", jpeg, "image/jpeg")),
        ("download file", lambda: _expect(store.get_file(run_id, "input.jpg") == jpeg, "downloaded bytes differ")),
        ("read run", lambda: _expect((store.get_run(run_id) or {}).get("status") == "error", "run not read back")),
        ("read spans", lambda: _expect(len(store.list_spans(run_id)) == 1, "span not read back")),
        ("list runs", lambda: _expect(store.list_runs(limit=5)[1] >= 1, "run list is empty")),
        ("stats", lambda: _expect("runs" in store.stats("2000-01-01T00:00:00+00:00"), "stats missing fields")),
        ("settings", lambda: store.get_setting("mode")),
    ]
    ok = True
    for name, step in steps:
        try:
            step()
            print(f"  ✓ {name}")
        except Exception as exc:  # noqa: BLE001 - report every failure plainly
            print(f"  ✕ {name}: {exc}")
            ok = False
            break
    try:
        store.delete_run(run_id)
        _expect(store.get_run(run_id) is None, "run still present after delete")
        _expect(store.get_file(run_id, "input.jpg") is None, "file still present after delete")
        print("  ✓ delete run and files")
    except Exception as exc:  # noqa: BLE001
        print(f"  ✕ delete: {exc}")
        ok = False
    print("All checks passed." if ok else "Store check FAILED.")
    return 0 if ok else 2


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _run(settings: Settings, store, args) -> int:
    if not args.image.is_file():
        print(f"File not found: {args.image}", file=sys.stderr)
        return 2
    mode = args.mode or current_mode(settings, store)
    try:
        run_id, image = create_run(settings, store, args.image.read_bytes(), mode=mode, source="cli", scenario=args.scenario)
    except (InvalidImage, ImageTooSmall, LiveUnavailable, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    print(f"Run {run_id}  mode={mode}  input={image.width}x{image.height}  store={store.kind}")
    run = execute_run(settings, store, run_id)
    if args.json:
        print(json.dumps({"run": run, "spans": store.list_spans(run_id)}, indent=2, ensure_ascii=False))
    else:
        for span in store.list_spans(run_id):
            cost = "—" if span["cost_usd"] is None else f"${span['cost_usd']:.4f}"
            mark = "✕" if span["status"] == "error" else "✓"
            print(f"  {mark} {span['stage']:9} try {span['attempt']}  {span['model']:24} {span['duration_ms']:>6} ms  {cost}")
        estimate = " (estimate)" if run["cost_estimated"] else ""
        print(f"Result: {run['status']}  reason={run.get('reason')}  attempts={run['attempts']}  "
              f"cost=${run['cost_usd']:.4f}{estimate}  time={run['duration_ms']} ms")
        if run.get("output_file"):
            where = settings.data_dir / "runs" / run_id / run["output_file"] if store.kind == "sqlite" else run["output_file"]
            print(f"Image: {where}")
        if run.get("error"):
            print(f"Error: {run['error']}")
    return EXIT.get(run["status"], 2)


if __name__ == "__main__":
    raise SystemExit(main())
