"""Roteamento de upstream no conversor: a base URL vem do gateway (header),
com fallback no env para o modo direto (sem gateway na frente)."""
from typing import Optional
from urllib.parse import urlparse

HEADER_BASE = "x-9router-upstream-base"
HEADER_NAME = "x-9router-upstream"


class InvalidUpstreamBase(ValueError):
    """X-9Router-Upstream-Base fora do contrato (scheme http/https)."""


def resolve_upstream_base(header_value: Optional[str], env_default: str) -> str:
    """Devolve a base do 9Router: header do gateway, senão o env (modo direto)."""
    if not header_value:
        return (env_default or "").rstrip("/")
    parsed = urlparse(header_value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise InvalidUpstreamBase(f"invalid upstream base: {header_value!r}")
    return header_value.rstrip("/")


def browser_origin_rejected(origin: Optional[str]) -> bool:
    """Claude Code/Desktop é cliente nativo e não manda Origin: Origin = browser = recusa."""
    return bool(origin)
