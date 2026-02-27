/**
 * Tables API client.
 *
 * Types and functions for table-related API operations.
 * Types sourced from backend: src/tasca/core/domain/table.py
 * Search types sourced from: src/tasca/shell/api/routes/search.py
 */

import { apiClient, ApiError } from './client'

const API_BASE = '/api/v1'

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

/** Data for updating a table (full replace semantics).
 *
 * All fields are REQUIRED - caller must provide all updatable fields.
 * For context:
 * - Provide string value to set/update context
 * - Provide null to explicitly clear context
 * - Omitting context is NOT allowed (prevents accidental clearing)
 */
export interface TableUpdate {
  question: string
  context: string | null
  status: TableStatus
}

/** Response model for table control operations. Mirrors backend ControlResponse (flat). */
export interface ControlResponse {
  table_status: TableStatus
  control_saying_sequence: number
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

/** 
 * Fetch a single table by ID. Backend endpoint: GET /tables/{tableId}
 *
 * @example
 * ```typescript
 * const table = await getTable('550e8400-e29b-41d4-a716-446655440000')
 * console.log(table.question, table.status)
 * ```
 */
export function getTable(tableId: string): Promise<Table> {
  return apiClient<Table>(`/tables/${tableId}`)
}

/**
 * Fetch all tables, ordered by creation date (newest first).
 *
 * @example
 * ```typescript
 * const tables = await listTables()
 * tables.forEach(t => console.log(t.question, t.status))
 * ```
 */
export function listTables(): Promise<Table[]> {
  return apiClient<Table[]>('/tables')
}

/**
 * Search tables via the /search endpoint.
 *
 * @param q - Search query string (required, min 1 char)
 * @param status - Optional table status filter
 *
 * @example
 * ```typescript
 * // Search all tables
 * const results = await searchTables('project planning')
 *
 * // Search only open tables
 * const openResults = await searchTables('project', 'open')
 * ```
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

/**
 * Build export URL for table transcript downloads.
 *
 * @example
 * ```typescript
 * const markdownUrl = getExportUrl(tableId, 'markdown')
 * window.open(markdownUrl, '_blank')
 *
 * const jsonUrl = getExportUrl(tableId, 'jsonl')
 * ```
 */
export function getExportUrl(tableId: string, format: string): string {
  const encodedTableId = encodeURIComponent(tableId)
  const encodedFormat = encodeURIComponent(format)
  return `${API_BASE}/tables/${encodedTableId}/export/${encodedFormat}?download=true`
}

// =============================================================================
// Batch Operations
// =============================================================================

/** Response model for batch delete. Mirrors backend BatchDeleteResponse. */
export interface BatchDeleteResponse {
  deleted_ids: string[]
}

/**
 * Batch delete closed tables.
 *
 * All-or-nothing: if any table is not found or not closed,
 * the entire batch is rejected (409 with per-ID details).
 *
 * @param ids - Table IDs to delete (all must be in 'closed' status)
 * @returns Deleted table IDs on success
 * @throws ApiError 409 if any table is not closed or not found
 * @throws AuthError if admin token is missing or invalid
 *
 * @example
 * ```typescript
 * const result = await batchDeleteTables(['id1', 'id2'])
 * console.log(result.deleted_ids) // ['id1', 'id2']
 * ```
 */
export function batchDeleteTables(ids: string[]): Promise<BatchDeleteResponse> {
  return apiClient<BatchDeleteResponse>('/tables/actions/batch-delete', {
    method: 'POST',
    body: JSON.stringify({ ids }),
  })
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
 *
 * @example
 * ```typescript
 * try {
 *   const updated = await updateTable(tableId, {
 *     question: 'New question',
 *     context: 'Updated context',
 *     status: 'open'
 *   }, table.version)
 * } catch (e) {
 *   if (e instanceof ApiError && e.status === 409) {
 *     console.log('Version conflict - refresh and retry')
 *   }
 * }
 * ```
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
 * Control table lifecycle state via control endpoint.
 *
 * @param tableId - The table ID to control
 * @param action - Lifecycle action
 * @param speakerName - Actor display name
 * @param reason - Optional reason for audit trail
 *
 * @example
 * ```typescript
 * const result = await controlTable(tableId, 'pause', 'human', 'Taking a break')
 * console.log(result.table_status) // 'paused'
 * ```
 */
export function controlTable(
  tableId: string,
  action: 'pause' | 'resume' | 'close',
  speakerName: string,
  reason?: string
): Promise<ControlResponse> {
  return apiClient<ControlResponse>(`/tables/${tableId}/control`, {
    method: 'POST',
    body: JSON.stringify({
      action,
      speaker_name: speakerName,
      reason,
    }),
  })
}

/**
 * Pause a table (prevents new joins, allows sayings).
 *
 * @param table - Current table state
 * @returns Updated table
 *
 * @example
 * ```typescript
 * const paused = await pauseTable(table)
 * console.log(paused.status) // 'paused'
 * ```
 */
export function pauseTable(table: Table): Promise<Table> {
  return controlTable(table.id, 'pause', 'human').then(() => getTable(table.id))
}

/**
 * Resume a paused table.
 *
 * @param table - Current table state
 * @returns Updated table
 *
 * @example
 * ```typescript
 * const resumed = await resumeTable(pausedTable)
 * console.log(resumed.status) // 'open'
 * ```
 */
export function resumeTable(table: Table): Promise<Table> {
  return controlTable(table.id, 'resume', 'human').then(() => getTable(table.id))
}

/**
 * Close a table (terminal state, no operations allowed).
 *
 * @param table - Current table state
 * @returns Updated table
 *
 * @example
 * ```typescript
 * const closed = await closeTable(table)
 * console.log(closed.status) // 'closed'
 * ```
 */
export function closeTable(table: Table): Promise<Table> {
  return controlTable(table.id, 'close', 'human').then(() => getTable(table.id))
}
