"""Adapters for OpenAI-compatible HTTP providers such as OpenRouter/SiliconFlow."""

import logging

import requests


class OpenAICompatibleWrapper:
    def __init__(self, api_key: str, base_url: str, model_name: str):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model_name = model_name

    def generate_text(
        self,
        prompt: str,
        max_tokens: int = 1024,
        temperature: float = 0.7,
        presence_penalty: float = 0.0,
    ) -> str:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/upupa-bot",
            "X-Title": "UpupaBot",
        }
        payload = {
            "model": self.model_name,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "presence_penalty": presence_penalty,
            "max_tokens": max_tokens,
        }
        try:
            response = requests.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload,
                timeout=60,
            )
            response.raise_for_status()
            data = response.json()
            result = data["choices"][0]["message"]["content"]
            logging.info(
                "OpenAICompatibleWrapper [%s]: получено %s символов",
                self.model_name,
                len(result) if result else 0,
            )
            return result or ""
        except Exception as exc:
            logging.error(
                "OpenAICompatibleWrapper error [%s]: %s",
                self.model_name,
                exc,
            )
            raise
