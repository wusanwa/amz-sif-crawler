from __future__ import annotations

import argparse
import json

from amz_sif_crawler.service import run_cli


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a single Amazon + SIF capture")
    parser.add_argument("url", nargs="+", help="Amazon product URL or ASIN")
    parser.add_argument("--outfile", default=None, help="Optional JSONL output path")
    parser.add_argument("--mode", choices=["both", "amazon", "sif"], default="both", help="Capture mode")
    args = parser.parse_args()

    payload = run_cli(args.url, outfile=args.outfile, mode=args.mode)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
