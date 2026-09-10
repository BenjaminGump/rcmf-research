from __future__ import annotations

from collections.abc import Callable, Mapping
import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


Transport = Callable[[str, str, Mapping[str, Any] | None], Mapping[str, Any]]


class WebShopHTTPRuntime:
    """Client for the Harness-owned, range-scoped Lambda WebShop bridge."""

    def __init__(
        self,
        index: int,
        *,
        deterministic_seed: int = 233,
        session_namespace: str = "rcmf-webshop-v1",
        endpoint: str | None = None,
        transport: Transport | None = None,
    ) -> None:
        if deterministic_seed != 233:
            raise ValueError("WebShop HTTP runtime seed is frozen to 233")
        self.index = int(index)
        self.task_id = f"agentbench-fc-webshop:{self.index:05d}"
        self.endpoint = (endpoint or os.environ.get("RCMF_WEBSHOP_SERVER_URL", "")).rstrip(
            "/"
        )
        if not self.endpoint and transport is None:
            raise ValueError("RCMF_WEBSHOP_SERVER_URL is required")
        self.session_id = f"{session_namespace}-{self.index}"
        self._transport = transport or self._http_transport
        self._observation = ""
        self._instruction = ""
        self._available_actions: Mapping[str, Any] = {}
        self._raw_reward = 0.0
        self._done = False
        self._step_index = 0
        health = self._transport("GET", "/health", None)
        if health.get("format") != "agentbench_fc_webshop_http_bridge_v1":
            raise RuntimeError("unexpected WebShop HTTP bridge identity")
        if self.index not in range(int(health["allowed_start"]), int(health["allowed_end"])):
            raise ValueError("task index is outside the server's frozen allowed range")
        self.reset()

    def _http_transport(
        self, method: str, path: str, payload: Mapping[str, Any] | None
    ) -> Mapping[str, Any]:
        data = None
        headers = {}
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(self.endpoint + path, data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=1800) as response:
                result = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"WebShop bridge HTTP {exc.code}: {body}") from exc
        except URLError as exc:
            raise RuntimeError(f"WebShop bridge is unavailable: {exc.reason}") from exc
        if not isinstance(result, Mapping):
            raise TypeError("WebShop bridge response must be an object")
        return dict(result)

    @property
    def observation(self) -> str:
        return self._observation

    @property
    def instruction(self) -> str:
        return self._instruction

    @property
    def raw_reward(self) -> float:
        return self._raw_reward

    @property
    def done(self) -> bool:
        return self._done

    def available_actions(self) -> Mapping[str, Any]:
        return dict(self._available_actions)

    def reset(self) -> Mapping[str, Any]:
        result = self._transport(
            "POST",
            "/reset",
            {"session_id": self.session_id, "index": self.index},
        )
        if result.get("task_id") != self.task_id or int(result.get("index", -1)) != self.index:
            raise RuntimeError("WebShop bridge reset returned a different task")
        self._instruction = str(result["instruction"])
        self._observation = str(result["observation"])
        self._available_actions = dict(result["available_actions"])
        self._raw_reward = float(result["raw_reward"])
        self._done = bool(result["done"])
        self._step_index = int(result["step_index"])
        return dict(result)

    def step_action(self, action: str) -> Mapping[str, Any]:
        result = self._transport(
            "POST", "/step", {"session_id": self.session_id, "action": action}
        )
        if result.get("task_id") != self.task_id:
            raise RuntimeError("WebShop bridge step returned a different task")
        if int(result.get("step_index", -1)) != self._step_index:
            raise RuntimeError("WebShop bridge step index differs")
        self._observation = str(result["observation"])
        self._available_actions = dict(result["available_actions"])
        self._raw_reward = float(result["raw_reward"])
        self._done = bool(result["done"])
        self._step_index += 1
        return dict(result)

    def close(self) -> None:
        self._transport("POST", "/close", {"session_id": self.session_id})
