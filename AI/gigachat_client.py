"""GigaChat conversation wrapper using the current API endpoint and request schema."""

import logging
import threading
from typing import List, Optional

from gigachat import GigaChat
from gigachat.models import Chat, Messages, MessagesRole


GIGACHAT_BASE_URL = "https://api.giga.chat/v1"


class GigaChatResponse:
    """Small compatibility response with the `.text` attribute used by Upupa."""

    def __init__(self, text: str):
        self.text = text


class GigaChatConversationWrapper:
    """Text GigaChat client with per-chat fallback and diagnostics.

    The old wrapper passed generation parameters to ``GigaChat(...)``. SDK 0.2.x
    treats those as unknown constructor kwargs, so model/temperature/max_tokens
    are now sent in the ``Chat`` request payload instead.
    """

    def __init__(self, api_key: str, default_queue: List[str], special_queue: List[str]):
        self.api_key = api_key
        self.default_queue = list(default_queue)
        self.special_queue = list(special_queue)
        self.last_used_model_name: Optional[str] = None
        self.last_used_model_by_chat: dict[str, str] = {}
        self._state_lock = threading.Lock()

    def _get_queue(self, chat_id: Optional[int]) -> List[str]:
        from core.settings import SPECIAL_CHAT_ID

        if chat_id is not None and str(chat_id) == str(SPECIAL_CHAT_ID):
            return self.special_queue
        return self.default_queue

    @staticmethod
    def _chat_key(chat_id: Optional[int]) -> Optional[str]:
        return str(chat_id) if chat_id is not None else None

    def get_last_used_model(self, chat_id: Optional[int] = None) -> Optional[str]:
        """Return the actual last successful model for this chat.

        Before the first request after a restart, return the head of that chat's
        configured queue so the diagnostic command still reflects routing.
        """
        key = self._chat_key(chat_id)
        with self._state_lock:
            if key is not None and key in self.last_used_model_by_chat:
                return self.last_used_model_by_chat[key]
            if chat_id is None and self.last_used_model_name:
                return self.last_used_model_name

        queue = self._get_queue(chat_id)
        return queue[0] if queue else None

    def _remember_success(self, chat_id: Optional[int], model_name: str) -> None:
        key = self._chat_key(chat_id)
        with self._state_lock:
            self.last_used_model_name = model_name
            if key is not None:
                self.last_used_model_by_chat[key] = model_name

    def generate_content(
        self,
        prompt: str,
        *,
        chat_id=None,
        temperature: float = 0.7,
        **kwargs,
    ) -> GigaChatResponse:
        """Generate text using the configured per-chat fallback queue."""
        if not isinstance(prompt, str):
            raise TypeError("GigaChat conversation wrapper currently accepts text prompts only")

        queue = self._get_queue(chat_id)
        max_tokens = kwargs.get("max_tokens", 500)

        for model_name in queue:
            try:
                payload = Chat(
                    model=model_name,
                    messages=[
                        Messages(
                            role=MessagesRole.USER,
                            content=prompt,
                        )
                    ],
                    temperature=temperature,
                    max_tokens=max_tokens,
                )

                with GigaChat(
                    credentials=self.api_key,
                    base_url=GIGACHAT_BASE_URL,
                    verify_ssl_certs=False,
                    timeout=120,
                ) as giga:
                    response = giga.chat(payload)

                content = response.choices[0].message.content or ""
                self._remember_success(chat_id, model_name)
                logging.info(
                    "GigaChat success chat_id=%s requested_model=%s response_model=%s",
                    chat_id,
                    model_name,
                    getattr(response, "model", None),
                )
                return GigaChatResponse(content)

            except Exception as exc:
                logging.error(
                    "GigaChat error chat_id=%s model=%s: %s",
                    chat_id,
                    model_name,
                    exc,
                )
                continue

        raise RuntimeError("All GigaChat models failed")
