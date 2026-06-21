"""``energyintelligence`` command-line entrypoint: seed | serve | sim."""

from __future__ import annotations

import argparse
import sys


def _cmd_seed(args: argparse.Namespace) -> int:
    from .config import get_settings
    from .seed.loader import seed

    settings = get_settings()
    print(f"Seeding from {settings.dataset_dir} -> {settings.db_path} ...")
    counts = seed()
    for k, v in counts.items():
        print(f"  {k:16} {v:>8,}")
    print("Done.")
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn
    from pathlib import Path

    from .config import get_settings

    settings = get_settings()
    reload = not args.no_reload
    # Watch the backend package so Python edits trigger a reload.
    pkg_dir = str(Path(__file__).resolve().parent)
    uvicorn.run(
        "energyintelligence.web.app:app",
        host=args.host or settings.host,
        port=args.port or settings.port,
        reload=reload,
        reload_dirs=[pkg_dir] if reload else None,
    )
    return 0


def _cmd_sim(args: argparse.Namespace) -> int:
    from .simulators.cli import run_sim

    return run_sim(
        household_id=args.household,
        devices=args.devices,
        speed=args.speed,
        clock=args.clock,
        seed=args.seed,
        base_url=args.base_url,
        limit=args.limit,
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="energyintelligence", description="EnergyIntelligence CLI")
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("seed", help="Load the dataset into SQLite")
    sp.set_defaults(func=_cmd_seed)

    sv = sub.add_parser("serve", help="Run the FastAPI API backend (hot-reload on by default)")
    sv.add_argument("--host", default=None)
    sv.add_argument("--port", type=int, default=None)
    sv.add_argument("--no-reload", action="store_true",
                    help="disable hot-reload (reload is on by default for local dev)")
    sv.set_defaults(func=_cmd_serve)

    sm = sub.add_parser("sim", help="Stream telemetry from a household's devices")
    sm.add_argument("--household", required=True)
    sm.add_argument("--devices", default="all",
                    help="'all' or comma list of pv,battery,heatpump,ev,household")
    sm.add_argument("--speed", type=float, default=60.0,
                    help="simulated steps per real second")
    sm.add_argument("--clock", choices=["original", "rebase", "continue"],
                    default="original")
    sm.add_argument("--seed", type=int, default=42)
    sm.add_argument("--base-url", default="http://127.0.0.1:8000")
    sm.add_argument("--limit", type=int, default=None,
                    help="stop after N steps (for testing)")
    sm.set_defaults(func=_cmd_sim)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
