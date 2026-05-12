"""Transport-neutral shell operation orchestration.

Modules in this package own business write flows shared by REST and MCP
without constructing transport-specific exceptions or response envelopes.
"""

from tasca.shell.services.operations.batch_delete import (
    BatchDeleteOperationResult,
    delete_tables_batch,
)
from tasca.shell.services.operations.patron_registration import (
    PatronCreateError,
    PatronLookupError,
    PatronRegistrationError,
    PatronRegistrationOutcome,
    register_patron,
)
from tasca.shell.services.operations.table_control import (
    TableControlAction,
    TableControlErrorCode,
    TableControlOperationError,
    TableControlOutcome,
    build_control_content,
    execute_table_control,
    normalize_control_action,
)
from tasca.shell.services.operations.table_creation import (
    TableCreateError,
    TableCreationError,
    TableCreationOutcome,
    TableIdSelectionError,
    create_discussion_table,
)
from tasca.shell.services.operations.table_export import (
    TableExportOperationResult,
    export_table,
)

__all__ = [
    "BatchDeleteOperationResult",
    "PatronCreateError",
    "PatronLookupError",
    "PatronRegistrationError",
    "PatronRegistrationOutcome",
    "TableCreateError",
    "TableCreationError",
    "TableCreationOutcome",
    "TableControlAction",
    "TableControlErrorCode",
    "TableControlOperationError",
    "TableControlOutcome",
    "TableExportOperationResult",
    "TableIdSelectionError",
    "build_control_content",
    "create_discussion_table",
    "delete_tables_batch",
    "execute_table_control",
    "export_table",
    "normalize_control_action",
    "register_patron",
]
