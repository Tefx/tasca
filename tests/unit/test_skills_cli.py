"""
Unit tests for `tasca skills` CLI subcommands.

Tests cover: list, show, and install commands via cli.main().
"""

from __future__ import annotations

import pytest

import tasca.cli as cli


# ---------------------------------------------------------------------------
# Group A — skills list
# ---------------------------------------------------------------------------


def test_skills_list_exits_zero() -> None:
    """skills list returns exit code 0."""
    result = cli.main(["skills", "list"])
    assert result == 0


def test_skills_list_includes_tasca_moderation(capsys: pytest.CaptureFixture[str]) -> None:
    """skills list output contains 'tasca-moderation'."""
    cli.main(["skills", "list"])
    captured = capsys.readouterr()
    assert "tasca-moderation" in captured.out


# ---------------------------------------------------------------------------
# Group B — skills show
# ---------------------------------------------------------------------------


def test_skills_show_exits_zero() -> None:
    """skills show tasca-moderation returns exit code 0."""
    result = cli.main(["skills", "show", "tasca-moderation"])
    assert result == 0


def test_skills_show_prints_content(capsys: pytest.CaptureFixture[str]) -> None:
    """skills show tasca-moderation prints non-empty markdown content."""
    cli.main(["skills", "show", "tasca-moderation"])
    captured = capsys.readouterr()
    assert len(captured.out) > 0
    # SKILL.md contains markdown headings or frontmatter
    assert "#" in captured.out or "---" in captured.out


def test_skills_show_unknown_skill_returns_nonzero() -> None:
    """skills show nonexistent-skill returns non-zero exit code."""
    result = cli.main(["skills", "show", "nonexistent-skill"])
    assert result != 0


# ---------------------------------------------------------------------------
# Group C — skills install
# ---------------------------------------------------------------------------


def test_skills_install_requires_target() -> None:
    """skills install without --target raises SystemExit with code 2."""
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["skills", "install", "tasca-moderation"])
    assert exc_info.value.code == 2


def test_skills_install_copies_skill_to_target(tmp_path: pytest.TempPathFactory) -> None:
    """skills install --target <path> copies SKILL.md and returns 0."""
    result = cli.main(["skills", "install", "--target", str(tmp_path), "tasca-moderation"])
    assert result == 0
    dest = tmp_path / "tasca-moderation" / "SKILL.md"
    assert dest.exists()
    assert dest.stat().st_size > 0


def test_skills_install_unknown_skill_returns_nonzero(tmp_path: pytest.TempPathFactory) -> None:
    """skills install with nonexistent skill name returns non-zero exit code."""
    result = cli.main(["skills", "install", "--target", str(tmp_path), "nonexistent-skill"])
    assert result != 0
