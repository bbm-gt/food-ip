import type { DirectorLocalState } from './directorTypes'

const STORAGE_KEY = 'food-ip:director:v1'
const STORAGE_VERSION = 1

interface StoredDirectorState {
  version: typeof STORAGE_VERSION
  data: DirectorLocalState
}

export function emptyDirectorState(): DirectorLocalState {
  return {
    project_id: null,
    session_id: null,
    state_version: 0,
    status: 'idle',
    messages: [],
    ready_content: null,
    source_ready_content_id: null,
    pending_request: null,
    updated_at: new Date().toISOString(),
  }
}

function isStoredState(value: unknown): value is StoredDirectorState {
  if (!value || typeof value !== 'object') return false
  const candidate = value as Partial<StoredDirectorState>
  return candidate.version === STORAGE_VERSION && Boolean(candidate.data)
}

export function loadDirectorState(): DirectorLocalState {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    if (!raw) return emptyDirectorState()
    const parsed: unknown = JSON.parse(raw)
    if (!isStoredState(parsed)) return emptyDirectorState()
    return {
      ...emptyDirectorState(),
      ...parsed.data,
      messages: parsed.data.messages.map((message) => ({
        ...message,
        delivery: message.delivery === 'sending' ? 'failed' : message.delivery,
      })),
    }
  } catch {
    return emptyDirectorState()
  }
}

export function saveDirectorState(state: DirectorLocalState): void {
  const stored: StoredDirectorState = { version: STORAGE_VERSION, data: state }
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
