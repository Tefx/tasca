/**
 * CommandConsole — Bottom command bar for the Table view.
 *
 * Contains the MentionInput, send button, and table status controls
 * (pause/resume, request summary, end meeting).
 *
 * Design source: docs/tasca-web-uiux-v0.1.md (Table View / Mission Control spec §E Controls)
 */

import { useState, useCallback, useRef, forwardRef, useImperativeHandle, useEffect } from 'react'
import {
  postSaying,
  type AttachmentInput,
  type Saying,
  type Seat,
} from '../api/sayings'
import { pauseTable, resumeTable, closeTable, type Table as TableType } from '../api/tables'
import { MentionInput, type MentionInputRef } from './MentionInput'
import { type PatronInfo } from './SeatDeck'
import { useAuth } from '../auth/AuthContext'
import { RequestSummaryButton } from './RequestSummaryButton'

// =============================================================================
// Types
// =============================================================================

/** Ref handle exposed by CommandConsole — used for keyboard nav focus. */
export interface CommandConsoleRef {
  focus: () => void
}

export interface CommandConsoleProps {
  table: TableType
  seats: Seat[]
  patrons?: Map<string, PatronInfo>
  /** Called after a saying is successfully posted — receives the new saying for optimistic update. */
  onPosted?: (saying: Saying) => void
  /** Called when table status changes successfully */
  onStatusChange?: (table: TableType) => void
  /** Called when an error occurs */
  onError?: (error: Error) => void
}

// =============================================================================
// Helpers
// =============================================================================

/** Check if table can be paused. */
function canPause(status: string): boolean {
  return status === 'open'
}

/** Check if table can be resumed. */
function canResume(status: string): boolean {
  return status === 'paused'
}

/** Check if table can be closed. */
function canClose(status: string): boolean {
  return status === 'open' || status === 'paused'
}

/** Match the characters treated as whitespace by Python's str.strip(). */
const pythonWhitespaceBoundary = /^[\p{White_Space}\u001c-\u001f]|[\p{White_Space}\u001c-\u001f]$/u
const pythonWhitespaceOnly = /^[\p{White_Space}\u001c-\u001f]*$/u

// =============================================================================
// Close Confirmation Hook
// =============================================================================

type CloseState = 'idle' | 'confirming' | 'closing'

interface UseCloseConfirmationResult {
  closeState: CloseState
  canTriggerClose: boolean
  handleClose: () => void
  confirmClose: () => Promise<void>
  cancelClose: () => void
}

/** Hook to manage close confirmation state with escape key and auto-timeout. */
function useCloseConfirmation(
  table: TableType,
  onStatusChange?: (table: TableType) => void,
  onError?: (error: Error) => void,
  isOperating?: boolean
): UseCloseConfirmationResult {
  const [closeState, setCloseState] = useState<CloseState>('idle')

  const handleClose = useCallback(() => {
    if (!canClose(table.status) || isOperating || closeState !== 'idle') return
    setCloseState('confirming')
  }, [table.status, isOperating, closeState])

  const confirmClose = useCallback(async () => {
    if (closeState !== 'confirming') return
    setCloseState('closing')
    try {
      const updated = await closeTable(table)
      onStatusChange?.(updated)
      setCloseState('idle')
    } catch (err) {
      onError?.(err instanceof Error ? err : new Error('Failed to close table'))
      setCloseState('idle')
    }
  }, [table, closeState, onStatusChange, onError])

  const cancelClose = useCallback(() => setCloseState('idle'), [])

  // Escape key cancels confirmation
  useEffect(() => {
    if (closeState !== 'confirming') return
    const handleKeydown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') cancelClose()
    }
    document.addEventListener('keydown', handleKeydown)
    return () => document.removeEventListener('keydown', handleKeydown)
  }, [closeState, cancelClose])

  // Auto-revert after 5s idle
  useEffect(() => {
    if (closeState !== 'confirming') return
    const timer = window.setTimeout(() => setCloseState('idle'), 5000)
    return () => window.clearTimeout(timer)
  }, [closeState])

  return {
    closeState,
    canTriggerClose: canClose(table.status) && !isOperating,
    handleClose,
    confirmClose,
    cancelClose,
  }
}

