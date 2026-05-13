"""
Skills CLI commands for the `tasca skills` subcommand group.

Provides list, show, and install commands for bundled agent skills.
All file access uses importlib.resources — no hardcoded paths.
"""

from __future__ import annotations

import argparse
import importlib.resources
import pathlib

from returns.result import Failure, Result, Success


def cmd_skills_list(_args: argparse.Namespace) -> Result[int, str]:
    """List all bundled skill names.

    Enumerates subdirectories of tasca.skills that contain a SKILL.md file,
    printing one name per line to stdout.

    Args:
        _args: Parsed command-line arguments (unused).

    Returns:
        Success(0) for success, or Failure with a printable error.
    """
    try:
        skills_pkg = importlib.resources.files("tasca.skills")
    except (ModuleNotFoundError, OSError) as exc:
        return Failure(f"Error: cannot list bundled skills: {exc}")
    for entry in skills_pkg.iterdir():
        try:
            skill_md = entry / "SKILL.md"
            skill_md.read_text(encoding="utf-8")
        except (FileNotFoundError, NotADirectoryError, IsADirectoryError, AttributeError, OSError):
            continue
        print(entry.name)
    return Success(0)


def cmd_skills_show(args: argparse.Namespace) -> Result[int, str]:
    """Print the SKILL.md content for a named skill to stdout.

    Uses importlib.resources to read the bundled skill file. If the skill
    name is not found, prints an error to stderr and returns 1.

    Args:
        args: Parsed command-line arguments (args.name).

    Returns:
        Success(0) for success, or Failure with a printable error.
    """
    try:
        skill_md = importlib.resources.files("tasca.skills") / args.name / "SKILL.md"
        content = skill_md.read_text(encoding="utf-8")
        print(content, end="")
        return Success(0)
    except FileNotFoundError:
        return Failure(f"Error: skill '{args.name}' not found")


def cmd_skills_install(args: argparse.Namespace) -> Result[int, str]:
    """Install a bundled skill's SKILL.md to a user-specified target directory.

    Reads the skill via importlib.resources and writes it to
    <target>/<name>/SKILL.md. Creates the target directory if it does not
    exist. The --target argument is required (enforced by argparse).

    Args:
        args: Parsed command-line arguments (args.name, args.target).

    Returns:
        Success(0) for success, or Failure with a printable error.
    """
    try:
        skill_md = importlib.resources.files("tasca.skills") / args.name / "SKILL.md"
        content = skill_md.read_text(encoding="utf-8")
    except FileNotFoundError:
        return Failure(f"Error: skill '{args.name}' not found")

    dest_dir = pathlib.Path(args.target) / args.name
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_file = dest_dir / "SKILL.md"
        dest_file.write_text(content, encoding="utf-8")
        print(f"Installed: {args.name} -> {dest_file}")
        return Success(0)
    except OSError as e:
        return Failure(f"Error: cannot write to '{args.target}': {e}")
