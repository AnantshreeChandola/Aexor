"""
MCP Server Configuration

Maps logical MCP server names to connection details.
Loaded from environment variables at startup.

Service-level API keys live here (env vars), never in the database.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Literal

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class MCPServerConfig(BaseModel):
    """Connection details for a single hosted MCP server."""

    name: str = Field(min_length=1, max_length=128)
    url: str = Field(min_length=1)
    api_key: str = Field(default="")
    api_key_header: str = Field(default="x-api-key")
    extra_headers: dict[str, str] = Field(default_factory=dict)
    entity_id_injection: Literal["argument", "header", "query"] = "argument"
    entity_id_field: str = "entity_id"


class MCPServerNotConfiguredError(Exception):
    """Raised when a logical server name has no configuration."""

    def __init__(self, server_name: str) -> None:
        self.server_name = server_name
        super().__init__(
            f"MCP server '{server_name}' is not configured. "
            f"Set MCP_SERVER_{server_name.upper()}_URL or add it to MCP_SERVERS."
        )


class MCPConfigRegistry:
    """Registry of configured MCP servers. Immutable after construction."""

    def __init__(self, servers: dict[str, MCPServerConfig]) -> None:
        self._servers = dict(servers)

    def get(self, name: str) -> MCPServerConfig | None:
        return self._servers.get(name)

    def get_or_raise(self, name: str) -> MCPServerConfig:
        cfg = self._servers.get(name)
        if cfg is None:
            raise MCPServerNotConfiguredError(name)
        return cfg

    def list_servers(self) -> list[str]:
        return list(self._servers.keys())

    def __len__(self) -> int:
        return len(self._servers)


def load_mcp_config_from_env() -> MCPConfigRegistry:
    """Load MCP server configuration from environment variables.

    Two sources (individual vars override JSON blob):

    1. JSON blob: ``MCP_SERVERS={"composio": {"url": "...", "api_key": "..."}}``
    2. Individual vars: ``MCP_SERVER_<NAME>_URL``, ``MCP_SERVER_<NAME>_API_KEY``,
       ``MCP_SERVER_<NAME>_API_KEY_HEADER``, ``MCP_SERVER_<NAME>_ENTITY_ID_INJECTION``,
       ``MCP_SERVER_<NAME>_ENTITY_ID_FIELD``, ``MCP_SERVER_<NAME>_EXTRA_HEADERS`` (JSON)
    """
    servers: dict[str, dict] = {}

    # --- Source 1: JSON blob ---
    blob = os.environ.get("MCP_SERVERS", "").strip()
    if blob:
        try:
            parsed = json.loads(blob)
            if isinstance(parsed, dict):
                for name, cfg in parsed.items():
                    if isinstance(cfg, dict):
                        servers[name.lower()] = {"name": name.lower(), **cfg}
        except json.JSONDecodeError:
            logger.warning("MCP_SERVERS env var contains invalid JSON, ignoring")

    # --- Source 2: Individual env vars (override blob) ---
    prefix = "MCP_SERVER_"
    suffix_url = "_URL"
    seen_names: set[str] = set()

    for key in os.environ:
        if key.startswith(prefix) and key.endswith(suffix_url):
            # Extract server name: MCP_SERVER_COMPOSIO_URL -> composio
            name = key[len(prefix) : -len(suffix_url)].lower()
            seen_names.add(name)

    for name in seen_names:
        upper = name.upper()
        url = os.environ.get(f"MCP_SERVER_{upper}_URL", "")
        if not url:
            continue

        overrides: dict = {"name": name, "url": url}

        api_key = os.environ.get(f"MCP_SERVER_{upper}_API_KEY")
        if api_key is not None:
            overrides["api_key"] = api_key

        api_key_header = os.environ.get(f"MCP_SERVER_{upper}_API_KEY_HEADER")
        if api_key_header is not None:
            overrides["api_key_header"] = api_key_header

        entity_id_injection = os.environ.get(f"MCP_SERVER_{upper}_ENTITY_ID_INJECTION")
        if entity_id_injection is not None:
            overrides["entity_id_injection"] = entity_id_injection

        entity_id_field = os.environ.get(f"MCP_SERVER_{upper}_ENTITY_ID_FIELD")
        if entity_id_field is not None:
            overrides["entity_id_field"] = entity_id_field

        extra_headers_raw = os.environ.get(f"MCP_SERVER_{upper}_EXTRA_HEADERS")
        if extra_headers_raw is not None:
            try:
                overrides["extra_headers"] = json.loads(extra_headers_raw)
            except json.JSONDecodeError:
                logger.warning(
                    "MCP_SERVER_%s_EXTRA_HEADERS contains invalid JSON, ignoring",
                    upper,
                )

        # Merge: individual vars override blob values
        if name in servers:
            servers[name].update(overrides)
        else:
            servers[name] = overrides

    # --- Build validated configs ---
    configs: dict[str, MCPServerConfig] = {}
    for name, raw in servers.items():
        try:
            configs[name] = MCPServerConfig(**raw)
        except Exception:
            logger.warning("Invalid MCP server config for '%s', skipping", name)

    if configs:
        logger.info(
            "MCP config loaded",
            extra={"servers": list(configs.keys())},
        )
    else:
        logger.info("No MCP servers configured")

    return MCPConfigRegistry(configs)


# ------------------------------------------------------------------
# Composio-specific configuration (opt-in via COMPOSIO_API_KEY)
# ------------------------------------------------------------------


class ComposioConfig(BaseModel):
    """Configuration for Composio per-user MCP URL generation."""

    api_key: str = Field(min_length=1)
    mcp_config_id: str = Field(min_length=1)
    base_url: str = Field(default="https://backend.composio.dev")
    user_url_cache_ttl: int = Field(default=3600, ge=0)
    auth_configs: dict[str, str] = Field(default_factory=dict)
    system_user_id: str = Field(default="__system__")


def load_composio_config_from_env() -> ComposioConfig | None:
    """Load Composio configuration from environment variables.

    Returns ``None`` if ``COMPOSIO_API_KEY`` is not set, enabling graceful
    opt-in.  When set, ``COMPOSIO_MCP_CONFIG_ID`` is required.
    """
    api_key = os.environ.get("COMPOSIO_API_KEY", "").strip()
    if not api_key:
        return None

    mcp_config_id = os.environ.get("COMPOSIO_MCP_CONFIG_ID", "").strip()
    if not mcp_config_id:
        logger.warning(
            "COMPOSIO_API_KEY set but COMPOSIO_MCP_CONFIG_ID missing — "
            "Composio mode disabled"
        )
        return None

    auth_configs: dict[str, str] = {}
    auth_raw = os.environ.get("COMPOSIO_AUTH_CONFIGS", "").strip()
    if auth_raw:
        try:
            parsed = json.loads(auth_raw)
            if isinstance(parsed, dict):
                auth_configs = {str(k): str(v) for k, v in parsed.items()}
        except json.JSONDecodeError:
            logger.warning("COMPOSIO_AUTH_CONFIGS contains invalid JSON, ignoring")

    base_url = os.environ.get("COMPOSIO_BASE_URL", "https://backend.composio.dev").strip()
    system_user_id = os.environ.get("COMPOSIO_SYSTEM_USER_ID", "__system__").strip()
    ttl = int(os.environ.get("COMPOSIO_URL_CACHE_TTL", "3600"))

    logger.info(
        "Composio config loaded",
        extra={
            "mcp_config_id": mcp_config_id,
            "auth_config_providers": list(auth_configs.keys()),
            "system_user_id": system_user_id,
            "url_cache_ttl": ttl,
        },
    )

    return ComposioConfig(
        api_key=api_key,
        mcp_config_id=mcp_config_id,
        base_url=base_url,
        user_url_cache_ttl=ttl,
        auth_configs=auth_configs,
        system_user_id=system_user_id,
    )
