import argparse
import json
import sys
from typing import Optional

import httpx


def ok(label: str, detail: str) -> None:
    print(f"[PASS] {label}: {detail}")


def fail(label: str, detail: str) -> None:
    print(f"[FAIL] {label}: {detail}")


def check_health(client: httpx.Client, base_url: str) -> bool:
    try:
        resp = client.get(f"{base_url.rstrip('/')}/")
        resp.raise_for_status()
        payload = resp.json()
        ok("health", json.dumps(payload, ensure_ascii=False))
        return True
    except Exception as exc:
        fail("health", str(exc))
        return False


def check_sse(client: httpx.Client, sse_url: str) -> bool:
    try:
        with client.stream("GET", sse_url, headers={"Accept": "text/event-stream"}) as resp:
            if resp.status_code != 200:
                fail("sse", f"unexpected status={resp.status_code}")
                return False
            content_type = resp.headers.get("content-type", "")
            if "text/event-stream" not in content_type:
                fail("sse", f"unexpected content-type={content_type}")
                return False
            ok("sse", f"status=200 content-type={content_type}")
            return True
    except Exception as exc:
        fail("sse", str(exc))
        return False


def check_crawl(client: httpx.Client, base_url: str, sample_url: str, timeout: float) -> bool:
    try:
        resp = client.post(
            f"{base_url.rstrip('/')}/crawl",
            json={"urls": [sample_url]},
            timeout=timeout,
        )
        resp.raise_for_status()
        payload = resp.json()
        summary = {
            "status": payload.get("status"),
            "count": payload.get("count"),
            "keys": sorted(payload.keys()),
        }
        ok("crawl", json.dumps(summary, ensure_ascii=False))
        return True
    except Exception as exc:
        fail("crawl", str(exc))
        return False


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Validate local MCP gateway/worker endpoints")
    parser.add_argument("--base-url", default="http://localhost:8888", help="HTTP base URL, default: http://localhost:8888")
    parser.add_argument("--sse-path", default="/sse", help="SSE path, default: /sse")
    parser.add_argument("--timeout", type=float, default=10.0, help="HTTP timeout in seconds")
    parser.add_argument("--crawl-timeout", type=float, default=120.0, help="POST /crawl timeout in seconds")
    parser.add_argument("--probe-crawl", action="store_true", help="Also call POST /crawl")
    parser.add_argument(
        "--sample-url",
        default="https://www.amazon.com/dp/B0CDX5XGLK",
        help="Sample URL used with --probe-crawl",
    )
    args = parser.parse_args(argv)

    sse_url = f"{args.base_url.rstrip('/')}{args.sse_path}"
    print(f"Validating MCP service: base={args.base_url} sse={sse_url}")

    with httpx.Client(timeout=args.timeout) as client:
        health_ok = check_health(client, args.base_url)
        sse_ok = check_sse(client, sse_url)
        crawl_ok = True
        if args.probe_crawl:
            crawl_ok = check_crawl(client, args.base_url, args.sample_url, args.crawl_timeout)

    passed = health_ok and sse_ok and crawl_ok
    print("Validation result: PASS" if passed else "Validation result: FAIL")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
