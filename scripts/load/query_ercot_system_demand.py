"""Query one page of ERCOT 15-minute system-wide demand or its archive listing."""

from __future__ import annotations

import argparse
from datetime import date
from getpass import getpass
import json
import os
from pathlib import Path
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from dotenv import load_dotenv

TOKEN_URL = "https://ercotb2c.b2clogin.com/ercotb2c.onmicrosoft.com/B2C_1_PUBAPI-ROPC-FLOW/oauth2/v2.0/token"
DEMAND_URL = "https://api.ercot.com/api/public-reports/np6-235-cd/system_wide_demand"
ARCHIVE_URL = "https://api.ercot.com/api/public-reports/archive/np6-235-cd"
CLIENT_ID = "fec253ea-0d06-4272-a5e6-b478baeecd70"

def request_json(request: Request) -> dict:
    try:
        with urlopen(request, timeout=30) as response:
            payload = json.load(response)
    except HTTPError as error:
        raise RuntimeError(f"ERCOT request failed with HTTP {error.code}.") from None
    except (URLError, TimeoutError, OSError):
        raise RuntimeError("ERCOT request failed due to a network error.") from None
    except (ValueError, UnicodeDecodeError):
        raise RuntimeError("ERCOT returned an invalid JSON response.") from None
    if not isinstance(payload, dict):
        raise RuntimeError("ERCOT returned an unexpected JSON response.")
    return payload


def query_demand(
    start_date: date,
    end_date: date,
    page: int,
    size: int,
    *,
    endpoint: str = "archive",
) -> dict:
    load_dotenv(Path.home() / ".env")

    subscription_key = os.getenv("API_KEY_ERCOT_PRIMARY") or os.getenv("ERCOT_API_SUBSCRIPTION_KEY")
    if not subscription_key:
        raise ValueError("Set API_KEY_ERCOT_PRIMARY or ERCOT_API_SUBSCRIPTION_KEY in ~/.env or your environment.")
    username = os.getenv("USERNAME_ERCOT") or os.getenv("ERCOT_API_USERNAME")
    if not username:
        print("ERCOT username: ", end="", file=sys.stderr, flush=True)
        username = input().strip()
    password = os.getenv("PASSWORD_ERCOT") or os.getenv("ERCOT_API_PASSWORD") or getpass("ERCOT password: ")
    if not username or not password:
        raise ValueError("An ERCOT username and password are required.")

    token_data = urlencode({
        "username": username,
        "password": password,
        "grant_type": "password",
        "scope": f"openid {CLIENT_ID} offline_access",
        "client_id": CLIENT_ID,
        "response_type": "id_token",
    }).encode("utf-8")

    token_response = request_json(Request(TOKEN_URL, data=token_data, method="POST"))
    token = token_response.get("id_token")

    if not isinstance(token, str) or not token:
        raise RuntimeError("ERCOT authentication returned no ID token.")

    if endpoint == "archive":
        base_url = ARCHIVE_URL
        parameters = urlencode({
            "postDatetimeFrom": f"{start_date.isoformat()}T00:00:00",
            "postDatetimeTo": f"{end_date.isoformat()}T23:59:59",
            "page": page,
            "size": size,
        })
    elif endpoint == "data":
        base_url = DEMAND_URL
        parameters = urlencode({
            "deliveryDateFrom": start_date.isoformat(),
            "deliveryDateTo": end_date.isoformat(),
            "page": page,
            "size": size,
        })
    else:
        raise ValueError(f"Unsupported endpoint: {endpoint}")

    request = Request(
        f"{base_url}?{parameters}",
        headers={"Authorization": f"Bearer {token}", "Ocp-Apim-Subscription-Key": subscription_key},
    )
    return request_json(request)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog=(
            "Credentials are loaded from ~/.env: API_KEY_ERCOT_PRIMARY (or ERCOT_API_SUBSCRIPTION_KEY) is required; "
            "USERNAME_ERCOT / PASSWORD_ERCOT (or ERCOT_API_USERNAME / ERCOT_API_PASSWORD) are optional and prompted for if absent. "
            "Output is one JSON response page; inspect _meta for pagination and empty results."
        ),
    )
    parser.add_argument("--start-date", required=True, type=date.fromisoformat, help="First date (YYYY-MM-DD).")
    parser.add_argument("--end-date", type=date.fromisoformat, help="Last date (YYYY-MM-DD); defaults to start date.")
    parser.add_argument(
        "--endpoint",
        choices=("archive", "data"),
        default="archive",
        help="API endpoint to query: archive listing (default) or row-level data.",
    )
    parser.add_argument("--page", type=int, default=1, help="Page to retrieve (default: 1).")
    parser.add_argument("--size", type=int, default=100, help="Maximum requested rows (default: 100).")
    args = parser.parse_args()
    args.end_date = args.end_date or args.start_date
    if args.end_date < args.start_date:
        parser.error("--end-date must not precede --start-date")
    if args.page < 1 or not 1 <= args.size <= 1000:
        parser.error("--page must be positive and --size must be between 1 and 1000")
    return args


def main() -> int:
    args = parse_args()
    try:
        result = query_demand(args.start_date, args.end_date, args.page, args.size, endpoint=args.endpoint)
    except (RuntimeError, ValueError, EOFError, KeyboardInterrupt) as error:
        print(f"Query failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())