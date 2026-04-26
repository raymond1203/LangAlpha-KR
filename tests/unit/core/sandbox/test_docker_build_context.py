"""Unit tests for Docker build-context helpers.

Covers `_build_tar_context` and `_load_dockerignore` added for aiodocker
0.26+ compatibility — `images.build()` no longer accepts `path`/`dockerfile`,
so build context must be packaged as a tar BytesIO and passed via `fileobj`.
"""

from __future__ import annotations

import io
import os
import tarfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from ptc_agent.config.core import DockerConfig
from ptc_agent.core.sandbox.providers.docker import (
    _DEFAULT_TAR_EXCLUDES,
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


class TestSafetyExcludes:
    """Default excludes when .dockerignore is missing — guards against packing
    the entire repo (e.g., 1.3GB .venv) by accident."""

    def test_default_excludes_applied_when_dockerignore_missing(
        self, tmp_path: Path
    ) -> None:
        _write(tmp_path / "Dockerfile.sandbox", "FROM scratch")
        _write(tmp_path / ".venv" / "lib" / "huge.bin", "x" * 5000)
        _write(tmp_path / ".git" / "HEAD", "ref")
        _write(tmp_path / "node_modules" / "pkg" / "i.js", "j")
        _write(tmp_path / "src" / "app.py", "ok")
        # Note: no .dockerignore created

        buf = _build_tar_context(str(tmp_path))

        names = _tar_names(buf)
        assert "Dockerfile.sandbox" in names
        assert "src/app.py" in names
        assert not any(n.startswith(".venv") for n in names)
        assert not any(n.startswith(".git") for n in names)
        assert not any(n.startswith("node_modules") for n in names)

    def test_user_dockerignore_takes_precedence_over_defaults(
        self, tmp_path: Path
    ) -> None:
        # User explicitly wants .git in the context (unusual but allowed)
        _write(tmp_path / "Dockerfile.sandbox", "FROM scratch")
        _write(tmp_path / ".git" / "HEAD", "ref")
        _write(tmp_path / "secret.txt", "s")
        _write(tmp_path / ".dockerignore", "secret.txt\n")

        buf = _build_tar_context(str(tmp_path))

        names = _tar_names(buf)
        # .git included because user's dockerignore did NOT exclude it
        assert any(n.startswith(".git") for n in names)
        # User's explicit exclusion still works
        assert "secret.txt" not in names

    def test_default_excludes_constant_has_expected_entries(self) -> None:
        # Sanity: protect against accidental removal of critical excludes.
        for required in (".git/", ".venv/", "node_modules/", "__pycache__/"):
            assert required in _DEFAULT_TAR_EXCLUDES


class TestSizeCap:
    def test_raises_when_tar_exceeds_max_size(self, tmp_path: Path) -> None:
        _write(tmp_path / "Dockerfile.sandbox", "FROM scratch")
        # 100KB of incompressible random-ish bytes — defeats gzip
        _write(tmp_path / "big.bin", "".join(chr(((i * 1103515245) % 256) ^ 0x55) for i in range(100_000)))

        with pytest.raises(RuntimeError, match="too large"):
            _build_tar_context(str(tmp_path), max_size=1024)  # 1KB cap

    def test_does_not_raise_below_cap(self, tmp_path: Path) -> None:
        _write(tmp_path / "Dockerfile.sandbox", "FROM scratch")
        _build_tar_context(str(tmp_path), max_size=10 * 1024 * 1024)  # 10MB


class TestDeterministicOrder:
    def test_files_added_in_sorted_order(self, tmp_path: Path) -> None:
        _write(tmp_path / "Dockerfile.sandbox", "FROM scratch")
        # Create files in non-alphabetical order
        for name in ("z.txt", "a.txt", "m.txt"):
            _write(tmp_path / name, name)
        for name in ("zz", "aa", "mm"):
            _write(tmp_path / name / "f.txt", name)

        buf = _build_tar_context(str(tmp_path))

        # Order in archive must be sorted (lexicographic) at each level
        buf.seek(0)
        with tarfile.open(fileobj=buf, mode="r:gz") as t:
            members = [m.name for m in t.getmembers()]
        # Top-level files appear in sorted order
        top_files = [n for n in members if "/" not in n]
        assert top_files == sorted(top_files)


class TestSymlinkSafety:
    def test_skips_symlink_pointing_outside_build_dir(
        self, tmp_path: Path
    ) -> None:
        outside = tmp_path / "outside"
        outside.mkdir()
        outside_file = outside / "secret.key"
        outside_file.write_text("HOST_SECRET")

        build_dir = tmp_path / "build"
        build_dir.mkdir()
        _write(build_dir / "Dockerfile.sandbox", "FROM scratch")
        _write(build_dir / "kept.txt", "k")
        # Symlink pointing OUTSIDE build_dir
        os.symlink(str(outside_file), str(build_dir / "leaked.key"))

        buf = _build_tar_context(str(build_dir))

        names = _tar_names(buf)
        assert "Dockerfile.sandbox" in names
        assert "kept.txt" in names
        assert "leaked.key" not in names

    def test_includes_symlink_pointing_inside_build_dir(
        self, tmp_path: Path
    ) -> None:
        build_dir = tmp_path / "build"
        build_dir.mkdir()
        _write(build_dir / "Dockerfile.sandbox", "FROM scratch")
        _write(build_dir / "real.txt", "real")
        os.symlink(str(build_dir / "real.txt"), str(build_dir / "alias.txt"))

        buf = _build_tar_context(str(build_dir))

        names = _tar_names(buf)
        assert "alias.txt" in names

    def test_skips_dangling_symlink(self, tmp_path: Path) -> None:
        _write(tmp_path / "Dockerfile.sandbox", "FROM scratch")
        os.symlink(
            str(tmp_path / "does_not_exist.txt"),
            str(tmp_path / "broken.txt"),
        )

        buf = _build_tar_context(str(tmp_path))

        names = _tar_names(buf)
        assert "broken.txt" not in names


class TestEnsureImageBuildSignature:
    """Verify `_ensure_image` calls aiodocker with the new (0.26+) signature."""

    @pytest.mark.asyncio
    async def test_build_called_with_fileobj_and_path_dockerfile(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Arrange: fake build context with Dockerfile.sandbox in tmp_path.
        dockerfile = tmp_path / "Dockerfile.sandbox"
        dockerfile.write_text("FROM scratch\n")

        # Isolate _ensure_image's Dockerfile search.
        #
        # `_ensure_image` builds a `search_paths` list:
        #   [os.path.join(os.getcwd(), 'Dockerfile.sandbox'),
        #    + module_dir-based paths from os.path.abspath(__file__) walking
        #      up to 6 levels]
        #
        # The loop breaks on first match. We chdir to tmp_path so cwd-based
        # path is found first. Without this isolation, the module_dir walk
        # could discover the real repo's Dockerfile.sandbox, making the test
        # implicitly depend on repo layout.
        monkeypatch.chdir(tmp_path)

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
