/**
 * Sayings & Seats API client.
 *
 * Types and functions for saying and seat operations.
 * Types sourced from backend:
 *   - src/tasca/core/domain/saying.py (Speaker, Saying)
 *   - src/tasca/core/domain/seat.py (Seat)
 *   - src/tasca/shell/api/routes/sayings.py (SayingListResponse)
 *   - src/tasca/shell/api/routes/seats.py (SeatListResponse)
 */

import { apiClient } from './client'

// =============================================================================
// Speaker Types (mirror backend Speaker / SpeakerKind)
// =============================================================================

/** Speaker kind enum — mirrors backend SpeakerKind. */
export type SpeakerKind = 'agent' | 'human' | 'patron'

/** Identifies who made a saying. */
export interface Speaker {
  /** Kind of speaker: agent, human, or patron */
  kind: SpeakerKind
  /** Display name of the speaker */
  name: string
  /** Patron ID if speaker is an AI agent; null for humans */
  patron_id: string | null
}

// =============================================================================
// Saying Types (mirror backend Saying model)
// =============================================================================

/** A single saying (message) in a discussion table. */
export interface Saying {
  /** Unique identifier (UUID) */
  id: string
  /** Table this saying belongs to */
  table_id: string
  /** Monotonically increasing sequence number within the table */
  sequence: number
  /** Who said it */
  speaker: Speaker
  /** Markdown content */
  content: string
  /** Whether this saying is pinned */
  pinned: boolean
  /** ISO timestamp — when the saying was created */
  created_at: string
  /** Sequence of the saying this is a reply to (null if not a reply) */
  reply_to?: number | null
  /** Unresolved @mention handles (present when mentions could not be resolved) */
  mentions_unresolved?: string[]
}

/** Request model for creating a new saying. */
export interface SayingCreate {
  /** Display name of the speaker */
  speaker_name: string
  /** Markdown content */
  content: string
  /** Patron ID if speaker is an AI agent; null for humans */
  patron_id?: string | null
}

/** Response model from GET /tables/{id}/sayings — mirrors backend SayingListResponse. */
export interface SayingListResponse {
  /** List of sayings ordered by sequence (ascending) */
  sayings: Saying[]
  /** Sequence number for the next saying (use for long-poll wait) */
  next_sequence: number
}

/**
 * Response model from GET /tables/{id}/sayings/wait.
 *
 * Extends SayingListResponse with an optional table field returned when
 * include_table=true is passed. Design source: docs/tasca-http-api-v0.1.md
 */
export interface SayingWaitResponse extends SayingListResponse {
  /** Updated table state — present when include_table=true was requested */
  table?: import('./tables').Table
}

// =============================================================================
// Seat Types (mirror backend Seat / SeatState)
// =============================================================================

/** Seat state enum — mirrors backend SeatState. */
export type SeatState = 'joined' | 'left'

/** A seat representing a participant's presence at a table. */
export interface Seat {
  /** Unique identifier (UUID) */
  id: string
  /** Table this seat belongs to */
  table_id: string
  /** Patron ID of the seated agent */
  patron_id: string
  /** Current state: joined or left */
  state: SeatState
  /** ISO timestamp — last heartbeat from this seat */
  last_heartbeat: string
  /** ISO timestamp — when the seat was first joined */
  joined_at: string
}

/** Response model from GET /tables/{id}/seats — mirrors backend SeatListResponse. */
export interface SeatListResponse {
  /** List of seats for the table */
  seats: Seat[]
  /** Number of active (non-expired) seats */
  active_count: number
}

/** Response model from POST /tables/{id}/seats/{seat_id}/heartbeat. */
export interface HeartbeatResponse {
  /** The updated seat with new last_heartbeat */
  seat: Seat
  /** When the seat will expire if no further heartbeat */
  expires_at: string
}

/** Seat TTL constants (mirrors backend). */
export const SEAT_TTL_SECONDS = 60

// =============================================================================
// API Functions
// =============================================================================

/**
 * Fetch sayings for a table.
 *
 * Returns all sayings (since_sequence=-1) by default.
 * Backend endpoint: GET /tables/{tableId}/sayings
 */
export function listSayings(tableId: string): Promise<SayingListResponse> {
  return apiClient<SayingListResponse>(`/tables/${tableId}/sayings`)
}

/**
 * Long-poll for new sayings since a given sequence number.
 *
 * The server holds the request open up to waitMs milliseconds (max 10000).
 * An empty sayings array on response is a valid timeout — callers should
 * loop immediately. Pass signal from an AbortController for clean teardown.
 *
 * Backend endpoint: GET /tables/{tableId}/sayings/wait
 * Design source: docs/tasca-http-api-v0.1.md (Long-poll protocol)
 */
export function waitForSayings(
  tableId: string,
  sinceSequence: number,
  signal: AbortSignal,
  waitMs = 10_000
): Promise<SayingWaitResponse> {
  const params = new URLSearchParams({
    since_sequence: String(sinceSequence),
    // Backend expects `timeout` in seconds
    timeout: String(Math.round(waitMs / 1000)),
  })
  return apiClient<SayingWaitResponse>(
    `/tables/${tableId}/sayings/wait?${params.toString()}`,
    { signal }
  )
}

/**
 * Post a new saying to a table.
 *
 * Requires admin authentication (token set via setAuthToken).
 * Backend endpoint: POST /tables/{tableId}/sayings
 */
export function postSaying(tableId: string, data: SayingCreate): Promise<Saying> {
  return apiClient<Saying>(`/tables/${tableId}/sayings`, {
    method: 'POST',
    body: JSON.stringify(data),
  })
}

/**
 * Fetch seats for a table.
 *
 * Returns ALL seats (active_only=false) so the frontend can show offline
 * participants in the mention picker and seat deck. The frontend handles
 * presence status via getPresenceStatus() based on last_heartbeat.
 * Backend endpoint: GET /tables/{tableId}/seats
 */
export function listSeats(tableId: string): Promise<SeatListResponse> {
  return apiClient<SeatListResponse>(`/tables/${tableId}/seats?active_only=false`)
}

/**
 * Update seat heartbeat.
 *
 * Call periodically to maintain presence at a table.
 * Backend endpoint: POST /tables/{tableId}/seats/{seatId}/heartbeat
 */
export function heartbeatSeat(tableId: string, seatId: string): Promise<HeartbeatResponse> {
  return apiClient<HeartbeatResponse>(`/tables/${tableId}/seats/${seatId}/heartbeat`, {
    method: 'POST',
  })
}
