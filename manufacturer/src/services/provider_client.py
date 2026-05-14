"""HTTP client for remote provider APIs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx


class ProviderClientError(RuntimeError):
    """Base provider client error."""


class ProviderConnectionError(ProviderClientError):
    """Raised when provider cannot be reached."""


class ProviderApiError(ProviderClientError):
    """Raised when provider returns non-2xx responses."""


@dataclass
class ProviderConfig:
    name: str
    url: str


class ProviderClient:
    """Simple sync client used by API logic and CLI."""

    def __init__(self, timeout_seconds: float = 8.0):
        self.timeout_seconds = timeout_seconds

    def _request_json(
        self,
        method: str,
        url: str,
        provider_name: str,
        **kwargs: Any,
    ) -> Any:
        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                response = client.request(method, url, **kwargs)
        except httpx.RequestError as exc:
            raise ProviderConnectionError(
                f"Provider '{provider_name}' is unreachable ({exc.__class__.__name__}: {exc})"
            ) from exc

        if response.status_code >= 400:
            detail = response.text.strip() or f"HTTP {response.status_code}"
            raise ProviderApiError(
                f"Provider '{provider_name}' request failed ({response.status_code}): {detail}"
            )
        return response.json()

    def get_catalog(self, provider: ProviderConfig) -> list[dict]:
        url = f"{provider.url.rstrip('/')}/api/catalog"
        return self._request_json("GET", url, provider.name)

    def create_order(
        self,
        provider: ProviderConfig,
        product: str,
        quantity: int,
        buyer: str = "manufacturer",
    ) -> dict:
        url = f"{provider.url.rstrip('/')}/api/orders"
        payload = {"product": product, "quantity": quantity, "buyer": buyer}
        return self._request_json("POST", url, provider.name, json=payload)

    def get_order(self, provider: ProviderConfig, provider_order_id: int) -> dict:
        url = f"{provider.url.rstrip('/')}/api/orders/{provider_order_id}"
        return self._request_json("GET", url, provider.name)
