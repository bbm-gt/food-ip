import {
  DIRECTOR_STORAGE_VERSION,
  emptyDirectorState,
  normalizeLoadedDirectorState,
} from './directorLogic'
import type { DirectorLocalState } from './directorTypes'

const STORAGE_KEY = 'food-ip:director:v2'

interface StoredDirectorState {
  version: typeof DIRECTOR_STORAGE_VERSION
  data: DirectorLocalState
}

function isStoredState(value: unknown): value is StoredDirectorState {
  if (!value || typeof value !== 'object') return false
  const candidate = value as Partial<StoredDirectorState>
  return candidate.version === DIRECTOR_STORAGE_VERSION && Boolean(candidate.data)
}

export function loadDirectorState(): DirectorLocalState {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    if (!raw) return emptyDirectorState()
    const parsed: unknown = JSON.parse(raw)
    if (!isStoredState(parsed)) return emptyDirectorState()
    return normalizeLoadedDirectorState({
      ...emptyDirectorState(),
      ...parsed.data,
      messages: parsed.data.messages.map((message) => ({
        ...message,
        delivery: message.delivery === 'sending' ? 'failed' : message.delivery,
      })),
    }) as DirectorLocalState
  } catch {
    return emptyDirectorState()
  }
}

export function saveDirectorState(state: DirectorLocalState): void {
  const stored: StoredDirectorState = { version: DIRECTOR_STORAGE_VERSION, data: state }
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(stored))
  } catch {
    // Storage failure must not make the chat unusable.
  }
}

export function clearDirectorState(): void {
  try {
    window.localStorage.removeItem(STORAGE_KEY)
  } catch {
    // Nothing else is required when storage cannot be cleared.
  }
}
