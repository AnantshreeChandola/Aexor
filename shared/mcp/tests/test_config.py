"""Tests for shared.mcp.config — MCP server configuration loading."""

from __future__ import annotations

import pytest

from shared.mcp.config import (
    ComposioConfig,
    MCPConfigRegistry,
    MCPServerConfig,
    MCPServerNotConfiguredError,
    load_composio_config_from_env,
    load_mcp_config_from_env,
)


class TestMCPServerConfig:
    def test_defaults(self):
        cfg = MCPServerConfig(name="test", url="https://example.com")
        assert cfg.api_key == ""
        assert cfg.api_key_header == "x-api-key"
        assert cfg.extra_headers == {}
        assert cfg.entity_id_injection == "argument"
        assert cfg.entity_id_field == "entity_id"


class TestMCPConfigRegistry:
    def test_get_existing(self):
        cfg = MCPServerConfig(name="composio", url="https://example.com")
        registry = MCPConfigRegistry({"composio": cfg})
        assert registry.get("composio") is cfg

    def test_get_missing(self):
        registry = MCPConfigRegistry({})
        assert registry.get("nonexistent") is None

    def test_get_or_raise_existing(self):
        cfg = MCPServerConfig(name="composio", url="https://example.com")
        registry = MCPConfigRegistry({"composio": cfg})
        assert registry.get_or_raise("composio") is cfg

    def test_get_or_raise_missing(self):
        registry = MCPConfigRegistry({})
        with pytest.raises(MCPServerNotConfiguredError) as exc_info:
            registry.get_or_raise("missing_server")
        assert "missing_server" in str(exc_info.value)

    def test_list_servers(self):
        cfgs = {
            "a": MCPServerConfig(name="a", url="https://a.com"),
            "b": MCPServerConfig(name="b", url="https://b.com"),
        }
        registry = MCPConfigRegistry(cfgs)
        assert sorted(registry.list_servers()) == ["a", "b"]

    def test_len(self):
        registry = MCPConfigRegistry({})
        assert len(registry) == 0


class TestLoadMCPConfigFromEnv:
    def test_empty_env(self, monkeypatch):
        monkeypatch.delenv("MCP_SERVERS", raising=False)
        registry = load_mcp_config_from_env()
        assert len(registry) == 0

    def test_load_from_json_blob(self, monkeypatch):
        monkeypatch.setenv(
            "MCP_SERVERS",
            '{"composio": {"url": "https://backend.composio.dev/v3/mcp/abc", "api_key": "sk-123"}}',
        )
        registry = load_mcp_config_from_env()
        cfg = registry.get("composio")
        assert cfg is not None
        assert cfg.url == "https://backend.composio.dev/v3/mcp/abc"
        assert cfg.api_key == "sk-123"
        assert cfg.name == "composio"

    def test_load_from_individual_env_vars(self, monkeypatch):
        monkeypatch.delenv("MCP_SERVERS", raising=False)
        monkeypatch.setenv("MCP_SERVER_COMPOSIO_URL", "https://composio.dev/mcp")
        monkeypatch.setenv("MCP_SERVER_COMPOSIO_API_KEY", "sk-abc")
        registry = load_mcp_config_from_env()
        cfg = registry.get("composio")
        assert cfg is not None
        assert cfg.url == "https://composio.dev/mcp"
        assert cfg.api_key == "sk-abc"

    def test_individual_vars_override_json_blob(self, monkeypatch):
        monkeypatch.setenv(
            "MCP_SERVERS",
            '{"composio": {"url": "https://old.url", "api_key": "old-key"}}',
        )
        monkeypatch.setenv("MCP_SERVER_COMPOSIO_URL", "https://new.url")
        monkeypatch.setenv("MCP_SERVER_COMPOSIO_API_KEY", "new-key")
        registry = load_mcp_config_from_env()
        cfg = registry.get_or_raise("composio")
        assert cfg.url == "https://new.url"
        assert cfg.api_key == "new-key"

    def test_custom_entity_id_injection(self, monkeypatch):
        monkeypatch.delenv("MCP_SERVERS", raising=False)
        monkeypatch.setenv("MCP_SERVER_MYSERVER_URL", "https://my.server")
        monkeypatch.setenv("MCP_SERVER_MYSERVER_ENTITY_ID_INJECTION", "header")
        monkeypatch.setenv("MCP_SERVER_MYSERVER_ENTITY_ID_FIELD", "x-entity-id")
        registry = load_mcp_config_from_env()
        cfg = registry.get_or_raise("myserver")
        assert cfg.entity_id_injection == "header"
        assert cfg.entity_id_field == "x-entity-id"

    def test_custom_api_key_header(self, monkeypatch):
        monkeypatch.delenv("MCP_SERVERS", raising=False)
        monkeypatch.setenv("MCP_SERVER_SVC_URL", "https://svc.com")
        monkeypatch.setenv("MCP_SERVER_SVC_API_KEY_HEADER", "Authorization")
        registry = load_mcp_config_from_env()
        cfg = registry.get_or_raise("svc")
        assert cfg.api_key_header == "Authorization"

    def test_invalid_json_blob_ignored(self, monkeypatch):
        monkeypatch.setenv("MCP_SERVERS", "not-valid-json")
        registry = load_mcp_config_from_env()
        assert len(registry) == 0

    def test_server_name_lowercased(self, monkeypatch):
        monkeypatch.setenv(
            "MCP_SERVERS",
            '{"ComposiO": {"url": "https://example.com"}}',
        )
        registry = load_mcp_config_from_env()
        assert registry.get("composio") is not None
        assert registry.get("ComposiO") is None

    def test_extra_headers_from_env(self, monkeypatch):
        monkeypatch.delenv("MCP_SERVERS", raising=False)
        monkeypatch.setenv("MCP_SERVER_TEST_URL", "https://test.com")
        monkeypatch.setenv(
            "MCP_SERVER_TEST_EXTRA_HEADERS",
            '{"X-Custom": "value"}',
        )
        registry = load_mcp_config_from_env()
        cfg = registry.get_or_raise("test")
        assert cfg.extra_headers == {"X-Custom": "value"}


