"""Async ElevenLabs API client used by the переозвучь feature."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import random
import secrets
import time
from urllib.parse import quote

import httpx


logger = logging.getLogger(__name__)

ELEVENLABS_BASE_URL = "https://api.elevenlabs.io"
VOICE_CACHE_TTL_SECONDS = 10 * 60
VOICE_PAGE_SIZE = 100
MAX_VOICE_PAGES = 10
TEMP_VOICE_PREFIX = "upupa_tmp_"
VOICE_CHANGER_MODEL = "eleven_multilingual_sts_v2"
VOICE_DESIGN_MODEL = "eleven_multilingual_ttv_v2"
VOICE_OUTPUT_FORMAT = "mp3_44100_128"

# Voice Design requires preview text of at least 100 chars when text is supplied.
# A fixed short sample keeps preview-credit use bounded and predictable.
VOICE_DESIGN_PREVIEW_TEXT = (
    "Это короткий тестовый текст для создания голоса Упупы. "
    "Он нужен только для настройки тембра, манеры речи и выразительности, "
    "после чего голос будет сразу удалён."
)


class ElevenLabsError(RuntimeError):
    """Base error for ElevenLabs failures with safe provider metadata."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        provider_code: str | None = None,
        provider_status: str | None = None,
        request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.provider_code = provider_code
        self.provider_status = provider_status
        self.request_id = request_id


class ElevenLabsConfigurationError(ElevenLabsError):
    """The service is not configured."""


class ElevenLabsAuthenticationError(ElevenLabsError):
    """The API key is invalid or missing."""


class ElevenLabsAuthorizationError(ElevenLabsError):
    """The authenticated account/key is not allowed to perform an operation."""


class ElevenLabsQuotaError(ElevenLabsError):
    """The account hit a quota/rate limit."""


class ElevenLabsTimeoutError(ElevenLabsError):
    """The remote request timed out."""


class ElevenLabsTemporaryError(ElevenLabsError):
    """The remote service is temporarily unavailable."""


class ElevenLabsInvalidResponseError(ElevenLabsError):
    """The remote service returned an unusable response."""


class ElevenLabsNoVoicesError(ElevenLabsError):
    """No usable voices are available to the account."""


@dataclass(frozen=True)
class ElevenLabsVoice:
    voice_id: str
    name: str


