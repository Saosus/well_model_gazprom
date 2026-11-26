"""Small OpenAI-compatible Chat Completions adapter; no SDK dependency."""
import os
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx


class LLMError(Exception):
    pass


@dataclass(frozen=True)
class Settings:
    base_url: str = ""
    model: str = ""
    api_key: str = field(default="", repr=False)
    timeout: float = 30.0
    max_steps: int = 6

    @classmethod
    def from_env(cls):
        try:
            timeout = float(os.getenv("LLM_TIMEOUT", "30"))
            steps = int(os.getenv("LLM_MAX_TOOL_STEPS", "6"))
            if not 0 < timeout <= 120 or not 1 <= steps <= 12:
                raise ValueError
        except ValueError:
            raise LLMError("Некорректные настройки таймаута или лимита шагов LLM.") from None
        return cls(os.getenv("LLM_BASE_URL", "").strip().rstrip("/"),
                   os.getenv("LLM_MODEL", "").strip(), os.getenv("LLM_API_KEY", ""), timeout, steps)

    @property
    def enabled(self):
        return bool(self.base_url and self.model)


class ChatClient:
    def __init__(self, settings: Settings, transport=None):
        self.settings = settings
        self.transport = transport

    async def complete(self, messages, tools):
        s = self.settings
        if not s.enabled:
            raise LLMError("ИИ не настроен: укажите LLM_BASE_URL и LLM_MODEL на сервере.")
        try:
            url = urlsplit(s.base_url)
            if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password or url.query or url.fragment:
                raise ValueError
            headers = {"Authorization": f"Bearer {s.api_key}"} if s.api_key else {}
            async with httpx.AsyncClient(timeout=s.timeout, transport=self.transport, follow_redirects=False, trust_env=False) as client:
                response = await client.post(s.base_url + "/chat/completions", headers=headers, json={
                    "model": s.model, "messages": messages, "stream": False,
                    **({"tools": tools, "tool_choice": "auto"} if tools else {}),
                })
            if response.status_code != 200:
                raise LLMError(f"Провайдер LLM отклонил запрос (HTTP {response.status_code}).")
            body = response.json()
            message = body["choices"][0]["message"]
            if not isinstance(message, dict) or message.get("role") != "assistant":
                raise ValueError
            return message, body.get("usage") or {}
        except httpx.TimeoutException:
            raise LLMError("Истекло время ожидания языковой модели.") from None
        except httpx.RequestError:
            raise LLMError("Языковая модель недоступна.") from None
        except (ValueError, KeyError, IndexError, TypeError):
            raise LLMError("Некорректный ответ или адрес языковой модели.") from None
