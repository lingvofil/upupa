"""Устойчивый клиент FusionBrain/Kandinsky.

API оставляет совместимый интерфейс ``get_pipeline/generate/check`` для
``AI.picgeneration``. В отличие от старого клиента:
- запрашивает только TEXT2IMAGE pipeline;
- выбирает только ACTIVE pipeline и проверяет availability;
- учитывает model_status и status_time;
- умеет освежать устаревший pipeline и повторять запуск один раз;
- ждёт генерацию до двух минут вместо жёстких ~36 секунд;
- пишет диагностические ответы API в лог без ключей.
"""

from __future__ import annotations

import base64
import json
import logging
import time
from typing import Optional, Tuple

import requests


class FusionBrainAPI:
    PIPELINES_PATH = "key/api/v1/pipelines"
    RUN_PATH = "key/api/v1/pipeline/run"
    STATUS_PATH = "key/api/v1/pipeline/status/{uuid}"
    AVAILABILITY_PATH = "key/api/v1/pipeline/{pipeline_id}/availability"

    def __init__(
        self,
        url: str,
        api_key: Optional[str],
        secret_key: Optional[str],
        *,
        request_timeout: int = 15,
        poll_interval: float = 2.0,
        max_wait_seconds: int = 120,
    ) -> None:
        self.URL = url.rstrip("/") + "/"
        self.headers = {
            "X-Key": f"Key {api_key}" if api_key else "",
            "X-Secret": f"Secret {secret_key}" if secret_key else "",
        }
        self.request_timeout = request_timeout
        self.poll_interval = poll_interval
        self.max_wait_seconds = max_wait_seconds
        self._pipeline_id: Optional[str] = None
        self._initial_delays: dict[str, float] = {}

    @property
    def configured(self) -> bool:
        return bool(self.headers["X-Key"].removeprefix("Key ") and self.headers["X-Secret"].removeprefix("Secret "))

    @staticmethod
    def _safe_body(response: requests.Response, limit: int = 500) -> str:
        try:
            body = response.text
        except Exception:
            body = "<unreadable body>"
        return body[:limit]

    def _get_json(self, response: requests.Response) -> Optional[dict | list]:
        try:
            return response.json()
        except Exception:
            logging.warning(
                "Kandinsky invalid JSON HTTP=%s body=%s",
                response.status_code,
                self._safe_body(response),
            )
            return None

    def _availability(self, pipeline_id: str) -> Optional[str]:
        try:
            response = requests.get(
                self.URL + self.AVAILABILITY_PATH.format(pipeline_id=pipeline_id),
                headers=self.headers,
                timeout=self.request_timeout,
            )
            if response.status_code != 200:
                logging.warning(
                    "Kandinsky availability pipeline=%s HTTP=%s body=%s",
                    pipeline_id,
                    response.status_code,
                    self._safe_body(response),
                )
                return None
            data = self._get_json(response)
            if not isinstance(data, dict):
                return None
            return data.get("status")
        except Exception as exc:
            logging.warning("Kandinsky availability pipeline=%s error=%s", pipeline_id, exc)
            return None

    def get_pipeline(self, force_refresh: bool = False) -> Optional[str]:
        """Возвращает доступный ACTIVE pipeline типа TEXT2IMAGE."""
        if not self.configured:
            logging.warning("Kandinsky credentials are not configured")
            return None

        if self._pipeline_id and not force_refresh:
            status = self._availability(self._pipeline_id)
            if status == "ACTIVE":
                return self._pipeline_id
            logging.warning(
                "Kandinsky cached pipeline=%s is unavailable: %s; refreshing",
                self._pipeline_id,
                status,
            )
            self._pipeline_id = None

        try:
            response = requests.get(
                self.URL + self.PIPELINES_PATH,
                headers=self.headers,
                params={"type": "TEXT2IMAGE"},
                timeout=self.request_timeout,
            )
            if response.status_code != 200:
                logging.warning(
                    "Kandinsky get_pipeline HTTP=%s body=%s",
                    response.status_code,
                    self._safe_body(response),
                )
                return None

            data = self._get_json(response)
            if not isinstance(data, list):
                logging.warning("Kandinsky pipelines response is not a list: %r", data)
                return None

            active = [
                item for item in data
                if isinstance(item, dict)
                and item.get("id")
                and item.get("type", "TEXT2IMAGE") == "TEXT2IMAGE"
                and item.get("status", "ACTIVE") == "ACTIVE"
            ]

            if not active:
                statuses = [
                    f"{item.get('name', item.get('id', '?'))}:{item.get('status', '?')}"
                    for item in data
                    if isinstance(item, dict)
                ]
                logging.warning("Kandinsky has no ACTIVE TEXT2IMAGE pipeline: %s", statuses)
                return None

            # Сначала пробуем самый свежий/новый pipeline, если API прислал даты.
            active.sort(key=lambda item: str(item.get("lastModified") or item.get("version") or ""), reverse=True)
            for item in active:
                pipeline_id = str(item["id"])
                availability = self._availability(pipeline_id)
                if availability == "ACTIVE":
                    self._pipeline_id = pipeline_id
                    logging.info(
                        "Kandinsky selected pipeline=%s name=%s version=%s status=%s",
                        pipeline_id,
                        item.get("name"),
                        item.get("version"),
                        availability,
                    )
                    return pipeline_id

            logging.warning("Kandinsky ACTIVE pipelines exist, but none is currently available")
            return None
        except Exception as exc:
            logging.warning("Kandinsky get_pipeline error: %s", exc)
            return None

    def _resolve_pipeline(self, pipeline_id: Optional[str]) -> Optional[str]:
        if pipeline_id:
            availability = self._availability(pipeline_id)
            if availability == "ACTIVE":
                self._pipeline_id = pipeline_id
                return pipeline_id
            logging.warning(
                "Kandinsky requested pipeline=%s is unavailable: %s; refreshing",
                pipeline_id,
                availability,
            )
        return self.get_pipeline(force_refresh=True)

    def _run_once(self, prompt: str, pipeline_id: str) -> Tuple[Optional[str], Optional[str], bool]:
        params = {
            "type": "GENERATE",
            "numImages": 1,
            "width": 1024,
            "height": 1024,
            "generateParams": {"query": prompt[:900]},
        }
        files = {
            "pipeline_id": (None, str(pipeline_id)),
            "params": (None, json.dumps(params), "application/json"),
        }

        try:
            response = requests.post(
                self.URL + self.RUN_PATH,
                headers=self.headers,
                files=files,
                timeout=self.request_timeout,
            )
            data = self._get_json(response)
            if response.status_code not in (200, 201):
                error = f"HTTP {response.status_code}: {self._safe_body(response)}"
                logging.warning("Kandinsky run pipeline=%s %s", pipeline_id, error)
                # 404/409 обычно означают устаревший/недоступный pipeline.
                return None, error, response.status_code in (404, 409)

            if not isinstance(data, dict):
                return None, "Invalid JSON response", False

            model_status = data.get("model_status") or data.get("pipeline_status")
            if model_status:
                error = f"model_status={model_status}"
                logging.warning("Kandinsky run blocked pipeline=%s %s", pipeline_id, error)
                return None, error, model_status in {"DISABLED_MANUALLY", "DISABLED_BY_QUEUE"}

            uuid = data.get("uuid")
            if not uuid:
                error = data.get("errorDescription") or data.get("message") or f"Unexpected response: {data}"
                logging.warning("Kandinsky run returned no uuid pipeline=%s error=%s", pipeline_id, error)
                return None, str(error), False

            uuid = str(uuid)
            try:
                initial_delay = max(0.0, min(float(data.get("status_time") or 0), 30.0))
            except (TypeError, ValueError):
                initial_delay = 0.0
            self._initial_delays[uuid] = initial_delay
            logging.info(
                "Kandinsky run accepted pipeline=%s HTTP=%s uuid=%s status=%s status_time=%s",
                pipeline_id,
                response.status_code,
                uuid,
                data.get("status"),
                data.get("status_time"),
            )
            return uuid, None, False
        except Exception as exc:
            logging.warning("Kandinsky generate pipeline=%s error=%s", pipeline_id, exc)
            return None, str(exc), False

    def generate(self, prompt: str, pipeline_id: str) -> Tuple[Optional[str], Optional[str]]:
        """Запускает генерацию; при протухшем pipeline обновляет его и повторяет один раз."""
        pipeline = self._resolve_pipeline(pipeline_id)
        if not pipeline:
            return None, "No active TEXT2IMAGE pipeline"

        uuid, error, should_refresh = self._run_once(prompt, pipeline)
        if uuid or not should_refresh:
            return uuid, error

        refreshed = self.get_pipeline(force_refresh=True)
        if not refreshed or refreshed == pipeline:
            return None, error

        logging.info("Kandinsky retrying generation with refreshed pipeline=%s", refreshed)
        uuid, retry_error, _ = self._run_once(prompt, refreshed)
        return uuid, retry_error

    @staticmethod
    def _decode_image(file_value: str) -> Optional[bytes]:
        if not file_value:
            return None
        if file_value.startswith("http://") or file_value.startswith("https://"):
            try:
                response = requests.get(file_value, timeout=30)
                if response.status_code == 200:
                    return response.content
                logging.warning("Kandinsky result URL HTTP=%s", response.status_code)
                return None
            except Exception as exc:
                logging.warning("Kandinsky result URL error=%s", exc)
                return None

        try:
            return base64.b64decode(file_value.split(",")[-1], validate=False)
        except Exception as exc:
            logging.warning("Kandinsky base64 decode error=%s", exc)
            return None

    def check(self, uuid: str) -> Tuple[Optional[bytes], Optional[str]]:
        """Ждёт DONE до ``max_wait_seconds``, учитывая status_time от run."""
        initial_delay = self._initial_delays.pop(str(uuid), 0.0)
        if initial_delay:
            logging.info("Kandinsky initial wait uuid=%s delay=%ss", uuid, initial_delay)
            time.sleep(initial_delay)

        deadline = time.monotonic() + self.max_wait_seconds
        while time.monotonic() < deadline:
            try:
                response = requests.get(
                    self.URL + self.STATUS_PATH.format(uuid=uuid),
                    headers=self.headers,
                    timeout=self.request_timeout,
                )
                if response.status_code != 200:
                    error = f"HTTP {response.status_code}: {self._safe_body(response)}"
                    logging.warning("Kandinsky status uuid=%s %s", uuid, error)
                    return None, error

                data = self._get_json(response)
                if not isinstance(data, dict):
                    return None, "Invalid status response"

                status = data.get("status")
                if status == "DONE":
                    result = data.get("result") or {}
                    if result.get("censored"):
                        logging.warning("Kandinsky result censored uuid=%s", uuid)
                        return None, "Censored"
                    files = result.get("files") or []
                    if not files:
                        return None, "DONE without files"
                    image = self._decode_image(str(files[0]))
                    if not image:
                        return None, "Cannot decode generated image"
                    logging.info(
                        "Kandinsky DONE uuid=%s generationTime=%s bytes=%s",
                        uuid,
                        data.get("generationTime"),
                        len(image),
                    )
                    return image, None

                if status == "FAIL":
                    error = data.get("errorDescription") or data.get("message") or "Generation failed"
                    logging.warning("Kandinsky FAIL uuid=%s error=%s data=%s", uuid, error, data)
                    return None, str(error)

                logging.info("Kandinsky status uuid=%s status=%s", uuid, status)
                time.sleep(self.poll_interval)
            except Exception as exc:
                logging.warning("Kandinsky check uuid=%s error=%s", uuid, exc)
                return None, str(exc)

        logging.warning("Kandinsky check timeout uuid=%s after=%ss", uuid, self.max_wait_seconds)
        return None, "Timeout"


def install_into_picgeneration(picgeneration_module) -> FusionBrainAPI:
    """Подменяет старый встроенный клиент в ``AI.picgeneration`` без большого рефакторинга файла."""
    client = FusionBrainAPI(
        "https://api-key.fusionbrain.ai/",
        getattr(picgeneration_module, "KANDINSKY_API_KEY", None),
        getattr(picgeneration_module, "KANDINSKY_SECRET_KEY", None),
    )
    picgeneration_module.kandinsky_api = client
    # Не наследуем потенциально протухший pipeline старого клиента.
    picgeneration_module.PIPELINE_ID = None
    logging.info("Installed resilient Kandinsky/FusionBrain client")
    return client
