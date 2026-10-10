"""Live probe of Gemini text models available to the production keys.

Run from the repo root on the server:
    python3 scripts/probe_gemini_models.py               # all flash/lite/pro text models
    python3 scripts/probe_gemini_models.py --keys 3 --rounds 3
    python3 scripts/probe_gemini_models.py --models gemini-3.6-flash gemini-3.8-flash

Each (model, key, round) costs one free-tier request for that model only.
Keys are shown by pool index, never by value.
"""
from __future__ import annotations

import argparse
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from google import genai  # noqa: E402
from google.genai import types  # noqa: E402

from core.settings import GEMINI_KEYS_POOL, MODEL_QUEUE_DEFAULT  # noqa: E402
from infrastructure.ai.gemini_quota import is_daily_quota_error  # noqa: E402

SYSTEM = (
    "Ты ведущий настольной ролевой игры. Отвечай по-русски. В конце ответа обязательно "
    "поставь тег [ACTION:CHECK;ABILITY:DEX;DC:12;TARGETS:1] на отдельной строке."
)
PROMPT = (
    "Игрок Маша пытается незаметно проскользнуть мимо стражника у северных ворот, "
    "пока тот спорит с купцом. Опиши сцену в 3-4 предложениях и запроси проверку."
)
SKIP_MARKERS = ("tts", "image", "embedding", "audio", "live", "robotics", "computer-use", "native", "aqa", "veo", "imagen")


def thinking_for(model: str):
    if model.startswith("gemini-2.5-flash"):
        return types.ThinkingConfig(thinking_budget=0)
    if model.startswith("gemini-3"):
        return types.ThinkingConfig(thinking_level="low")
    return None


def discover(client) -> list[str]:
    names = []
    for row in client.models.list():
        name = str(row.name).removeprefix("models/")
        actions = getattr(row, "supported_actions", None) or []
        if actions and "generateContent" not in actions:
            continue
        if not name.startswith("gemini-") or any(marker in name for marker in SKIP_MARKERS):
            continue
        names.append(name)
    return sorted(set(names))


def probe(api_key: str, model: str) -> dict:
    client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=30_000))
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM, temperature=0.8, max_output_tokens=1800,
        thinking_config=thinking_for(model),
    )
    started = time.monotonic()
    try:
        response = client.models.generate_content(model=model, contents=PROMPT, config=config)
    except Exception as error:  # noqa: BLE001 - report every provider failure
        code = getattr(error, "code", None) or getattr(error, "status_code", None)
        kind = "429-day" if is_daily_quota_error(error) else (f"{code}" if code else type(error).__name__)
        return {"ok": False, "kind": kind, "ms": int((time.monotonic() - started) * 1000), "error": str(error)[:160]}
    ms = int((time.monotonic() - started) * 1000)
    text = (getattr(response, "text", None) or "").strip()
    finish = ""
    for candidate in getattr(response, "candidates", None) or []:
        finish = str(getattr(candidate, "finish_reason", "") or "").rsplit(".", 1)[-1]
    usage = getattr(response, "usage_metadata", None)
    thoughts = getattr(usage, "thoughts_token_count", None) or 0
    if not text:
        return {"ok": False, "kind": "empty", "ms": ms, "error": f"finish={finish}"}
    if "MAX_TOKENS" in finish:
        return {"ok": False, "kind": "truncated", "ms": ms, "error": f"len={len(text)} thoughts={thoughts}"}
    return {"ok": True, "kind": "ok", "ms": ms, "tag": "[ACTION:" in text, "len": len(text), "thoughts": thoughts}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keys", type=int, default=2, help="how many pool keys to try per model")
    parser.add_argument("--rounds", type=int, default=2, help="requests per key per model")
    parser.add_argument("--models", nargs="*", help="explicit model list (default: discovered)")
    args = parser.parse_args()

    keys = [key for key in GEMINI_KEYS_POOL if key]
    if not keys:
        print("GEMINI_KEYS_POOL is empty")
        return 1
    models = args.models or discover(genai.Client(api_key=keys[0]))
    print(f"keys in pool: {len(keys)}; probing {len(models)} models x {min(args.keys, len(keys))} keys x {args.rounds} rounds")
    print(f"current MODEL_QUEUE_DEFAULT: {MODEL_QUEUE_DEFAULT}\n")

    rows = []
    for model in models:
        results = []
        for key_idx in range(min(args.keys, len(keys))):
            for _ in range(args.rounds):
                result = probe(keys[key_idx], model)
                result["key_idx"] = key_idx
                results.append(result)
                time.sleep(1.0)
        ok = [row for row in results if row["ok"]]
        failures = {}
        for row in results:
            if not row["ok"]:
                failures[row["kind"]] = failures.get(row["kind"], 0) + 1
        rows.append({
            "model": model,
            "ok": f"{len(ok)}/{len(results)}",
            "p50_ms": int(statistics.median(row["ms"] for row in ok)) if ok else "-",
            "max_ms": max(row["ms"] for row in ok) if ok else "-",
            "tag": f"{sum(1 for row in ok if row.get('tag'))}/{len(ok)}" if ok else "-",
            "thoughts": max((row.get("thoughts") or 0) for row in ok) if ok else "-",
            "failures": ", ".join(f"{kind}x{count}" for kind, count in failures.items()) or "",
            "sample_error": next((f"key{row['key_idx']}: {row['error']}" for row in results if not row["ok"]), ""),
        })
        last = rows[-1]
        print(f"{model:36} ok={last['ok']:5} p50={last['p50_ms']!s:>6}ms max={last['max_ms']!s:>6}ms "
              f"tag={last['tag']:5} thoughts={last['thoughts']!s:>5} {last['failures']}")

    print("\nfailure samples:")
    for row in rows:
        if row["sample_error"]:
            print(f"- {row['model']}: {row['sample_error']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
