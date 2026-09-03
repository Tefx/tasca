"""Tests for declarative MCP tool contract metadata."""

from __future__ import annotations

import inspect

from tasca.shell.mcp import server
from tasca.shell.mcp.tool_contracts import (
    DEFAULTS_SPEC_ANCHOR,
    ERROR_RESPONSE_SHAPE,
    ERROR_SHAPE_SPEC_ANCHOR,
    RECOMMENDED_DEFAULTS,
    SPEC_DOC,
    SPEC_PARAMETER_DEFAULTS,
    TOOL_CONTRACTS,
    TOOL_CONTRACTS_BY_NAME,
    parameter_field,
)

EXPECTED_SERVER_TOOLS = {
    "attachment_get",
    "patron_register",
    "patron_get",
    "table_create",
    "table_join",
    "table_get",
    "table_list",
    "table_delete_batch",
    "table_export",
    "table_say",
    "table_listen",
    "table_control",
    "table_update",
    "table_wait",
    "seat_heartbeat",
    "seat_list",
    "connect",
    "connection_status",
}


def test_tool_contracts_cover_current_mcp_server_surface() -> None:
    """Every public server tool has contract metadata for adoption wiring."""
    assert {contract.tool_name for contract in TOOL_CONTRACTS} == EXPECTED_SERVER_TOOLS
    assert set(TOOL_CONTRACTS_BY_NAME) == EXPECTED_SERVER_TOOLS


def test_tool_contracts_centralize_known_defaults() -> None:
    """Defaults now duplicated in server.py/entrypoints.py are represented once."""
    assert SPEC_DOC == "docs/tasca-mcp-interface-v0.1.md"
    assert DEFAULTS_SPEC_ANCHOR.endswith("recommended-defaults-v0-1")
    assert RECOMMENDED_DEFAULTS["table.join.history_limit"] == 10
    assert RECOMMENDED_DEFAULTS["table.join.history_max_bytes"] == 65536
    assert RECOMMENDED_DEFAULTS["table.listen.limit"] == 50
    assert RECOMMENDED_DEFAULTS["table.wait.limit"] == 50
    assert RECOMMENDED_DEFAULTS["table.wait.wait_ms"] == 10000
    assert RECOMMENDED_DEFAULTS["seat.heartbeat.ttl_ms"] == 60000
    assert RECOMMENDED_DEFAULTS["saying.content.max_bytes"] == 65536

    table_join_defaults = {
        parameter.name: parameter.default
        for parameter in TOOL_CONTRACTS_BY_NAME["table_join"].parameters
    }
    assert table_join_defaults["history_limit"] == 10
    assert table_join_defaults["history_max_bytes"] == 65536


def test_parameter_contract_defaults_match_spec_defaults() -> None:
    table_say_defaults = {
        parameter.name: parameter.default for parameter in TOOL_CONTRACTS_BY_NAME["table_say"].parameters
    }
    heartbeat_defaults = {
        parameter.name: parameter.default
        for parameter in TOOL_CONTRACTS_BY_NAME["seat_heartbeat"].parameters
    }

    assert SPEC_PARAMETER_DEFAULTS["table_say.saying_type"] == "text"
    assert table_say_defaults["saying_type"] == "text"
    assert table_say_defaults["mentions"] is None
    assert table_say_defaults["reply_to_sequence"] is None
    assert table_say_defaults["attachments"] is None
    assert SPEC_PARAMETER_DEFAULTS["seat_heartbeat.ttl_ms"] == 60000
    assert heartbeat_defaults["ttl_ms"] == 60000
    assert heartbeat_defaults["state"] == "running"


def test_table_create_contract_covers_spec_and_legacy_inputs() -> None:
    """table_create metadata exposes the v0.1 input surface plus aliases."""
    table_create_defaults = {
        parameter.name: parameter.default
        for parameter in TOOL_CONTRACTS_BY_NAME["table_create"].parameters
    }

    assert set(table_create_defaults) == {
        "title",
        "question",
        "context",
        "creator_patron_id",
        "created_by",
        "host_ids",
        "metadata",
        "policy",
        "board",
        "dedup_id",
    }
    assert all(default is None for default in table_create_defaults.values())


def test_optional_parameter_contract_defaults_match_server_signatures() -> None:
    """Every optional/defaulted contract parameter agrees with runtime defaults."""
    mismatches: list[tuple[str, str, object, object]] = []
    covered_defaults: list[str] = []

    for contract in TOOL_CONTRACTS:
        runtime = getattr(server, contract.tool_name)
        signature = inspect.signature(runtime)
        for parameter in contract.parameters:
            runtime_parameter = signature.parameters[parameter.name]
            runtime_default = runtime_parameter.default
            if parameter.required:
                assert runtime_default is inspect.Signature.empty, (
                    f"{contract.tool_name}.{parameter.name} is required in contract "
                    "but defaulted at runtime"
                )
                continue

            covered_defaults.append(f"{contract.tool_name}.{parameter.name}")
            if runtime_default != parameter.default:
                mismatches.append((
                    contract.tool_name,
                    parameter.name,
                    parameter.default,
                    runtime_default,
                ))

    assert "table_say.mentions" in covered_defaults
    assert "table_say.reply_to_sequence" in covered_defaults
    assert "table_create.title" in covered_defaults
    assert not mismatches


def test_runtime_field_metadata_is_built_from_tool_contracts() -> None:
    field = parameter_field("seat_heartbeat", "ttl_ms").unwrap()

    assert field.description == (
        "Time-to-live in ms before the seat expires (default 60000 = 60s)"
    )


def test_tool_contracts_centralize_parameter_documentation_and_anchors() -> None:
    """Parameter docs and source anchors are declarative metadata, not handlers."""
    for contract in TOOL_CONTRACTS:
        assert contract.summary
        assert contract.description
        assert contract.spec_name.startswith("tasca.")
        assert contract.runtime_entrypoint.startswith("tasca.shell.mcp.entrypoints.")
        assert contract.surface == "mcp_server_registration"
        assert contract.spec_anchor
        for parameter in contract.parameters:
            assert parameter.name
            assert parameter.description
            assert isinstance(parameter.required, bool)

    table_say_docs = {
        parameter.name: parameter.description
        for parameter in TOOL_CONTRACTS_BY_NAME["table_say"].parameters
    }
    assert "max 65536 bytes" in table_say_docs["content"]
    assert "aliases, display names, or 'all'" in table_say_docs["mentions"]


def test_error_response_shape_anchor_is_available() -> None:
    """The normative error envelope is available for future drift checks."""
    assert ERROR_SHAPE_SPEC_ANCHOR.endswith("error-response-shape")
    assert ERROR_RESPONSE_SHAPE == {
        "error": {
            "code": "ErrorCode",
            "message": "Human-readable message",
            "details": {},
        }
    }
