"""Tests for MCP registry environment scrubbing.

Verifies that _prepare_env() starts from a safe subset of os.environ
instead of the full environment, preventing host secret leakage to
MCP discovery subprocesses.
"""

from unittest.mock import patch

from ptc_agent.config.core import MCPServerConfig
from ptc_agent.core.mcp_registry import MCPServerConnector


class TestPrepareEnvSafety:
    """Verify _prepare_env() only forwards safe env vars."""

    def _make_connector(self, env: dict[str, str] | None = None) -> MCPServerConnector:
        config = MCPServerConfig(
            name="test-server",
            command="echo",
            args=["hello"],
            env=env or {},
        )
        return MCPServerConnector(config)

    def test_no_env_config_excludes_host_secrets(self):
        """MCP server with no env block should NOT inherit host secrets."""
        fake_environ = {
            "PATH": "/usr/bin",
            "HOME": "/home/user",
            "ANTHROPIC_API_KEY": "sk-ant-secret",
            "DB_PASSWORD": "supersecret",
            "BYOK_ENCRYPTION_KEY": "enckey123",
        }
        connector = self._make_connector(env={})
        with patch.dict("os.environ", fake_environ, clear=True):
            result = connector._prepare_env()

        assert "PATH" in result
        assert "HOME" in result
        assert "ANTHROPIC_API_KEY" not in result
        assert "DB_PASSWORD" not in result
        assert "BYOK_ENCRYPTION_KEY" not in result

    def test_empty_env_config_returns_safe_subset(self):
        """Empty env config (falsy) returns only safe vars."""
        connector = self._make_connector(env={})
        # MCPServerConfig with empty dict - config.env is truthy but empty
        # Force it to be falsy for the early return path
        connector.config.env = {}

        fake_environ = {
            "PATH": "/usr/bin",
            "HOME": "/home/user",
            "SHELL": "/bin/zsh",
            "SECRET_TOKEN": "tok_abc123",
        }
        with patch.dict("os.environ", fake_environ, clear=True):
            result = connector._prepare_env()

        assert result == {"PATH": "/usr/bin", "HOME": "/home/user", "SHELL": "/bin/zsh"}
        assert "SECRET_TOKEN" not in result

    def test_declared_env_vars_are_expanded(self):
        """Declared env vars with ${VAR} placeholders are resolved."""
        connector = self._make_connector(
            env={"FMP_API_KEY": "${FMP_API_KEY}"}
        )
        fake_environ = {
            "PATH": "/usr/bin",
            "FMP_API_KEY": "fmp_real_key_value",
        }
        with patch.dict("os.environ", fake_environ, clear=True):
            result = connector._prepare_env()

        assert result["PATH"] == "/usr/bin"
        assert result["FMP_API_KEY"] == "fmp_real_key_value"

    def test_literal_env_values_pass_through(self):
        """Literal (non-placeholder) env values are included as-is."""
        connector = self._make_connector(
            env={"MY_SETTING": "literal_value"}
        )
        fake_environ = {"PATH": "/usr/bin"}
        with patch.dict("os.environ", fake_environ, clear=True):
            result = connector._prepare_env()

        assert result["MY_SETTING"] == "literal_value"
        assert result["PATH"] == "/usr/bin"

    def test_safe_vars_forwarded(self):
        """All categories of safe vars are forwarded when present."""
        fake_environ = {
            # OS basics
            "PATH": "/usr/bin",
            "HOME": "/home/user",
            "USER": "testuser",
            "LANG": "en_US.UTF-8",
            # Temp
            "TMPDIR": "/tmp",
            # Node.js
            "NODE_PATH": "/usr/lib/node_modules",
            "NODE_ENV": "production",
            # Python
            "VIRTUAL_ENV": "/home/user/.venv",
            "PYTHONPATH": "/opt/lib",
            # XDG
            "XDG_CACHE_HOME": "/home/user/.cache",
            # Should NOT appear
            "AWS_SECRET_ACCESS_KEY": "wJalrXUtnFEMI",
            "OPENAI_API_KEY": "sk-openai-secret",
        }
        connector = self._make_connector(env={})
        with patch.dict("os.environ", fake_environ, clear=True):
            result = connector._prepare_env()

        assert result["PATH"] == "/usr/bin"
        assert result["NODE_PATH"] == "/usr/lib/node_modules"
        assert result["VIRTUAL_ENV"] == "/home/user/.venv"
        assert result["XDG_CACHE_HOME"] == "/home/user/.cache"
        assert "AWS_SECRET_ACCESS_KEY" not in result
        assert "OPENAI_API_KEY" not in result

    def test_declared_env_overrides_safe_var(self):
        """Declared env vars can override safe vars (e.g., custom PATH)."""
        connector = self._make_connector(
            env={"PATH": "/custom/bin:/usr/bin"}
        )
        fake_environ = {"PATH": "/usr/bin"}
        with patch.dict("os.environ", fake_environ, clear=True):
            result = connector._prepare_env()

        assert result["PATH"] == "/custom/bin:/usr/bin"

    def test_missing_safe_vars_are_skipped(self):
        """Safe vars not in os.environ are simply absent, no KeyError."""
        connector = self._make_connector(env={})
        fake_environ = {"PATH": "/usr/bin"}  # Only PATH, no HOME etc.
        with patch.dict("os.environ", fake_environ, clear=True):
            result = connector._prepare_env()

        assert result == {"PATH": "/usr/bin"}
