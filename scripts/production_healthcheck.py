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


def check_crocodile_mini_app(
    *,
    timeout: float,
    base_url: str = "http://127.0.0.1:8080",
    opener: Callable = urlopen,
) -> dict:
    """Verify the local Mini App page and an Engine.IO v4 polling handshake."""
    base_url = str(base_url or "").rstrip("/")
    if not base_url:
        raise HealthCheckError("Crocodile Mini App base URL is not configured")

    game_request = Request(
        f"{base_url}/game",
        headers={"User-Agent": "upupa-deploy-healthcheck/1"},
    )
    try:
        with opener(game_request, timeout=timeout) as response:
            html = response.read().decode("utf-8")
    except (HTTPError, URLError, OSError):
        raise HealthCheckError("Crocodile Mini App is unreachable") from None
    except UnicodeDecodeError:
        raise HealthCheckError("Crocodile Mini App returned invalid HTML") from None

    required_markers = (
        "<title>Crocodile</title>",
        'id="canvas"',
        "telegram-web-app.js",
        "socket.io.min.js",
    )
    if any(marker not in html for marker in required_markers):
        raise HealthCheckError("Crocodile Mini App returned invalid HTML")

    handshake_request = Request(
        f"{base_url}/socket.io/?EIO=4&transport=polling",
        headers={"User-Agent": "upupa-deploy-healthcheck/1"},
    )
    try:
        with opener(handshake_request, timeout=timeout) as response:
            packet = response.read().decode("utf-8")
    except (HTTPError, URLError, OSError, UnicodeDecodeError):
        raise HealthCheckError(
            "Crocodile Socket.IO handshake is unavailable or invalid"
        ) from None

    if not packet.startswith("0"):
        raise HealthCheckError("Crocodile Socket.IO handshake is unavailable or invalid")
    try:
        handshake = json.loads(packet[1:])
    except json.JSONDecodeError:
        raise HealthCheckError(
            "Crocodile Socket.IO handshake is unavailable or invalid"
        ) from None

    if not isinstance(handshake, dict) or not handshake.get("sid"):
        raise HealthCheckError("Crocodile Socket.IO handshake is unavailable or invalid")
    for field in ("pingInterval", "pingTimeout"):
        if type(handshake.get(field)) is not int or handshake[field] <= 0:
            raise HealthCheckError(
                "Crocodile Socket.IO handshake is unavailable or invalid"
            )

    return handshake


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app-dir", required=True, type=Path)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--port", type=int, default=int(os.getenv("UPUPA_HEALTHCHECK_PORT", "8766")))
    parser.add_argument("--expected-pid", type=int, default=os.getenv("UPUPA_EXPECTED_PID"))
    parser.add_argument(
        "--crocodile-base",
        default=os.getenv("UPUPA_CROCODILE_BASE", "http://127.0.0.1:8080"),
        help=argparse.SUPPRESS,
    )
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
        check_crocodile_mini_app(
            timeout=args.timeout,
            base_url=args.crocodile_base,
        )
    except HealthCheckError as error:
        print(f"healthcheck failed: {error}", file=sys.stderr)
        return 1

    print(
        f"healthcheck ok: process_pid={process['pid']} polling=ok databases=ok tasks=ok telegram=getMe "
        f"bot_id={result['id']} username={result.get('username', 'unknown')} mini_app=game+socketio"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
