"""
MCP response envelope helpers (shell re-exports).

This module re-exports pure response builders from core for backward compatibility.
The actual implementations are in tasca.core.mcp_response with @pre/@post contracts.
"""

from __future__ import annotations

from tasca.core.mcp_response import error_response, success_response

__all__ = ["error_response", "success_response"]
