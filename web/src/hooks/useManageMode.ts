/**
 * Manage mode hook — encapsulates batch delete selection state.
 *
 * Extracted from Taproom.tsx to reduce component complexity (S-2).
 */

import { useState, useCallback, useEffect } from 'react'
import { batchDeleteTables, type Table } from '../api/tables'
import { ApiError } from '../api/client'

const MAX_BATCH_SIZE = 100

/** Parse per-ID rejection details from a 409 batch delete error. */
function parseBatchDeleteError(err: unknown): string {
  if (err instanceof ApiError && err.status === 409) {
    // The message contains JSON-stringified detail from the server
    // Try to extract human-readable rejection details
    try {
      // ApiError.message format: "API Error: {json}"
      const jsonStr = err.message.replace(/^API Error:\s*/, '')
      const detail = JSON.parse(jsonStr)
      if (detail?.details && Array.isArray(detail.details)) {
        const reasons = detail.details.map(
          (d: { id: string; reason: string }) => `${d.id}: ${d.reason}`
        )
        return `Cannot delete: ${reasons.join(', ')}`
      }
    } catch {
      // Fall through to generic message
    }
  }
  return err instanceof Error ? err.message : 'Failed to delete tables'
}

export interface UseManageModeResult {
  manageMode: boolean
  selectedIds: Set<string>
  showDeleteConfirm: boolean
  deleteLoading: boolean
  deleteError: string | null
  enterManageMode: () => void
  exitManageMode: () => void
  toggleSelect: (tableId: string) => void
  toggleSelectAll: (closedTables: Table[]) => void
  requestDelete: () => void
  cancelDelete: () => void
  confirmDelete: () => Promise<void>
}

/**
 * Hook managing batch delete selection state and actions.
 *
 * @param isAdmin - Whether the current user has admin privileges
 * @param onDeleteSuccess - Callback after successful batch delete (e.g. refetch tables)
 */
export function useManageMode(
  isAdmin: boolean,
  onDeleteSuccess: () => void
): UseManageModeResult {
  const [manageMode, setManageMode] = useState(false)
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false)
  const [deleteLoading, setDeleteLoading] = useState(false)
  const [deleteError, setDeleteError] = useState<string | null>(null)

  // Exit manage mode when switching away from admin
  useEffect(() => {
    if (!isAdmin) {
      setManageMode(false)
      setSelectedIds(new Set())
    }
  }, [isAdmin])

  const enterManageMode = useCallback(() => {
    setManageMode(true)
  }, [])

  const exitManageMode = useCallback(() => {
    setManageMode(false)
    setSelectedIds(new Set())
    setDeleteError(null)
  }, [])

  const toggleSelect = useCallback((tableId: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev)
      if (next.has(tableId)) {
        next.delete(tableId)
      } else {
        if (next.size >= MAX_BATCH_SIZE) return prev
        next.add(tableId)
      }
      return next
    })
  }, [])

  const toggleSelectAll = useCallback((closedTables: Table[]) => {
    setSelectedIds((prev) => {
      const allSelected = closedTables.length > 0 && closedTables.every((t) => prev.has(t.id))
      if (allSelected) {
        return new Set()
      }
      const ids = closedTables.slice(0, MAX_BATCH_SIZE).map((t) => t.id)
      return new Set(ids)
    })
  }, [])

  const requestDelete = useCallback(() => {
    setShowDeleteConfirm(true)
  }, [])

  const cancelDelete = useCallback(() => {
    setShowDeleteConfirm(false)
  }, [])

  const confirmDelete = useCallback(async () => {
    if (selectedIds.size === 0) return
    setDeleteLoading(true)
    setDeleteError(null)
    try {
      await batchDeleteTables(Array.from(selectedIds))
      setSelectedIds(new Set())
      setShowDeleteConfirm(false)
      setManageMode(false)
      onDeleteSuccess()
    } catch (err) {
      setDeleteError(parseBatchDeleteError(err))
      setShowDeleteConfirm(false)
    } finally {
      setDeleteLoading(false)
    }
  }, [selectedIds, onDeleteSuccess])

  return {
    manageMode,
    selectedIds,
    showDeleteConfirm,
    deleteLoading,
    deleteError,
    enterManageMode,
    exitManageMode,
    toggleSelect,
    toggleSelectAll,
    requestDelete,
    cancelDelete,
    confirmDelete,
  }
}
