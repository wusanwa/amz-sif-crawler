from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from amz_sif_crawler.daily_bindings import load_daily_bindings
from amz_sif_crawler.runtime.config import load_app_config
from amz_sif_crawler.service import export_daily_report_csv, run_cli


def _next_daily_output_path(base_dir: Path, bind_key: str, report_date: str) -> Path:
    bind_dir = base_dir / "runtime_data" / bind_key
    bind_dir.mkdir(parents=True, exist_ok=True)
    index = 1
    while True:
        candidate = bind_dir / f"{report_date}-{index}.csv"
        if not candidate.exists():
            return candidate
        index += 1


def _resolve_bind_key() -> str:
    env_bind_key = os.getenv("HERMES_BINDING_KEY", "").strip()
    if env_bind_key:
        return env_bind_key

    raise ValueError("bindKey is required via HERMES_BINDING_KEY environment variable")


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate daily Amazon + SIF CSV report by HERMES_BINDING_KEY")
    parser.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"), help="Report date suffix")
    parser.add_argument("--mode", choices=["both", "amazon", "sif"], default="both", help="Capture mode")
    args = parser.parse_args()

    try:
        bind_key = _resolve_bind_key()
    except ValueError as exc:
        sys.stderr.write(
            json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False, indent=2)
        )
        sys.stderr.write("\n")
        return 1

    config = load_app_config()
    bindings = load_daily_bindings(config.base_dir)
    urls = bindings.get(bind_key, [])
    if not urls:
        sys.stderr.write(
            json.dumps(
                {
                    "status": "error",
                    "bindKey": bind_key,
                    "message": f"bindKey not found: {bind_key}",
                    "availableBindKeys": sorted(bindings.keys()),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        sys.stderr.write("\n")
        return 1

    output_path = _next_daily_output_path(config.base_dir, bind_key, args.date)

    crawl_payload = run_cli(urls, mode=args.mode)
    csv_path = export_daily_report_csv(crawl_payload.get("results", []), output_path)

    print(csv_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
