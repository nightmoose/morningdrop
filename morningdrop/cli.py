"""morningdrop run | watch | infer"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import yaml

from . import __version__, tracing
from .agent import Config, run_once
from .drops import load_records
from .infer import infer_contract


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="morningdrop", description=__doc__)
    ap.add_argument("--config", "-c", default="morningdrop.yaml")
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run", help="check new drops once and write the morning report (cron this)")
    w = sub.add_parser("watch", help="poll the folder forever")
    w.add_argument("--every", type=int, default=300, help="seconds between polls")
    i = sub.add_parser("infer", help="draft a DataContractGate contract from a sample file")
    i.add_argument("file")
    i.add_argument("--name")
    args = ap.parse_args(argv)

    if args.cmd == "infer":
        p = Path(args.file)
        print(yaml.safe_dump(infer_contract(args.name or p.stem, load_records(p)), sort_keys=False))
        return 0

    cfg = Config.load(Path(args.config) if Path(args.config).exists() else None)
    tracing.init()
    while True:
        res = run_once(cfg)
        if res.summary:
            print(f"{res.summary.line1}\n{res.summary.line2}")
            print(f"report: {res.report_path}  ({res.seconds:.1f}s, summary by {res.summary.source})",
                  file=sys.stderr)
        elif args.cmd == "run":
            print("No new drops.", file=sys.stderr)
        if args.cmd == "run":
            return 0
        time.sleep(args.every)


if __name__ == "__main__":
    sys.exit(main())