// =============================================================================
// Close Confirmation UI
// =============================================================================

interface CloseConfirmationProps {
  closeState: CloseState
  onConfirm: () => void
  onCancel: () => void
}

/** Inline confirmation UI for end meeting action. */
function CloseConfirmation({ closeState, onConfirm, onCancel }: CloseConfirmationProps) {
  return (
    <span className="mc-inline-confirm" role="group" aria-label="Confirm end meeting">
      <span className="mc-inline-confirm-label">End meeting?</span>
      <button
        type="button"
        className="mc-control-btn mc-control-btn--end-confirm"
        onClick={onConfirm}
        disabled={closeState === 'closing'}
        aria-label="Confirm end meeting"
      >
        {closeState === 'closing' ? 'Closing...' : 'Confirm'}
      </button>
      <button
        type="button"
        className="mc-control-btn mc-control-btn--cancel"
        onClick={onCancel}
        disabled={closeState === 'closing'}
        aria-label="Cancel end meeting"
      >
        Cancel
      </button>
    </span>
  )
}

// =============================================================================
// ConsoleToolbar Component
// =============================================================================

interface ConsoleToolbarProps {
  table: TableType
  seats: Seat[]
  patrons?: Map<string, PatronInfo>
  isSubmitting: boolean
  onInsertSummary: (text: string) => void
  onStatusChange?: (table: TableType) => void
  onError?: (error: Error) => void
}

/** ConsoleToolbar — Admin controls for table operations. */
function ConsoleToolbar({
  table,
  seats,
  patrons,
  isSubmitting,
  onInsertSummary,
  onStatusChange,
  onError,
}: ConsoleToolbarProps) {
  const [controlState, setControlState] = useState<'idle' | 'pausing' | 'resuming'>('idle')
  const isOperating = controlState !== 'idle'

  const { closeState, canTriggerClose, handleClose, confirmClose, cancelClose } =
    useCloseConfirmation(table, onStatusChange, onError, isOperating)

  const handlePause = useCallback(async () => {
    if (!canPause(table.status) || isOperating) return
    setControlState('pausing')
    try {
      const updated = await pauseTable(table)
      onStatusChange?.(updated)
    } catch (err) {
      onError?.(err instanceof Error ? err : new Error('Failed to pause table'))
    } finally {
      setControlState('idle')
    }
  }, [table, isOperating, onStatusChange, onError])

  const handleResume = useCallback(async () => {
    if (!canResume(table.status) || isOperating) return
    setControlState('resuming')
    try {
      const updated = await resumeTable(table)
      onStatusChange?.(updated)
    } catch (err) {
      onError?.(err instanceof Error ? err : new Error('Failed to resume table'))
    } finally {
      setControlState('idle')
    }
  }, [table, isOperating, onStatusChange, onError])

  return (
    <div className="mc-console-toolbar">
      <RequestSummaryButton
        seats={seats}
        patrons={patrons}
        onInsert={onInsertSummary}
        disabled={table.status === 'closed'}
        isOperating={isSubmitting}
      />
      {canPause(table.status) && (
        <button
          type="button"
          className="mc-control-btn mc-control-btn--pause"
          onClick={handlePause}
          disabled={isOperating}
          title="Pause table — prevent new joins"
        >
          {controlState === 'pausing' ? 'Pausing...' : 'Pause'}
        </button>
      )}
      {canResume(table.status) && (
        <button
          type="button"
          className="mc-control-btn mc-control-btn--resume"
          onClick={handleResume}
          disabled={isOperating}
          title="Resume table — allow new joins"
        >
          {controlState === 'resuming' ? 'Resuming...' : 'Resume'}
        </button>
      )}
      {canTriggerClose && closeState === 'idle' && (
        <button
          type="button"
          className="mc-control-btn mc-control-btn--end-ghost"
          onClick={handleClose}
          title="End meeting — close table permanently"
        >
          End Meeting
        </button>
      )}
      {canTriggerClose && closeState !== 'idle' && (
        <CloseConfirmation
          closeState={closeState}
          onConfirm={confirmClose}
          onCancel={cancelClose}
        />
      )}
    </div>
  )
}

