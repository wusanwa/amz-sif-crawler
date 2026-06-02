from __future__ import annotations

import argparse
import json
import os

from amz_sif_crawler.daily_bindings import add_daily_asins, load_daily_bindings, remove_daily_asins
from amz_sif_crawler.runtime.config import load_app_config


def _resolve_bind_key() -> str:
    env_bind_key = os.getenv("HERMES_BINDING_KEY", "").strip()
    if env_bind_key:
        return env_bind_key

    raise ValueError("bindKey is required via HERMES_BINDING_KEY environment variable")


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage daily ASIN list by HERMES_BINDING_KEY")
    parser.add_argument("--action", required=True, choices=["list", "add", "remove"], help="Operation type")
    parser.add_argument("--asin", action="append", default=[], help="ASIN or Amazon URL, repeatable")
    args = parser.parse_args()

    config = load_app_config()
    try:
        bind_key = _resolve_bind_key()
    except ValueError as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False, indent=2))
        return 1

    if args.action == "list":
        bindings = load_daily_bindings(config.base_dir)
        items = bindings.get(bind_key, [])
        print(json.dumps({"status": "success", "bindKey": bind_key, "items": items, "count": len(items)}, ensure_ascii=False, indent=2))
        return 0

    if not args.asin:
        print(json.dumps({"status": "error", "message": "--asin is required for add/remove", "bindKey": bind_key}, ensure_ascii=False, indent=2))
        return 1

    if args.action == "add":
        items = add_daily_asins(config.base_dir, bind_key, args.asin)
    else:
        items = remove_daily_asins(config.base_dir, bind_key, args.asin)

    print(json.dumps({"status": "success", "bindKey": bind_key, "items": items, "count": len(items)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
