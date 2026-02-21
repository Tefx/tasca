/**
 * Patrons API client.
 *
 * Types and functions for patron operations.
 * Types sourced from backend:
 *   - src/tasca/core/domain/patron.py (Patron)
 *   - src/tasca/shell/api/routes/patrons.py (PatronRegisterResponse)
 */

import { apiClient } from './client'

// =============================================================================
// Patron Types (mirror backend Patron model)
// =============================================================================

/** Patron kind enum — mirrors backend. */
export type PatronKind = 'agent' | 'human'

/** A patron (agent or human) that can participate in discussions. */
export interface Patron {
  /** Unique identifier (UUID) */
  id: string
  /** Display name of the patron */
  name: string
  /** Type of patron: agent or human */
  kind: PatronKind
  /** ISO timestamp — when the patron was created */
  created_at: string
}

/** Response from POST /patrons (registration with deduplication). */
export interface PatronRegisterResponse {
  id: string
  name: string
  kind: PatronKind
  created_at: string
  /** True if this was a new patron, false if existing was returned */
  is_new: boolean
}

// =============================================================================
// API Functions
// =============================================================================

/**
 * Register a patron (or get existing by name).
 *
 * Backend endpoint: POST /patrons
 */
export function registerPatron(name: string, kind: PatronKind = 'agent'): Promise<PatronRegisterResponse> {
  return apiClient<PatronRegisterResponse>('/patrons', {
    method: 'POST',
    body: JSON.stringify({ name, kind }),
  })
}

/**
 * Get a patron by ID.
 *
 * Backend endpoint: GET /patrons/{patronId}
 */
export function getPatron(patronId: string): Promise<Patron> {
  return apiClient<Patron>(`/patrons/${patronId}`)
}