// =============================================================================
// CommandConsole
// =============================================================================

/**
 * CommandConsole — Bottom command bar for the Table view.
 *
 * @example
 * // Basic usage with table and seats
 * <CommandConsole
 *   table={tableData}
 *   seats={seatsArray}
 *   onPosted={(saying) => console.log('Posted:', saying)}
 * />
 *
 * @example
 * // With keyboard nav ref
 * const consoleRef = useRef<CommandConsoleRef>(null)
 * <CommandConsole ref={consoleRef} table={tableData} seats={[]} />
 */
export const CommandConsole = forwardRef<CommandConsoleRef, CommandConsoleProps>(
  function CommandConsole({ table, seats, patrons, onPosted, onStatusChange, onError }, ref) {
    const { mode, hasToken } = useAuth()
    const [value, setValue] = useState('')
    const [isSubmitting, setIsSubmitting] = useState(false)
    const [error, setError] = useState<string | null>(null)
    const [attachments, setAttachments] = useState<AttachmentInput[]>([])
    const [isReadingAttachments, setIsReadingAttachments] = useState(false)
    const attachmentsRef = useRef<AttachmentInput[]>([])
    const attachmentReadPendingRef = useRef(false)
    const mentionInputRef = useRef<MentionInputRef>(null)
    const attachmentInputRef = useRef<HTMLInputElement>(null)

    const replaceAttachments = useCallback((next: AttachmentInput[]) => {
      attachmentsRef.current = next
      setAttachments(next)
    }, [])

    const isAdmin = mode === 'admin' && hasToken
    const isClosed = table.status === 'closed'

    // Expose focus() to parent via forwardRef (keyboard nav '/' binding)
    useImperativeHandle(ref, () => ({
      focus: () => mentionInputRef.current?.focus(0),
    }))

    const handleSubmit = useCallback(async () => {
      const trimmed = value.trim()
      if (!trimmed || !isAdmin || isSubmitting || attachmentReadPendingRef.current) return

      setIsSubmitting(true)
      setError(null)
      const submittedAttachments = attachmentsRef.current
      try {
        const newSaying = await postSaying(table.id, {
          speaker_name: 'Human',
          content: trimmed,
          patron_id: null,
          ...(submittedAttachments.length > 0 ? { attachments: submittedAttachments } : {}),
        })
        setValue('')
        replaceAttachments([])
        onPosted?.(newSaying)
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to send saying')
      } finally {
        setIsSubmitting(false)
      }
    }, [value, isAdmin, isSubmitting, table.id, onPosted, replaceAttachments])

    const handleAttachmentSelection = useCallback(async (files: FileList | null) => {
      if (!files?.length || attachmentReadPendingRef.current) return

      attachmentReadPendingRef.current = true
      setIsReadingAttachments(true)
      setError(null)
      const existingAttachments = attachmentsRef.current
      try {
        const selected = Array.from(files)
        if (existingAttachments.length + selected.length > 8) {
          throw new Error('A saying may contain at most 8 attachments')
        }
        const decoded: AttachmentInput[] = []
        let totalBytes = existingAttachments.reduce(
          (total, item) => total + new TextEncoder().encode(item.content).byteLength,
          0
        )
        for (const file of selected) {
          const nameLength = Array.from(file.name).length
          const validName =
            nameLength >= 1 &&
            nameLength <= 128 &&
            !pythonWhitespaceBoundary.test(file.name) &&
            (file.name.endsWith('.md') || file.name.endsWith('.markdown')) &&
            !file.name.includes('/') &&
            !file.name.includes('\\') &&
            !file.name.includes('\0')
          if (!validName) throw new Error(`Invalid Markdown attachment name: ${file.name}`)
          if (file.size > 256 * 1024) throw new Error(`${file.name} exceeds 256 KiB`)
          const bytes = new Uint8Array(await file.arrayBuffer())
          const content = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(bytes)
          if (pythonWhitespaceOnly.test(content)) {
            throw new Error(`${file.name} must contain non-whitespace Markdown`)
          }
          totalBytes += bytes.byteLength
          if (totalBytes > 1024 * 1024) throw new Error('Attachments exceed 1 MiB total')
          decoded.push({ name: file.name, content })
        }
        replaceAttachments([...existingAttachments, ...decoded])
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to read Markdown attachment')
      } finally {
        attachmentReadPendingRef.current = false
        setIsReadingAttachments(false)
        if (attachmentInputRef.current) attachmentInputRef.current.value = ''
      }
    }, [replaceAttachments])

    const handleRemoveAttachment = useCallback((index: number) => {
      if (attachmentReadPendingRef.current || isSubmitting) return
      replaceAttachments(attachmentsRef.current.filter((_, item) => item !== index))
    }, [isSubmitting, replaceAttachments])

    const handleInsertSummaryRequest = useCallback((text: string) => {
      setValue(text)
      mentionInputRef.current?.focus(text.length)
    }, [])

    return (
      <div className="mc-console">
        {/* Toolbar controls — only for admin */}
        {isAdmin && (
          <ConsoleToolbar
            table={table}
            seats={seats}
            patrons={patrons}
            isSubmitting={isSubmitting}
            onInsertSummary={handleInsertSummaryRequest}
            onStatusChange={onStatusChange}
            onError={onError}
          />
        )}

        {error && (
          <p className="mc-console-error" role="alert">
            {error}
          </p>
        )}
        {attachments.length > 0 && (
          <ul className="mc-attachment-selection" aria-label="Selected Markdown attachments">
            {attachments.map((attachment, index) => (
              <li key={`${attachment.name}-${index}`}>
                <span>{attachment.name}</span>
                <button
                  type="button"
                  onClick={() => handleRemoveAttachment(index)}
                  disabled={isSubmitting || isReadingAttachments}
                  aria-label={`Remove ${attachment.name}`}
                >
                  ×
                </button>
              </li>
            ))}
          </ul>
        )}
        <div className="mc-console-row">
          <MentionInput
            ref={mentionInputRef}
            value={value}
            onChange={setValue}
            seats={seats}
            patrons={patrons}
            disabled={!isAdmin || isClosed}
            onSubmit={handleSubmit}
            placeholder={
              isClosed
                ? 'Meeting ended — no further messages'
                : isAdmin
                  ? 'Say something…'
                  : 'Viewer mode — enter admin to post'
            }
            className="mc-console-input"
          />
          {isAdmin && !isClosed && (
            <label className="mc-attachment-picker" title="Attach Markdown files">
              <span aria-hidden="true">＋.md</span>
              <span className="sr-only">Attach Markdown files</span>
              <input
                ref={attachmentInputRef}
                type="file"
                aria-label="Attach Markdown files"
                accept=".md,.markdown,text/markdown"
                multiple
                disabled={isSubmitting || isReadingAttachments || attachments.length >= 8}
                onChange={(event) => void handleAttachmentSelection(event.target.files)}
              />
            </label>
          )}
          {isAdmin && !isClosed && (
            <button
              type="button"
              className="mc-console-send-btn"
              onClick={handleSubmit}
              disabled={!value.trim() || isSubmitting || isReadingAttachments}
              title="Send saying (Enter)"
            >
              {isReadingAttachments ? 'Reading…' : isSubmitting ? '…' : 'Send'}
            </button>
          )}
        </div>
      </div>
    )
  }
)
