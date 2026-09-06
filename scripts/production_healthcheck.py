#!/usr/bin/env python3
"""Functional production health check for the Telegram bot."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class HealthCheckError(RuntimeError):
    pass


def load_api_token(app_dir: Path) -> str:
    sys.path.insert(0, str(app_dir.resolve()))
    try:
        from core.settings import API_TOKEN, validate_required_settings

        validate_required_settings()
        return str(API_TOKEN)
    finally:
        sys.path.pop(0)


def check_telegram(
    token: str,
    *,
    timeout: float,
    api_base: str = "https://api.telegram.org",
    opener: Callable = urlopen,
) -> dict:
    request = Request(
        f"{api_base.rstrip('/')}/bot{token}/getMe",
        headers={"User-Agent": "upupa-deploy-healthcheck/1"},
    )
    try:
        with opener(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        raise HealthCheckError(
            f"Telegram getMe returned HTTP {error.code}"
        ) from None
    except URLError:
        raise HealthCheckError("Telegram getMe is unreachable") from None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise HealthCheckError("Telegram getMe returned an invalid response") from None

    if not isinstance(payload, dict):
        raise HealthCheckError("Telegram getMe returned an invalid response")
    result = payload.get("result")
    if payload.get("ok") is not True or not isinstance(result, dict):
        raise HealthCheckError("Telegram getMe reported an unhealthy bot")
    if not result.get("id"):
        raise HealthCheckError("Telegram getMe response has no bot id")
    return result


def check_process(*, timeout: float, port: int = 8766, expected_pid: int | None = None,
                  opener: Callable = urlopen) -> dict:
    request = Request(f"http://127.0.0.1:{port}/ready")
    try:
        with opener(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise HealthCheckError("Bot process readiness is unavailable or unhealthy") from None
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise HealthCheckError("Bot process is not ready")
    checks = payload.get("checks")
    if not isinstance(checks, dict) or any(
        checks.get(name) is not True for name in ("polling", "databases", "background_tasks")
    ):
        raise HealthCheckError("Bot process readiness checks failed")
    if type(payload.get("pid")) is not int or payload["pid"] <= 0:
        raise HealthCheckError("Bot process returned an invalid PID")
    if expected_pid is not None and (expected_pid <= 0 or payload["pid"] != expected_pid):
        raise HealthCheckError("Readiness PID does not match the systemd service")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app-dir", required=True, type=Path)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--port", type=int, default=int(os.getenv("UPUPA_HEALTHCHECK_PORT", "8766")))
    parser.add_argument("--expected-pid", type=int, default=os.getenv("UPUPA_EXPECTED_PID"))
    parser.add_argument(
        "--api-base",
        default="https://api.telegram.org",
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args()

    try:
        process = check_process(timeout=args.timeout, port=args.port, expected_pid=args.expected_pid)
        token = load_api_token(args.app_dir)
        result = check_telegram(
            token,
            timeout=args.timeout,
            api_base=args.api_base,
        )
    except HealthCheckError as error:
        print(f"healthcheck failed: {error}", file=sys.stderr)
        return 1

    print(
        f"healthcheck ok: process_pid={process['pid']} polling=ok databases=ok tasks=ok telegram=getMe "
        f"bot_id={result['id']} username={result.get('username', 'unknown')}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
