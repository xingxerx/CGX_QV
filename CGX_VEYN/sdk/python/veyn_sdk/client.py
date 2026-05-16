"""Synchronous and async HTTP client for the VEYN daemon REST API."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Iterator, List, Optional

try:
    import httpx
except ImportError as exc:  # pragma: no cover
    raise ImportError("veyn-sdk requires 'httpx': pip install veyn-sdk[http]") from exc


@dataclass
class VeynEvent:
    metric: str
    value: float
    unit: str
    ts: int
    device_id: str
    source: str

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "VeynEvent":
        return cls(
            metric=d["metric"],
            value=float(d["value"]),
            unit=d.get("unit", ""),
            ts=int(d["ts"]),
            device_id=d.get("device_id", ""),
            source=d.get("source", ""),
        )


@dataclass
class StateDelta:
    device_id: str
    metric: str
    value: float
    unit: str
    ts: int

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "StateDelta":
        return cls(**{k: d[k] for k in ("device_id", "metric", "value", "unit", "ts")})


@dataclass
class ContextSnapshot:
    timestamp_ms: int
    session_id: str
    intent: str
    confidence: float
    active_devices: List[str]
    state_deltas: List[StateDelta]

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ContextSnapshot":
        return cls(
            timestamp_ms=d["timestamp_ms"],
            session_id=d["session_id"],
            intent=d["intent"],
            confidence=float(d["confidence"]),
            active_devices=d.get("active_devices", []),
            state_deltas=[StateDelta.from_dict(s) for s in d.get("state_deltas", [])],
        )


class VeynClient:
    """Client for the VEYN daemon REST API.

    Usage::

        client = VeynClient("http://localhost:7700", token="<your-token>")

        # Check daemon health.
        health = client.health()

        # Get current context.
        ctx = client.context_current()
        print(ctx.intent, ctx.confidence)

        # List recent events.
        events = client.recent_events()

        # Stream events via SSE (synchronous iterator).
        for event in client.stream_sse(metrics="heart_rate,hrv"):
            print(event.metric, event.value)

    Async usage::

        async with VeynClient.async_context("http://localhost:7700", token="...") as c:
            ctx = await c.async_context_current()
    """

    DEFAULT_URL = "http://localhost:7700"

    def __init__(
        self,
        base_url: str = DEFAULT_URL,
        token: Optional[str] = None,
        timeout: float = 10.0,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._timeout = timeout
        self._sync_client: Optional[httpx.Client] = None
        self._async_client: Optional[httpx.AsyncClient] = None

    # ── Sync transport ────────────────────────────────────────────────────────

    def _client(self) -> httpx.Client:
        if self._sync_client is None:
            self._sync_client = httpx.Client(
                base_url=self._base,
                headers=self._headers,
                timeout=self._timeout,
            )
        return self._sync_client

    def close(self) -> None:
        if self._sync_client:
            self._sync_client.close()
            self._sync_client = None

    def __enter__(self) -> "VeynClient":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def _get(self, path: str, **params: Any) -> dict[str, Any]:
        resp = self._client().get(path, params={k: v for k, v in params.items() if v is not None})
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        resp = self._client().post(path, json=body)
        resp.raise_for_status()
        return resp.json()

    # ── Public sync API ───────────────────────────────────────────────────────

    def health(self) -> dict[str, Any]:
        return self._get("/v1/health")

    def recent_events(self) -> List[VeynEvent]:
        data = self._get("/v1/events/recent")
        return [VeynEvent.from_dict(e) for e in data.get("events", [])]

    def metric(self, name: str) -> VeynEvent:
        return VeynEvent.from_dict(self._get(f"/v1/metrics/{name}"))

    def devices(self) -> List[dict[str, Any]]:
        return self._get("/v1/devices").get("devices", [])

    def plugins(self) -> List[dict[str, Any]]:
        return self._get("/v1/plugins").get("plugins", [])

    def presence(self) -> List[dict[str, Any]]:
        return self._get("/v1/presence").get("presence", [])

    def context_current(self) -> ContextSnapshot:
        return ContextSnapshot.from_dict(self._get("/v1/context/current"))

    def context_history(self, n: int = 10) -> List[ContextSnapshot]:
        data = self._get("/v1/context/history", n=n)
        return [ContextSnapshot.from_dict(s) for s in data.get("history", [])]

    def notify(
        self,
        title: str,
        body: str,
        target_device: Optional[str] = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"title": title, "body": body}
        if target_device:
            payload["target_device"] = target_device
        return self._post("/v1/notify", payload)

    def stream_sse(
        self,
        metrics: Optional[str] = None,
        sources: Optional[str] = None,
    ) -> Iterator[VeynEvent]:
        """Synchronous SSE iterator over filtered VeynEvents."""
        params: dict[str, str] = {}
        if metrics:
            params["metrics"] = metrics
        if sources:
            params["sources"] = sources

        # Add token to params for SSE (headers may not work in all clients).
        token = self._headers.get("Authorization", "").removeprefix("Bearer ").strip()
        if token:
            params["token"] = token

        with httpx.Client(base_url=self._base, timeout=None) as client:
            with client.stream("GET", "/v1/stream/sse", params=params) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    if line.startswith("data:"):
                        data_str = line[len("data:"):].strip()
                        if data_str:
                            try:
                                yield VeynEvent.from_dict(json.loads(data_str))
                            except (json.JSONDecodeError, KeyError):
                                continue

    def subscribe_context(
        self,
        intents: Optional[str] = None,
        min_confidence: float = 0.0,
        tier: str = "filtered",
    ) -> Iterator[ContextSnapshot]:
        """Synchronous SSE iterator for context snapshots."""
        params: dict[str, Any] = {"min_confidence": min_confidence, "tier": tier}
        if intents:
            params["intents"] = intents
        token = self._headers.get("Authorization", "").removeprefix("Bearer ").strip()
        if token:
            params["token"] = token

        with httpx.Client(base_url=self._base, timeout=None) as client:
            with client.stream("GET", "/v1/context/subscribe", params=params) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    if line.startswith("data:"):
                        data_str = line[len("data:"):].strip()
                        if data_str:
                            try:
                                yield ContextSnapshot.from_dict(json.loads(data_str))
                            except (json.JSONDecodeError, KeyError):
                                continue

    # ── Async transport ───────────────────────────────────────────────────────

    def _async_http(self) -> httpx.AsyncClient:
        if self._async_client is None:
            self._async_client = httpx.AsyncClient(
                base_url=self._base,
                headers=self._headers,
                timeout=self._timeout,
            )
        return self._async_client

    async def aclose(self) -> None:
        if self._async_client:
            await self._async_client.aclose()
            self._async_client = None

    async def __aenter__(self) -> "VeynClient":
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.aclose()

    async def _aget(self, path: str, **params: Any) -> dict[str, Any]:
        resp = await self._async_http().get(
            path, params={k: v for k, v in params.items() if v is not None}
        )
        resp.raise_for_status()
        return resp.json()

    async def async_health(self) -> dict[str, Any]:
        return await self._aget("/v1/health")

    async def async_context_current(self) -> ContextSnapshot:
        return ContextSnapshot.from_dict(await self._aget("/v1/context/current"))

    async def async_recent_events(self) -> List[VeynEvent]:
        data = await self._aget("/v1/events/recent")
        return [VeynEvent.from_dict(e) for e in data.get("events", [])]

    async def async_stream_sse(
        self,
        metrics: Optional[str] = None,
        sources: Optional[str] = None,
    ) -> AsyncIterator[VeynEvent]:
        """Async SSE iterator over filtered VeynEvents."""
        params: dict[str, Any] = {}
        if metrics:
            params["metrics"] = metrics
        if sources:
            params["sources"] = sources
        token = self._headers.get("Authorization", "").removeprefix("Bearer ").strip()
        if token:
            params["token"] = token

        async with httpx.AsyncClient(base_url=self._base, timeout=None) as client:
            async with client.stream("GET", "/v1/stream/sse", params=params) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if line.startswith("data:"):
                        data_str = line[len("data:"):].strip()
                        if data_str:
                            try:
                                yield VeynEvent.from_dict(json.loads(data_str))
                            except (json.JSONDecodeError, KeyError):
                                continue

    @classmethod
    def from_token_file(
        cls,
        base_url: str = DEFAULT_URL,
        token_path: Optional[str] = None,
    ) -> "VeynClient":
        """Load token from the default VEYN token file path."""
        import os
        if token_path is None:
            xdg = os.environ.get("XDG_DATA_HOME", "")
            home = os.environ.get("HOME") or os.environ.get("USERPROFILE", "")
            base = xdg if xdg else os.path.join(home, ".local", "share")
            token_path = os.path.join(base, "veyn", "token")
        try:
            with open(token_path) as f:
                token = f.read().strip().split(":")[0]  # strip scope suffix if present
        except OSError:
            token = None
        return cls(base_url, token=token)