class TestComposioConfig:
    def test_defaults(self):
        cfg = ComposioConfig(api_key="sk-test", mcp_config_id="cfg-abc")
        assert cfg.user_url_cache_ttl == 3600
        assert cfg.auth_configs == {}
        assert cfg.system_user_id == "__system__"

    def test_custom_values(self):
        cfg = ComposioConfig(
            api_key="sk-test",
            mcp_config_id="cfg-abc",
            user_url_cache_ttl=600,
            auth_configs={"google_calendar": "ac_123"},
            system_user_id="admin",
        )
        assert cfg.user_url_cache_ttl == 600
        assert cfg.auth_configs == {"google_calendar": "ac_123"}
        assert cfg.system_user_id == "admin"

    def test_api_key_required(self):
        with pytest.raises(Exception):
            ComposioConfig(api_key="", mcp_config_id="cfg-abc")

    def test_mcp_config_id_required(self):
        with pytest.raises(Exception):
            ComposioConfig(api_key="sk-test", mcp_config_id="")


class TestLoadComposioConfigFromEnv:
    def test_returns_none_when_no_api_key(self, monkeypatch):
        monkeypatch.delenv("COMPOSIO_API_KEY", raising=False)
        monkeypatch.delenv("COMPOSIO_MCP_CONFIG_ID", raising=False)
        assert load_composio_config_from_env() is None

    def test_returns_none_when_empty_api_key(self, monkeypatch):
        monkeypatch.setenv("COMPOSIO_API_KEY", "  ")
        assert load_composio_config_from_env() is None

    def test_returns_none_when_no_config_id(self, monkeypatch):
        monkeypatch.setenv("COMPOSIO_API_KEY", "sk-test")
        monkeypatch.delenv("COMPOSIO_MCP_CONFIG_ID", raising=False)
        assert load_composio_config_from_env() is None

    def test_loads_minimal_config(self, monkeypatch):
        monkeypatch.setenv("COMPOSIO_API_KEY", "sk-test")
        monkeypatch.setenv("COMPOSIO_MCP_CONFIG_ID", "cfg-abc")
        monkeypatch.delenv("COMPOSIO_AUTH_CONFIGS", raising=False)
        monkeypatch.delenv("COMPOSIO_SYSTEM_USER_ID", raising=False)
        monkeypatch.delenv("COMPOSIO_URL_CACHE_TTL", raising=False)

        cfg = load_composio_config_from_env()
        assert cfg is not None
        assert cfg.api_key == "sk-test"
        assert cfg.mcp_config_id == "cfg-abc"
        assert cfg.auth_configs == {}
        assert cfg.system_user_id == "__system__"
        assert cfg.user_url_cache_ttl == 3600

    def test_loads_full_config(self, monkeypatch):
        monkeypatch.setenv("COMPOSIO_API_KEY", "sk-full")
        monkeypatch.setenv("COMPOSIO_MCP_CONFIG_ID", "cfg-full")
        monkeypatch.setenv(
            "COMPOSIO_AUTH_CONFIGS",
            '{"google_calendar": "ac_gcal", "gmail": "ac_gmail"}',
        )
        monkeypatch.setenv("COMPOSIO_SYSTEM_USER_ID", "platform-admin")
        monkeypatch.setenv("COMPOSIO_URL_CACHE_TTL", "1800")

        cfg = load_composio_config_from_env()
        assert cfg is not None
        assert cfg.api_key == "sk-full"
        assert cfg.mcp_config_id == "cfg-full"
        assert cfg.auth_configs == {"google_calendar": "ac_gcal", "gmail": "ac_gmail"}
        assert cfg.system_user_id == "platform-admin"
        assert cfg.user_url_cache_ttl == 1800

    def test_invalid_auth_configs_json_ignored(self, monkeypatch):
        monkeypatch.setenv("COMPOSIO_API_KEY", "sk-test")
        monkeypatch.setenv("COMPOSIO_MCP_CONFIG_ID", "cfg-abc")
        monkeypatch.setenv("COMPOSIO_AUTH_CONFIGS", "not-valid-json")

        cfg = load_composio_config_from_env()
        assert cfg is not None
        assert cfg.auth_configs == {}