class ElevenLabsClient:
    """Small async client with cached account voices and no automatic generation retries."""

    def __init__(
        self,
        api_key: str,
        *,
        timeout_seconds: float = 60.0,
        http_client: httpx.AsyncClient | None = None,
        voice_cache_ttl_seconds: float = VOICE_CACHE_TTL_SECONDS,
    ) -> None:
        normalized_key = str(api_key or "").strip()
        if not normalized_key:
            raise ElevenLabsConfigurationError("ELEVENLABS_API_KEY is not configured")
        self.api_key = normalized_key
        self.timeout_seconds = float(timeout_seconds)
        self._http_client = http_client
        self.voice_cache_ttl_seconds = float(voice_cache_ttl_seconds)
        self._voices_cache: list[ElevenLabsVoice] | None = None
        self._voices_cache_at = 0.0
        self._last_random_voice_id: str | None = None
        self._stale_cleanup_done = False

    @staticmethod
    def _error_metadata(
        response: httpx.Response,
    ) -> tuple[str | None, str | None, str | None]:
        """Extract non-sensitive provider error identifiers without logging messages."""
        try:
            payload = response.json()
        except ValueError:
            return None, None, None
        if not isinstance(payload, dict):
            return None, None, None

        detail = payload.get("detail")
        provider_code: str | None = None
        provider_status: str | None = None
        request_id: str | None = None

        if isinstance(detail, dict):
            provider_code = str(detail.get("code") or "").strip() or None
            provider_status = str(detail.get("status") or "").strip() or None
            request_id = str(detail.get("request_id") or "").strip() or None

        if request_id is None:
            request_id = str(payload.get("request_id") or "").strip() or None

        return provider_code, provider_status, request_id

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        started = time.monotonic()
        url = f"{ELEVENLABS_BASE_URL}{path}"
        headers = dict(kwargs.pop("headers", {}) or {})
        headers["xi-api-key"] = self.api_key
        owns_client = self._http_client is None
        client = self._http_client or httpx.AsyncClient()

        try:
            try:
                response = await client.request(
                    method,
                    url,
                    headers=headers,
                    timeout=self.timeout_seconds,
                    **kwargs,
                )
            except httpx.TimeoutException as exc:
                duration_ms = round((time.monotonic() - started) * 1000)
                logger.warning(
                    "[elevenlabs] timeout method=%s path=%s duration_ms=%s",
                    method,
                    path,
                    duration_ms,
                )
                raise ElevenLabsTimeoutError("ElevenLabs request timed out") from exc
            except httpx.RequestError as exc:
                duration_ms = round((time.monotonic() - started) * 1000)
                logger.warning(
                    "[elevenlabs] transport_error method=%s path=%s duration_ms=%s error=%s",
                    method,
                    path,
                    duration_ms,
                    type(exc).__name__,
                )
                raise ElevenLabsTemporaryError("ElevenLabs transport error") from exc

            duration_ms = round((time.monotonic() - started) * 1000)
            logger.info(
                "[elevenlabs] method=%s path=%s status=%s duration_ms=%s response_bytes=%s",
                method,
                path,
                response.status_code,
                duration_ms,
                len(response.content or b""),
            )

            provider_code = None
            provider_status = None
            request_id = None
            if response.status_code >= 400:
                (
                    provider_code,
                    provider_status,
                    request_id,
                ) = self._error_metadata(response)
                logger.warning(
                    "[elevenlabs] api_error method=%s path=%s status=%s provider_code=%s provider_status=%s request_id=%s",
                    method,
                    path,
                    response.status_code,
                    provider_code,
                    provider_status,
                    request_id,
                )

            error_kwargs = {
                "status_code": response.status_code,
                "provider_code": provider_code,
                "provider_status": provider_status,
                "request_id": request_id,
            }
            if response.status_code == 401:
                raise ElevenLabsAuthenticationError(
                    "ElevenLabs authentication failed",
                    **error_kwargs,
                )
            if response.status_code == 403:
                raise ElevenLabsAuthorizationError(
                    "ElevenLabs authorization failed",
                    **error_kwargs,
                )
            if response.status_code in (402, 429):
                raise ElevenLabsQuotaError(
                    "ElevenLabs quota/rate limit reached",
                    **error_kwargs,
                )
            if 500 <= response.status_code <= 599:
                raise ElevenLabsTemporaryError(
                    f"ElevenLabs temporary HTTP {response.status_code}",
                    **error_kwargs,
                )
            if response.status_code >= 400:
                raise ElevenLabsError(
                    f"ElevenLabs HTTP {response.status_code}",
                    **error_kwargs,
                )
            return response
        finally:
            if owns_client:
                await client.aclose()

    @staticmethod
    def _json_object(response: httpx.Response) -> dict:
        try:
            payload = response.json()
        except ValueError as exc:
            raise ElevenLabsInvalidResponseError(
                "ElevenLabs returned invalid JSON"
            ) from exc
        if not isinstance(payload, dict):
            raise ElevenLabsInvalidResponseError(
                "ElevenLabs returned an unexpected JSON payload"
            )
        return payload

    def invalidate_voice_cache(self) -> None:
        self._voices_cache = None
        self._voices_cache_at = 0.0

    async def get_available_voices(
        self,
        *,
        force_refresh: bool = False,
    ) -> list[ElevenLabsVoice]:
        now = time.monotonic()
        if (
            not force_refresh
            and self._voices_cache is not None
            and now - self._voices_cache_at < self.voice_cache_ttl_seconds
        ):
            return list(self._voices_cache)

        voices: list[ElevenLabsVoice] = []
        next_page_token: str | None = None

        for _page in range(MAX_VOICE_PAGES):
            params: dict[str, str | int] = {
                "page_size": VOICE_PAGE_SIZE,
                "include_total_count": "false",
            }
            if next_page_token:
                params["next_page_token"] = next_page_token

            response = await self._request("GET", "/v2/voices", params=params)
            payload = self._json_object(response)
            page_voices = payload.get("voices")
            if not isinstance(page_voices, list):
                raise ElevenLabsInvalidResponseError(
                    "ElevenLabs voices response has no voices list"
                )

            for item in page_voices:
                if not isinstance(item, dict):
                    continue
                voice_id = str(item.get("voice_id") or "").strip()
                name = str(item.get("name") or "").strip()
                if not voice_id or name.startswith(TEMP_VOICE_PREFIX):
                    continue
                voices.append(ElevenLabsVoice(voice_id=voice_id, name=name))

            if not payload.get("has_more"):
                break
            next_page_token = str(payload.get("next_page_token") or "").strip() or None
            if not next_page_token:
                break

        deduplicated: dict[str, ElevenLabsVoice] = {
            voice.voice_id: voice for voice in voices
        }
        self._voices_cache = list(deduplicated.values())
        self._voices_cache_at = now
        return list(self._voices_cache)

    async def choose_random_voice(self) -> ElevenLabsVoice:
        voices = await self.get_available_voices()
        if not voices:
            raise ElevenLabsNoVoicesError(
                "No ElevenLabs voices are available for this account"
            )

        candidates = [
            voice
            for voice in voices
            if voice.voice_id != self._last_random_voice_id
        ]
        if not candidates:
            candidates = voices

        selected = random.choice(candidates)
        self._last_random_voice_id = selected.voice_id
        return selected

    async def voice_change(self, voice_id: str, audio_bytes: bytes) -> bytes:
        if not audio_bytes:
            raise ElevenLabsInvalidResponseError("Cannot transform empty audio")

        response = await self._request(
            "POST",
            f"/v1/speech-to-speech/{quote(voice_id, safe='')}",
            params={"output_format": VOICE_OUTPUT_FORMAT},
            data={"model_id": VOICE_CHANGER_MODEL},
            files={"audio": ("voice.ogg", audio_bytes, "audio/ogg")},
        )
        result = response.content
        if not result:
            raise ElevenLabsInvalidResponseError(
                "ElevenLabs voice changer returned empty audio"
            )
        content_type = (response.headers.get("content-type") or "").lower()
        if "json" in content_type:
            raise ElevenLabsInvalidResponseError(
                "ElevenLabs voice changer returned JSON instead of audio"
            )
        return result

    async def design_voice(self, voice_description: str) -> list[str]:
        response = await self._request(
            "POST",
            "/v1/text-to-voice/design",
            params={"output_format": VOICE_OUTPUT_FORMAT},
            json={
                "voice_description": voice_description,
                "model_id": VOICE_DESIGN_MODEL,
                "text": VOICE_DESIGN_PREVIEW_TEXT,
                "auto_generate_text": False,
            },
        )
        payload = self._json_object(response)
        previews = payload.get("previews")
        if not isinstance(previews, list):
            raise ElevenLabsInvalidResponseError(
                "ElevenLabs Voice Design returned no previews"
            )

        generated_ids = [
            str(item.get("generated_voice_id") or "").strip()
            for item in previews
            if isinstance(item, dict)
        ]
        generated_ids = [voice_id for voice_id in generated_ids if voice_id]
        if not generated_ids:
            raise ElevenLabsInvalidResponseError(
                "ElevenLabs Voice Design returned no generated voice ids"
            )
        return generated_ids

    async def create_temporary_voice(
        self,
        *,
        generated_voice_id: str,
        voice_description: str,
        voice_name: str | None = None,
    ) -> str:
        resolved_name = voice_name or (
            f"{TEMP_VOICE_PREFIX}{int(time.time())}_{secrets.token_hex(3)}"
        )
        response = await self._request(
            "POST",
            "/v1/text-to-voice",
            json={
                "voice_name": resolved_name,
                "voice_description": voice_description,
                "generated_voice_id": generated_voice_id,
            },
        )
        payload = self._json_object(response)
        voice_id = str(payload.get("voice_id") or "").strip()
        if not voice_id:
            raise ElevenLabsInvalidResponseError(
                "ElevenLabs did not return a created voice id"
            )
        self.invalidate_voice_cache()
        return voice_id

    async def delete_voice(self, voice_id: str) -> None:
        try:
            await self._request(
                "DELETE",
                f"/v1/voices/{quote(voice_id, safe='')}",
            )
        finally:
            self.invalidate_voice_cache()

    async def ensure_stale_temporary_voice_cleanup(self) -> None:
        """Best-effort cleanup once per process before the feature starts work."""
        if self._stale_cleanup_done:
            return

        try:
            response = await self._request(
                "GET",
                "/v2/voices",
                params={
                    "page_size": VOICE_PAGE_SIZE,
                    "include_total_count": "false",
                    "search": TEMP_VOICE_PREFIX,
                },
            )
            payload = self._json_object(response)
            raw_voices = payload.get("voices")
            if not isinstance(raw_voices, list):
                raise ElevenLabsInvalidResponseError(
                    "ElevenLabs cleanup response has no voices list"
                )

            stale_ids = []
            for item in raw_voices:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "")
                voice_id = str(item.get("voice_id") or "").strip()
                if name.startswith(TEMP_VOICE_PREFIX) and voice_id:
                    stale_ids.append(voice_id)

            failures = 0
            for voice_id in stale_ids:
                try:
                    await self.delete_voice(voice_id)
                    logger.info(
                        "[elevenlabs] stale temporary voice deleted voice_id=%s",
                        voice_id,
                    )
                except ElevenLabsError as exc:
                    failures += 1
                    logger.warning(
                        "[elevenlabs] stale temporary voice cleanup failed voice_id=%s error=%s",
                        voice_id,
                        type(exc).__name__,
                    )

            self._stale_cleanup_done = failures == 0
        except ElevenLabsError as exc:
            # Cleanup must never make the whole feature or bot unavailable.
            logger.warning(
                "[elevenlabs] stale temporary voice cleanup skipped error=%s",
                type(exc).__name__,
            )


__all__ = [
    "ELEVENLABS_BASE_URL",
    "TEMP_VOICE_PREFIX",
    "ElevenLabsAuthenticationError",
    "ElevenLabsAuthorizationError",
    "ElevenLabsClient",
    "ElevenLabsConfigurationError",
    "ElevenLabsError",
    "ElevenLabsInvalidResponseError",
    "ElevenLabsNoVoicesError",
    "ElevenLabsQuotaError",
    "ElevenLabsTemporaryError",
    "ElevenLabsTimeoutError",
    "ElevenLabsVoice",
]
