"""Sicherheit: Origin-Prüfung, Datei-Sandbox, SSRF-Schutz, Secret-Redaction."""
from __future__ import annotations

import pytest

from jarvis.core.logging_setup import redact, register_secret
from jarvis.core.security import is_allowed_origin
from jarvis.tools.base import ToolError
from jarvis.tools.builtin.file_tools import resolve_safe_path
from jarvis.web.http import assert_public_url


def test_origin_check():
    assert is_allowed_origin(None, 8765)
    assert is_allowed_origin("http://127.0.0.1:8765", 8765)
    assert is_allowed_origin("http://localhost:8765", 8765)
    assert not is_allowed_origin("http://localhost:9999", 8765)
    assert not is_allowed_origin("https://evil.example", 8765)
    assert not is_allowed_origin("http://127.0.0.1.evil.example:8765", 8765)


def test_file_paths_restricted_to_allowed_roots(sandbox):
    inside = resolve_safe_path(str(sandbox / "notes.txt"))
    assert inside.parent == sandbox.resolve()
    with pytest.raises(ToolError):
        resolve_safe_path("/etc/passwd")
    with pytest.raises(ToolError):
        resolve_safe_path(str(sandbox / ".." / ".." / "etc" / "passwd"))


def test_sensitive_files_blocked(sandbox):
    (sandbox / ".ssh").mkdir(exist_ok=True)
    with pytest.raises(ToolError):
        resolve_safe_path(str(sandbox / ".ssh" / "id_rsa"))
    with pytest.raises(ToolError):
        resolve_safe_path(str(sandbox / ".env"))


def test_symlink_escape_blocked(sandbox, tmp_path):
    outside = tmp_path / "secret.txt"
    outside.write_text("x")
    link = sandbox / "link.txt"
    if link.exists() or link.is_symlink():
        link.unlink()
    link.symlink_to(outside)
    with pytest.raises(ToolError):
        resolve_safe_path(str(link))


@pytest.mark.parametrize("url", ["http://127.0.0.1/", "http://localhost:8765/api", "http://192.168.1.1/", "http://169.254.169.254/latest", "file:///etc/passwd", "ftp://example.com"])
async def test_ssrf_guard_blocks_local_targets(url):
    with pytest.raises(ToolError):
        await assert_public_url(url)


def test_redaction():
    register_secret("super-secret-value-123")
    text = redact("key=AIzaSyA1234567890abcdefghijklmnopqrstuv and super-secret-value-123 password=hunter2")
    assert "AIza" not in text
    assert "super-secret-value-123" not in text
    assert "hunter2" not in text
