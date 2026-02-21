/**
 * Components barrel export.
 *
 * Re-exports all components for cleaner imports.
 */

// Mission Control components
export { Board } from './Board'
export { Stream } from './Stream'
export { SeatDeck, MentionPicker, getPresenceStatus } from './SeatDeck'
export type {
  SeatWithPatron,
  PatronInfo,
  SeatPresenceStatus,
  MentionPickerProps,
} from './SeatDeck'
export { MentionInput, MentionError } from './MentionInput'
export type { MentionInputProps, MentionErrorProps } from './MentionInput'
export { ModeIndicator } from './ModeIndicator'