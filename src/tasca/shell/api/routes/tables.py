"""
Tables API routes.

Endpoints for table management operations.
"""

from datetime import datetime

from fastapi import APIRouter

from tasca.core.domain.table import Table, TableCreate, TableId, TableStatus

router = APIRouter()


@router.post("", response_model=Table)
async def create_table(data: TableCreate) -> Table:
    """
    Create a new discussion table.

    Args:
        data: Table creation data with question and optional context.

    Returns:
        The created table.
    """
    now = datetime.now()
    return Table(
        id=TableId("placeholder-id"),
        question=data.question,
        context=data.context,
        status=TableStatus.OPEN,
        created_at=now,
        updated_at=now,
    )


@router.get("/{table_id}", response_model=Table)
async def get_table(table_id: str) -> Table:
    """
    Get a table by ID.

    Args:
        table_id: The table identifier.

    Returns:
        The requested table.
    """
    now = datetime.now()
    return Table(
        id=TableId(table_id),
        question="Placeholder question",
        context=None,
        status=TableStatus.OPEN,
        created_at=now,
        updated_at=now,
    )


@router.get("", response_model=list[Table])
async def list_tables() -> list[Table]:
    """
    List all tables.

    Returns:
        List of all tables.
    """
    return []


@router.delete("/{table_id}")
async def delete_table(table_id: str) -> dict[str, str]:
    """
    Delete a table by ID.

    Args:
        table_id: The table identifier.

    Returns:
        Confirmation message.
    """
    return {"status": "deleted", "table_id": table_id}
