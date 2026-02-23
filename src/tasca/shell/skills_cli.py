"""
Skills CLI commands for the `tasca skills` subcommand group.

Provides list, show, and install commands for bundled agent skills.
All file access uses importlib.resources — no hardcoded paths.
"""

from __future__ import annotations

import argparse
import sys


# @invar:allow shell_result: CLI entry points return exit codes, not Result[T, E]
def cmd_skills_list(_args: argparse.Namespace) -> int:
    """List all bundled skill names.

    Enumerates subdirectories of tasca.skills that contain a SKILL.md file,
    printing one name per line to stdout.

    Args:
        _args: Parsed command-line arguments (unused).

    Returns:
        Exit code (0 for success).
    """
    import importlib.resources

    skills_pkg = importlib.resources.files("tasca.skills")
    for entry in skills_pkg.iterdir():
        try:
            skill_md = entry / "SKILL.md"
            skill_md.read_text(encoding="utf-8")
        except (FileNotFoundError, NotADirectoryError, IsADirectoryError, AttributeError, OSError):
            continue
        print(entry.name)
    return 0


# @invar:allow shell_result: CLI entry points return exit codes, not Result[T, E]
def cmd_skills_show(args: argparse.Namespace) -> int:
    """Print the SKILL.md content for a named skill to stdout.

    Uses importlib.resources to read the bundled skill file. If the skill
    name is not found, prints an error to stderr and returns 1.

    Args:
        args: Parsed command-line arguments (args.name).

    Returns:
        Exit code (0 for success, 1 if skill not found).
    """
    import importlib.resources

    try:
        skill_md = importlib.resources.files("tasca.skills") / args.name / "SKILL.md"
        content = skill_md.read_text(encoding="utf-8")
        print(content, end="")
        return 0
    except FileNotFoundError:
        print(f"Error: skill '{args.name}' not found", file=sys.stderr)
        return 1


# @invar:allow shell_result: CLI entry points return exit codes, not Result[T, E]
def cmd_skills_install(args: argparse.Namespace) -> int:
    """Install a bundled skill's SKILL.md to a user-specified target directory.

    Reads the skill via importlib.resources and writes it to
    <target>/<name>/SKILL.md. Creates the target directory if it does not
    exist. The --target argument is required (enforced by argparse).

    Args:
        args: Parsed command-line arguments (args.name, args.target).

    Returns:
        Exit code (0 for success, 1 on error).
    """
    import importlib.resources
    import pathlib

    try:
        skill_md = importlib.resources.files("tasca.skills") / args.name / "SKILL.md"
        content = skill_md.read_text(encoding="utf-8")
    except FileNotFoundError:
        print(f"Error: skill '{args.name}' not found", file=sys.stderr)
        return 1

    dest_dir = pathlib.Path(args.target) / args.name
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_file = dest_dir / "SKILL.md"
        dest_file.write_text(content, encoding="utf-8")
        print(f"Installed: {args.name} -> {dest_file}")
        return 0
    except OSError as e:
        print(f"Error: cannot write to '{args.target}': {e}", file=sys.stderr)
        return 1
