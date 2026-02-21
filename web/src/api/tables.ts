/**
 * Tables API client.
 *
 * Types and functions for table-related API operations.
 * Types sourced from backend: src/tasca/core/domain/table.py
 * Search types sourced from: src/tasca/shell/api/routes/search.py
 */

import { apiClient, ApiError } from './client'

// =============================================================================
// Domain Types (mirror backend Table pydantic model)
// =============================================================================

/** Table status enum — mirrors backend TableStatus. */
export type TableStatus = 'open' | 'paused' | 'closed'

/** A discussion table where agents collaborate. */
export interface Table {
  /** Unique identifier (UUID) */
  id: string
  /** The question or topic for discussion */
  question: string
  /** Optional context for the discussion */
  context: string | null
  /** Current status */
  status: TableStatus
  /** Version number for optimistic concurrency */
  version: number
  /** ISO timestamp — when the table was created */
  created_at: string
  /** ISO timestamp — when the table was last updated */
  updated_at: string
}

/** Data for updating a table (full replace semantics). */
export interface TableUpdate {
  question: string
  context: string | null
  status: TableStatus
}

/** Version conflict error from optimistic concurrency. */
export interface VersionConflictError {
  message: string
  expected_version: number
  actual_version: number
}

// =============================================================================
// Search Types (mirror backend SearchResponse / SearchHit)
// =============================================================================

/** A single search hit representing a matching table. */
export interface SearchHit {
  table_id: string
  question: string
  status: string
  snippet: string
  match_type: string
  created_at: string
  updated_at: string
}

/** Response model for the search endpoint. */
export interface SearchResponse {
  query: string
  total: number
  hits: SearchHit[]
}

// =============================================================================
// API Functions
// =============================================================================

/** Fetch a single table by ID. Backend endpoint: GET /tables/{tableId} */
export function getTable(tableId: string): Promise<Table> {
  return apiClient<Table>(`/tables/${tableId}`)
}

/** Fetch all tables, ordered by creation date (newest first). */
export function listTables(): Promise<Table[]> {
  return apiClient<Table[]>('/tables')
}

/**
 * Search tables via the /search endpoint.
 *
 * @param q - Search query string (required, min 1 char)
 * @param status - Optional table status filter
 */
export function searchTables(
  q: string,
  status?: TableStatus
): Promise<SearchResponse> {
  const params = new URLSearchParams({ q })
  if (status) {
    params.set('status', status)
  }
  return apiClient<SearchResponse>(`/search?${params.toString()}`)
}

// =============================================================================
// Update Operations
// =============================================================================

/**
 * Update a table with optimistic concurrency control.
 *
 * Requires admin authentication. Uses full replace semantics.
 *
 * @param tableId - The table ID to update
 * @param data - Full replacement data
 * @param expectedVersion - The version the client expects (optimistic concurrency)
 * @returns The updated table with incremented version
 * @throws AuthError if admin token is missing or invalid
 * @throws ApiError with version conflict details if version mismatch (status 409)
 */
export async function updateTable(
  tableId: string,
  data: TableUpdate,
  expectedVersion: number
): Promise<Table> {
  const params = new URLSearchParams({
    expected_version: String(expectedVersion),
  })

  try {
    return await apiClient<Table>(`/tables/${tableId}?${params.toString()}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    })
  } catch (error) {
    // Re-throw with version conflict info if applicable
    if (error instanceof ApiError && error.status === 409) {
      // The error message already contains the detail from the server
      throw error
    }
    throw error
  }
}

/**
 * Pause a table (prevents new joins, allows sayings).
 *
 * @param table - Current table state
 * @returns Updated table
 */
export function pauseTable(table: Table): Promise<Table> {
  return updateTable(table.id, {
    question: table.question,
    context: table.context,
    status: 'paused',
  }, table.version)
}

/**
 * Resume a paused table.
 *
 * @param table - Current table state
 * @returns Updated table
 */
export function resumeTable(table: Table): Promise<Table> {
  return updateTable(table.id, {
    question: table.question,
    context: table.context,
    status: 'open',
  }, table.version)
}

/**
 * Close a table (terminal state, no operations allowed).
 *
 * @param table - Current table state
 * @returns Updated table
 */
export function closeTable(table: Table): Promise<Table> {
  return updateTable(table.id, {
    question: table.question,
    context: table.context,
    status: 'closed',
  }, table.version)
}
