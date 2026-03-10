"""Top-level `invar` console entrypoint with module-path guidance."""

from __future__ import annotations


def _missing_tasca_guidance(error: ModuleNotFoundError) -> str:
    """Build actionable guidance for missing tasca module startup failures."""

    missing_name = error.name if error.name is not None else "<unknown>"
    return (
        "Unable to start direct `invar` entrypoint: missing `tasca` module path.\n"
        f"Missing module: {missing_name}\n"
        "\n"
        "Use a supported invocation from repository root:\n"
        "  - uv run --group dev invar guard --all\n"
        "  - uv run --group dev invar guard <path>\n"
        "  - uvx invar-tools guard --all"
    )


def main() -> None:
    """Delegate to tasca shell entrypoint with import-path guardrails."""

    try:
        from tasca.shell.invar_entrypoint import main as delegate_main
    except ModuleNotFoundError as error:
        if error.name in {"tasca", "tasca.shell", "tasca.shell.invar_entrypoint"}:
            raise SystemExit(_missing_tasca_guidance(error)) from error
        raise

    delegate_main()
