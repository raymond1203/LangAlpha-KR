"""Unit tests for Docker build-context helpers.

Covers `_build_tar_context` and `_load_dockerignore` added for aiodocker
0.26+ compatibility — `images.build()` no longer accepts `path`/`dockerfile`,
so build context must be packaged as a tar BytesIO and passed via `fileobj`.
"""

from __future__ import annotations

import io
import tarfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ptc_agent.config.core import DockerConfig
from ptc_agent.core.sandbox.providers.docker import (
    DockerProvider,
    _build_tar_context,
    _load_dockerignore,
)


def _write(path: str | Path, content: str = "x") -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(content, encoding="utf-8")


def _tar_names(buf: io.BytesIO) -> list[str]:
    buf.seek(0)
    with tarfile.open(fileobj=buf, mode="r:gz") as t:
        return sorted(t.getnames())


class TestLoadDockerignore:
    def test_returns_none_when_file_missing(self, tmp_path: Path) -> None:
        assert _load_dockerignore(str(tmp_path)) is None

    def test_returns_pathspec_when_file_present(self, tmp_path: Path) -> None:
        (tmp_path / ".dockerignore").write_text("*.pyc\nnode_modules/\n")
        spec = _load_dockerignore(str(tmp_path))
        assert spec is not None
        assert spec.match_file("foo.pyc")
        assert spec.match_file("node_modules/")
        assert not spec.match_file("foo.py")

    def test_ignores_blank_lines_and_comments(self, tmp_path: Path) -> None:
        (tmp_path / ".dockerignore").write_text("# header\n\n*.log\n")
        spec = _load_dockerignore(str(tmp_path))
        assert spec is not None
        assert spec.match_file("server.log")


class TestBuildTarContext:
    def test_includes_all_files_when_no_dockerignore(self, tmp_path: Path) -> None:
        _write(tmp_path / "Dockerfile.sandbox", "FROM scratch")
        _write(tmp_path / "main.py", "print('hi')")
        _write(tmp_path / "sub" / "nested.txt", "n")

        buf = _build_tar_context(str(tmp_path))

        names = _tar_names(buf)
        assert "Dockerfile.sandbox" in names
        assert "main.py" in names
        assert "sub/nested.txt" in names

    def test_excludes_files_matching_dockerignore(self, tmp_path: Path) -> None:
        _write(tmp_path / "Dockerfile.sandbox", "FROM scratch")
        _write(tmp_path / "kept.py", "kept")
        _write(tmp_path / "secret.env", "SECRET=1")
        _write(tmp_path / "build.log", "log")
        _write(tmp_path / ".dockerignore", "*.env\n*.log\n")

        buf = _build_tar_context(str(tmp_path))

        names = _tar_names(buf)
        assert "Dockerfile.sandbox" in names
        assert "kept.py" in names
        assert "secret.env" not in names
        assert "build.log" not in names

    def test_excludes_directories_and_skips_walking_into_them(
        self, tmp_path: Path
    ) -> None:
        _write(tmp_path / "Dockerfile.sandbox", "FROM scratch")
        _write(tmp_path / ".venv" / "lib" / "huge.bin", "x" * 10_000)
        _write(tmp_path / ".git" / "HEAD", "ref")
        _write(tmp_path / "node_modules" / "pkg" / "index.js", "j")
        _write(tmp_path / "src" / "app.py", "code")
        _write(tmp_path / ".dockerignore", ".venv/\n.git/\nnode_modules/\n")

        buf = _build_tar_context(str(tmp_path))

        names = _tar_names(buf)
        assert "Dockerfile.sandbox" in names
        assert "src/app.py" in names
        assert not any(n.startswith(".venv") for n in names)
        assert not any(n.startswith(".git") for n in names)
        assert not any(n.startswith("node_modules") for n in names)

    def test_supports_nested_glob_patterns(self, tmp_path: Path) -> None:
        _write(tmp_path / "Dockerfile.sandbox", "FROM scratch")
        _write(tmp_path / "web" / "src" / "comp" / "__tests__" / "x.test.ts", "t")
        _write(tmp_path / "web" / "src" / "comp" / "Comp.ts", "c")
        _write(tmp_path / ".dockerignore", "web/src/**/__tests__/\n")

        buf = _build_tar_context(str(tmp_path))

        names = _tar_names(buf)
        assert "web/src/comp/Comp.ts" in names
        assert not any("__tests__" in n for n in names)

    def test_returns_bytesio_seekable_at_zero(self, tmp_path: Path) -> None:
        _write(tmp_path / "Dockerfile.sandbox", "FROM scratch")
        buf = _build_tar_context(str(tmp_path))
        assert isinstance(buf, io.BytesIO)
        assert buf.tell() == 0  # ready for aiodocker to read

    def test_produces_valid_gzip_tar(self, tmp_path: Path) -> None:
        _write(tmp_path / "Dockerfile.sandbox", "FROM scratch\nCMD echo hi\n")
        buf = _build_tar_context(str(tmp_path))
        # Round-trip: extract Dockerfile.sandbox and check content
        with tarfile.open(fileobj=buf, mode="r:gz") as t:
            member = t.extractfile("Dockerfile.sandbox")
            assert member is not None
            assert member.read().decode("utf-8") == "FROM scratch\nCMD echo hi\n"


class TestEnsureImageBuildSignature:
    """Verify `_ensure_image` calls aiodocker with the new (0.26+) signature."""

    @pytest.mark.asyncio
    async def test_build_called_with_fileobj_and_path_dockerfile(
        self, tmp_path: Path
    ) -> None:
        # Arrange: create a fake build context with Dockerfile.sandbox
        dockerfile = tmp_path / "Dockerfile.sandbox"
        dockerfile.write_text("FROM scratch\n")

        config = DockerConfig(image="langalpha-sandbox:test")
        provider = DockerProvider(config)

        client = MagicMock()
        # images.inspect raises so _ensure_image takes the build path
        client.images.inspect = AsyncMock(side_effect=Exception("not found"))

        # images.build returns an async iterator yielding stream lines
        async def fake_build_iter(*args, **kwargs):
            yield {"stream": "Step 1/1 : FROM scratch\n"}

        # MagicMock cannot be an async iterator directly; wrap.
        captured: dict[str, object] = {}

        def build_mock(*args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs
            return fake_build_iter()

        client.images.build = build_mock

        # Force search path to find our tmp Dockerfile.sandbox
        with patch(
            "ptc_agent.core.sandbox.providers.docker.os.getcwd",
            return_value=str(tmp_path),
        ):
            await provider._ensure_image(client)

        kwargs = captured["kwargs"]
        # New signature: fileobj + path_dockerfile (NOT path/dockerfile)
        assert "fileobj" in kwargs
        assert isinstance(kwargs["fileobj"], io.BytesIO)
        assert kwargs.get("path_dockerfile") == "Dockerfile.sandbox"
        assert kwargs.get("tag") == "langalpha-sandbox:test"
        assert kwargs.get("rm") is True
        assert kwargs.get("encoding") == "gzip"
        # stream=True is required — without it aiodocker returns a coroutine
        # rather than an async iterator, breaking the `async for` loop.
        assert kwargs.get("stream") is True
        # Old args must be absent
        assert "path" not in kwargs
        assert "dockerfile" not in kwargs
