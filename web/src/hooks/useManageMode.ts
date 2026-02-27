/**
 * Manage mode hook — encapsulates batch delete and batch close selection state.
 *
 * Extracted from Taproom.tsx to reduce component complexity (S-2).
 */

import { useState, useCallback, useEffect } from 'react'
import { batchDeleteTables, controlTable, type Table } from '../api/tables'
import { ApiError } from '../api/client'

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
  toggleSelectAll: (visibleTables: Table[]) => void
  requestDelete: () => void
  cancelDelete: () => void
  confirmDelete: () => Promise<void>
  showCloseConfirm: boolean
  closeLoading: boolean
  closeError: string | null
  requestClose: () => void
  cancelClose: () => void
  confirmClose: (closableIds: string[]) => Promise<void>
}

/**
 * Hook managing batch delete and batch close selection state and actions.
 *
 * @param isAdmin - Whether the current user has admin privileges
 * @param onDeleteSuccess - Callback after successful batch delete or close (e.g. refetch tables)
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
  const [showCloseConfirm, setShowCloseConfirm] = useState(false)
  const [closeLoading, setCloseLoading] = useState(false)
  const [closeError, setCloseError] = useState<string | null>(null)

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
    setCloseError(null)
  }, [])

  const toggleSelect = useCallback((tableId: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev)
      if (next.has(tableId)) {
        next.delete(tableId)
      } else {
        next.add(tableId)
      }
      return next
    })
  }, [])

  const toggleSelectAll = useCallback((visibleTables: Table[]) => {
    setSelectedIds((prev) => {
      const allSelected = visibleTables.length > 0 && visibleTables.every((t) => prev.has(t.id))
      if (allSelected) {
        return new Set()
      }
      const ids = visibleTables.map((t) => t.id)
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
      onDeleteSuccess()
    } catch (err) {
      setDeleteError(parseBatchDeleteError(err))
      setShowDeleteConfirm(false)
    } finally {
      setDeleteLoading(false)
    }
  }, [selectedIds, onDeleteSuccess])

  const requestClose = useCallback(() => {
    setShowCloseConfirm(true)
  }, [])

  const cancelClose = useCallback(() => {
    setShowCloseConfirm(false)
  }, [])

  const confirmClose = useCallback(async (closableIds: string[]) => {
    if (closableIds.length === 0) return
    setCloseLoading(true)
    setCloseError(null)
    try {
      const results = await Promise.allSettled(
        closableIds.map((id) => controlTable(id, 'close', 'human'))
      )
      const failedIds: string[] = []
      results.forEach((result, index) => {
        if (result.status === 'rejected') {
          failedIds.push(closableIds[index])
        }
      })
      if (failedIds.length === 0) {
        // Full success: clear selection, stay in manage mode, refetch
        setSelectedIds(new Set())
        setShowCloseConfirm(false)
        onDeleteSuccess()
      } else if (failedIds.length === closableIds.length) {
        // Total failure
        setCloseError(`Failed to close all ${failedIds.length} tables`)
        setShowCloseConfirm(false)
      } else {
        // Partial failure: keep failed IDs selected, clear succeeded ones
        setSelectedIds((prev) => {
          const next = new Set(prev)
          closableIds.forEach((id) => {
            if (!failedIds.includes(id)) {
              next.delete(id)
            }
          })
          return next
        })
        setCloseError(`Failed to close ${failedIds.length} of ${closableIds.length} tables`)
        setShowCloseConfirm(false)
        onDeleteSuccess()
      }
    } catch (err) {
      // Unexpected error (not from allSettled — those are captured as rejections)
      setCloseError(err instanceof Error ? err.message : 'Failed to close tables')
      setShowCloseConfirm(false)
    } finally {
      setCloseLoading(false)
    }
  }, [onDeleteSuccess])

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
    showCloseConfirm,
    closeLoading,
    closeError,
    requestClose,
    cancelClose,
    confirmClose,
  }
}
