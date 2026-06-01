from __future__ import annotations

import argparse
import json

from amz_sif_crawler.daily_bindings import add_daily_asins, load_daily_bindings, remove_daily_asins
from amz_sif_crawler.runtime.config import load_app_config


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage daily ASIN list by bindKey")
    parser.add_argument("--bindkey", required=True, help="bindKey to manage")
    parser.add_argument("--action", required=True, choices=["list", "add", "remove"], help="Operation type")
    parser.add_argument("--asin", action="append", default=[], help="ASIN or Amazon URL, repeatable")
    args = parser.parse_args()

    config = load_app_config()
    bind_key = args.bindkey.strip()

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
