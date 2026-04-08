"""
IntegrationManager Service

Manages user-provider connections (OAuth via Composio REST API).
Provides availability checks used by the intake layer.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..adapters.composio_client import ComposioClient

from ..adapters.connection_cache import ConnectionCache
from ..adapters.db import IntegrationDatabaseAdapter
from ..domain.models import (
    ComposioApiError,
    ProviderNotFoundError,
    UserConnection,
)


def _normalize_provider(name: str) -> str:
    """Strip separators and lowercase for provider name comparison.

    Composio tool names use ``GOOGLECALENDAR_CREATE_EVENT`` (no separator
    in app prefix), but connected-account ``appName`` may be
    ``google_calendar`` or ``GOOGLE_CALENDAR``.  Stripping ``_``, ``-``,
    and spaces then lowering gives a canonical form both sides match on.
    """
    return re.sub(r"[_\-\s]", "", name).lower()


logger = logging.getLogger(__name__)


class IntegrationManager:
    """Manages user <-> provider connection status.

    Connection status is used by the intake layer to validate
    "can this user use this tool?" before planning.

    OAuth flows are initiated via Composio REST API and completed
    via callback. We never hold or transmit raw OAuth tokens.

    Tool add/remove are system-level operations that update the
    MCP config's ``allowed_tools`` list via Composio REST API.
    """

    def __init__(
        self,
        db_adapter: IntegrationDatabaseAdapter,
        composio_config: object | None = None,
        composio_client: ComposioClient | None = None,
        connection_cache: ConnectionCache | None = None,
    ) -> None:
        self._db = db_adapter
        self._composio_config = composio_config
        self._composio = composio_client
        self._cache = connection_cache

    def get_available_providers(self) -> list[str]:
        """Return the list of provider names that have auth configs.

        Only meaningful when Composio mode is active. Returns an empty
        list when ``composio_config`` is not set.
        """
        if self._composio_config is None:
            return []
        auth_configs = getattr(self._composio_config, "auth_configs", {})
        return sorted(auth_configs.keys())

    # ------------------------------------------------------------------
    # Per-user: OAuth connection management
    # ------------------------------------------------------------------

    async def initiate_connection(
        self,
        user_id: str,
        provider_name: str,
        redirect_url: str | None = None,
    ) -> str:
        """Initiate OAuth connection flow with Composio.

        Returns the redirect URL for the frontend to navigate to.
        The user's internal ID is used as the Composio entity ID.

        Raises:
            ProviderNotFoundError: If provider has no auth_config_id.
            NotImplementedError: If Composio is not configured.
            ComposioApiError: On non-2xx Composio API response.
            ComposioUnreachableError: On network errors.
        """
        if self._composio_config is None or self._composio is None:
            raise NotImplementedError("Composio OAuth flow requires COMPOSIO_API_KEY to be set.")

        # 1. Static config (COMPOSIO_AUTH_CONFIGS env var)
        auth_configs = getattr(self._composio_config, "auth_configs", {})
        auth_config_id = auth_configs.get(provider_name)

        # 2. Find existing auth config on Composio
        if not auth_config_id:
            auth_config_id = await self._composio.get_auth_config(provider_name)

        # 3. Create new auth config with Composio-managed OAuth
        if not auth_config_id:
            logger.info(
                "creating_auth_config",
                extra={"provider_name": provider_name},
            )
            result = await self._composio.create_integration(provider_name)
            auth_config_id = result.get("id")

        if not auth_config_id:
            raise ProviderNotFoundError(provider_name)

        logger.info(
            "initiate_connection",
            extra={
                "user_id": user_id,
                "provider_name": provider_name,
                "auth_config_id": auth_config_id,
            },
        )

        oauth_redirect_url = await self._composio.initiate_connection(
            user_id=user_id,
            auth_config_id=auth_config_id,
            redirect_url=redirect_url,
        )

        logger.info(
            "connection_initiated",
            extra={
                "user_id": user_id,
                "provider_name": provider_name,
            },
        )
        return oauth_redirect_url

    async def handle_callback(
        self,
        user_id: str,
        provider_name: str,
        composio_response: dict,
    ) -> UserConnection:
        """Handle OAuth callback from Composio.

        Validates the response and upserts connection status.
        """
        connected = composio_response.get("status") == "connected"

        connection = await self._db.upsert_connection(
            user_id=user_id,
            provider_name=provider_name,
            is_connected=connected,
            composio_entity_id=user_id,
        )

        await self._invalidate_cache(user_id)

        logger.info(
            "connection_callback_processed",
            extra={
                "user_id": user_id,
                "provider_name": provider_name,
                "is_connected": connected,
            },
        )
        return connection

    async def disconnect(
        self,
        user_id: str,
        provider_name: str,
    ) -> UserConnection:
        """Disconnect a provider for a user.

        If a ComposioClient is available, revokes the connected account
        on Composio first, then updates the local DB.
        """
        if self._composio is not None:
            try:
                connections = await self._composio.list_connections(user_id)
                for conn in connections:
                    raw_name = (
                        conn.get("appName", "")
                        or conn.get("app_name", "")
                        or conn.get("toolkit", {}).get("slug", "")
                    )
                    app_name = _normalize_provider(raw_name) if raw_name else ""
                    if app_name == _normalize_provider(provider_name):
                        account_id = conn.get("id") or conn.get("connectedAccountId")
                        if account_id:
                            await self._composio.revoke_connection(str(account_id))
                            break
            except (ComposioApiError, Exception) as exc:
                logger.warning(
                    "composio_revoke_failed",
                    extra={
                        "user_id": user_id,
                        "provider_name": provider_name,
                        "error": str(exc),
                    },
                )

        result = await self._db.upsert_connection(
            user_id=user_id,
            provider_name=provider_name,
            is_connected=False,
            composio_entity_id=user_id,
        )
        await self._invalidate_cache(user_id)
        return result

    async def get_user_connections(
        self,
        user_id: str,
    ) -> list[UserConnection]:
        """Return all connection statuses for a user."""
        return await self._db.get_user_connections(user_id)

    async def is_user_connected(
        self,
        user_id: str,
        provider_name: str,
    ) -> bool:
        """Check if user has an active connection for a provider.

        Used by the intake layer to validate tool availability.
        """
        return await self._db.is_user_connected(user_id, _normalize_provider(provider_name))

    async def is_user_connected_cached(
        self,
        user_id: str,
        provider_name: str,
    ) -> bool:
        """Check connection status using cache, falling back to DB.

        Prefers the Redis cache populated by ``warm_connection_cache()``.
        On cache miss, falls back to a direct DB query.
        """
        normalized = _normalize_provider(provider_name)
        if self._cache is not None:
            cached = await self._cache.is_cached(user_id, normalized)
            if cached is not None:
                return cached

        return await self._db.is_user_connected(user_id, normalized)

    async def warm_connection_cache(self, user_id: str) -> None:
        """Sync connections from Composio (if available) and populate Redis cache.

        Called at session start so readiness checks avoid per-tool
        DB round-trips.  When a Composio client is configured, syncs
        from the Composio REST API first to pick up connections made
        outside our system (e.g. via Composio dashboard).
        """
        # Sync from Composio API first (picks up external connections)
        if self._composio is not None:
            try:
                await self.sync_connections(user_id)
            except Exception:
                logger.warning(
                    "composio_sync_failed_during_warmup",
                    extra={"user_id": user_id},
                )

        if self._cache is None:
            return

        connections = await self._db.get_user_connections(user_id)
        providers = {c.provider_name for c in connections if c.is_connected}
        await self._cache.set(user_id, providers)

        logger.info(
            "connection_cache_warmed",
            extra={
                "user_id": user_id,
                "provider_count": len(providers),
            },
        )

    async def sync_connections(self, user_id: str) -> list[UserConnection]:
        """Fetch connections from Composio REST API and sync to local DB.

        Used by ``warm_connection_cache()`` callers who want fresh data
        from Composio rather than relying solely on the local DB.

        Returns the updated list of connections.
        """
        if self._composio is None:
            return await self._db.get_user_connections(user_id)

        remote = await self._composio.list_connections(user_id)
        active_providers: set[str] = set()
        for conn in remote:
            raw_name = (
                conn.get("appName", "")
                or conn.get("app_name", "")
                or conn.get("toolkit", {}).get("slug", "")
            )
            app_name = _normalize_provider(raw_name) if raw_name else ""
            if app_name:
                active_providers.add(app_name)
                await self._db.upsert_connection(
                    user_id=user_id,
                    provider_name=app_name,
                    is_connected=True,
                    composio_entity_id=user_id,
                )

        # Mark providers that are no longer active
        local_connections = await self._db.get_user_connections(user_id)
        for lc in local_connections:
            if lc.is_connected and lc.provider_name not in active_providers:
                await self._db.upsert_connection(
                    user_id=user_id,
                    provider_name=lc.provider_name,
                    is_connected=False,
                    composio_entity_id=user_id,
                )

        await self._invalidate_cache(user_id)
        return await self._db.get_user_connections(user_id)

    async def _invalidate_cache(self, user_id: str) -> None:
        """Invalidate cached connections after a state change."""
        if self._cache is not None:
            await self._cache.invalidate(user_id)

    async def mark_connected(
        self,
        user_id: str,
        provider_name: str,
    ) -> UserConnection:
        """Directly mark a provider as connected (for testing/admin use)."""
        result = await self._db.upsert_connection(
            user_id=user_id,
            provider_name=provider_name,
            is_connected=True,
            composio_entity_id=user_id,
        )
        await self._invalidate_cache(user_id)
        return result

    # ------------------------------------------------------------------
    # System-level: Tool management (Composio MCP config)
    # ------------------------------------------------------------------

    async def create_integration(self, app_name: str) -> dict:
        """Create an OAuth integration for an app on Composio.

        This is a one-time setup per app. After creation, users can
        connect to the app via ``initiate_connection()``.

        Returns the integration dict including its ``id`` (auth_config_id).

        Raises:
            NotImplementedError: If Composio is not configured.
            ComposioApiError: On non-2xx Composio API response.
            ComposioUnreachableError: On network errors.
        """
        if self._composio_config is None or self._composio is None:
            raise NotImplementedError("Integration management requires COMPOSIO_API_KEY to be set.")

        logger.info("create_integration", extra={"app_name": app_name})
        result = await self._composio.create_integration(app_name)
        logger.info(
            "integration_created",
            extra={"app_name": app_name, "integration_id": result.get("id")},
        )
        return result

    async def list_apps(self) -> dict[str, list[str]]:
        """Return unique app names and their tools from the Composio MCP config.

        Composio tool names follow ``APPNAME_ACTION`` convention.
        This extracts the app prefix and groups tools under it.

        Returns:
            Dict mapping app name (lowercase) to list of tool names.
        """
        tools = await self.list_tools()
        apps: dict[str, list[str]] = {}
        for tool in tools:
            # GOOGLECALENDAR_CREATE_EVENT -> googlecalendar
            parts = tool.split("_", 1)
            app = parts[0].lower() if parts else tool.lower()
            apps.setdefault(app, []).append(tool)
        return apps

    async def list_tools(self) -> list[str]:
        """Return the allowed_tools list from the Composio MCP config.

        System-level operation — no user_id required.

        Raises:
            NotImplementedError: If Composio is not configured.
            ComposioApiError: On non-2xx Composio API response.
            ComposioUnreachableError: On network errors.
        """
        if self._composio_config is None or self._composio is None:
            raise NotImplementedError("Tool management requires COMPOSIO_API_KEY to be set.")

        mcp_config_id = getattr(self._composio_config, "mcp_config_id", "")
        config = await self._composio.get_mcp_config(mcp_config_id)
        return config.get("allowed_tools", [])

    async def add_tool(self, tool_name: str) -> None:
        """Add a tool to the MCP config's allowed_tools list.

        System-level operation — no user_id required.

        Raises:
            NotImplementedError: If Composio is not configured.
            ComposioApiError: On non-2xx Composio API response.
            ComposioUnreachableError: On network errors.
        """
        if self._composio_config is None or self._composio is None:
            raise NotImplementedError("Tool management requires COMPOSIO_API_KEY to be set.")

        mcp_config_id = getattr(self._composio_config, "mcp_config_id", "")
        logger.info(
            "add_tool",
            extra={"tool_name": tool_name, "mcp_config_id": mcp_config_id},
        )

        config = await self._composio.get_mcp_config(mcp_config_id)
        current_tools: list[str] = config.get("allowed_tools", [])

        if tool_name not in current_tools:
            current_tools.append(tool_name)
            await self._composio.update_allowed_tools(mcp_config_id, current_tools)

        logger.info(
            "tool_added",
            extra={"tool_name": tool_name, "mcp_config_id": mcp_config_id},
        )

    async def remove_tool(self, tool_name: str) -> None:
        """Remove a tool from the MCP config's allowed_tools list.

        System-level operation — no user_id required.

        Raises:
            NotImplementedError: If Composio is not configured.
            ComposioApiError: On non-2xx Composio API response.
            ComposioUnreachableError: On network errors.
        """
        if self._composio_config is None or self._composio is None:
            raise NotImplementedError("Tool management requires COMPOSIO_API_KEY to be set.")

        mcp_config_id = getattr(self._composio_config, "mcp_config_id", "")
        logger.info(
            "remove_tool",
            extra={"tool_name": tool_name, "mcp_config_id": mcp_config_id},
        )

        config = await self._composio.get_mcp_config(mcp_config_id)
        current_tools: list[str] = config.get("allowed_tools", [])

        if tool_name in current_tools:
            current_tools.remove(tool_name)
            await self._composio.update_allowed_tools(mcp_config_id, current_tools)

        logger.info(
            "tool_removed",
            extra={"tool_name": tool_name, "mcp_config_id": mcp_config_id},
        )
