"""Model adapters. The wire format follows Chat Completions function calls."""

import json
import os
from typing import Any, Dict, List, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class ChatModel(Protocol):
    def complete(self, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]]) -> Dict[str, Any]:
        ...


class EchoModel:
    """An explicit offline smoke-test adapter, not an AI model."""

    def complete(self, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]]) -> Dict[str, Any]:
        last = next(message for message in reversed(messages) if message["role"] == "user")
        return {"role": "assistant", "content": "Echo: " + str(last["content"])}


class ChatCompletionsModel:
    def __init__(self, base_url: str, api_key: str, model: str, timeout: int = 60):
        if not base_url.startswith(("https://", "http://localhost:", "http://127.0.0.1:")):
            raise ValueError("MU_API_BASE must use HTTPS or a local HTTP address")
        self.endpoint = base_url.rstrip("/") + "/chat/completions"
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    @classmethod
    def from_environment(cls) -> "ChatCompletionsModel":
        model = os.environ.get("MU_MODEL")
        if not model:
            raise ValueError("Set MU_MODEL, or set MU_MODEL_BACKEND=echo for an offline smoke test")
        return cls(
            os.environ.get("MU_API_BASE", "https://api.openai.com/v1"),
            os.environ.get("MU_API_KEY", ""),
            model,
        )

    def complete(self, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]]) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"model": self.model, "messages": messages}
        if tools:
            payload["tools"] = tools
        body = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = "Bearer " + self.api_key
        request = Request(self.endpoint, data=body, headers=headers, method="POST")
        try:
            with urlopen(request, timeout=self.timeout) as response:
                payload = json.load(response)
        except HTTPError as error:
            raise RuntimeError("Model request failed with HTTP " + str(error.code)) from error
        except URLError as error:
            raise RuntimeError("Model endpoint is unreachable: " + str(error.reason)) from error
        try:
            message = payload["choices"][0]["message"]
            if not isinstance(message, dict):
                raise TypeError("message must be an object")
            return message
        except (KeyError, IndexError, TypeError) as error:
            raise RuntimeError("Model returned an invalid Chat Completions response") from error


def model_from_environment() -> ChatModel:
    if os.environ.get("MU_MODEL_BACKEND") == "echo":
        return EchoModel()
    return ChatCompletionsModel.from_environment()
