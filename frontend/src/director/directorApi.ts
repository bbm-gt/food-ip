import type {
  CreateDirectorSessionResponse,
  DirectorTurnResponse,
} from './directorTypes'

const API_BASE = '/api'

export class DirectorApiError extends Error {
  readonly status: number
  readonly code: string | null
  readonly message: string

  constructor(status: number, code: string | null, message: string) {
    super(message)
    this.name = 'DirectorApiError'
    this.status = status
    this.code = code
    this.message = message
  }
}

interface ErrorPayload {
  code?: unknown
  message?: unknown
  detail?: unknown
}

function errorParts(payload: ErrorPayload): { code: string | null; message: string } {
  const detail = payload.detail && typeof payload.detail === 'object'
    ? payload.detail as ErrorPayload
    : undefined
  const code = detail?.code ?? payload.code
  const message = detail?.message ?? payload.message ?? payload.detail
  return {
    code: typeof code === 'string' ? code : null,
    message: typeof message === 'string' ? message : '请求失败，请稍后重试。',
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  if (init.body != null) headers.set('Content-Type', 'application/json')
  const response = await fetch(`${API_BASE}${path}`, { ...init, headers })
  if (!response.ok) {
    const payload = await response.json().catch(() => ({})) as ErrorPayload
    const parts = errorParts(payload)
    throw new DirectorApiError(response.status, parts.code, parts.message)
  }
  return response.json() as Promise<T>
}

export function createDirectorProject(name: string): Promise<{ id: string }> {
  return request<{ id: string }>('/projects', {
    method: 'POST',
    body: JSON.stringify({ name }),
  })
}

export function createDirectorSession(
  projectId: string,
  sourceReadyContentId?: string,
): Promise<CreateDirectorSessionResponse> {
  return request<CreateDirectorSessionResponse>(
    `/projects/${encodeURIComponent(projectId)}/director-sessions`,
    {
      method: 'POST',
      body: JSON.stringify(sourceReadyContentId
        ? { source_ready_content_id: sourceReadyContentId }
        : {}),
    },
  )
}

export function submitDirectorMessage(
  projectId: string,
  sessionId: string,
  requestBody: {
    client_message_id: string
    expected_state_version: number
    content: string
    parameters: Record<string, unknown>
  },
): Promise<DirectorTurnResponse> {
  return request<DirectorTurnResponse>(
    `/projects/${encodeURIComponent(projectId)}/director-sessions/${encodeURIComponent(sessionId)}/messages`,
    {
      method: 'POST',
      body: JSON.stringify(requestBody),
    },
  )
}
