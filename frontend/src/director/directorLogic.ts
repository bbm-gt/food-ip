export type DirectorLogicStatus = 'idle' | 'active' | 'ready' | 'blocked'

interface LogicMessage {
  id: string
  role: 'OWNER' | 'DIRECTOR'
  content: string
  delivery: 'sending' | 'sent' | 'failed'
}

interface LogicReadyContent {
  id: string
  title: string
  script_text: string
  shooting_notes: string[] | string
}

interface LogicPendingRequest {
  client_message_id: string
  expected_state_version: number
  content: string
  parameters: Record<string, unknown>
}

interface LogicDirectionInteraction {
  kind: 'DIRECTION_SELECTION'
  options: Array<{ id: string; direction: string; reason: string; recommended: boolean }>
}

export interface DirectorStateShape {
  project_id: string | null
  session_id: string | null
  state_version: number
  status: DirectorLogicStatus
  messages: LogicMessage[]
  ready_content: LogicReadyContent | null
  previous_ready_content: LogicReadyContent | null
  source_ready_content_id: string | null
  pending_request: LogicPendingRequest | null
  interaction: LogicDirectionInteraction | null
  updated_at: string
}

export interface LogicSessionResponse {
  session_id: string
  state_version: 0
  source_ready_content_id: string | null
}

export interface LogicTurnResponse {
  state_version: number
  message: {
    id: string
    role: 'OWNER' | 'DIRECTOR'
    content: string
  }
  status: 'WAITING_FOR_OWNER' | 'READY'
  ready_content: LogicReadyContent | null
  interaction: LogicDirectionInteraction | null
}

export interface SubmitErrorInput {
  status: number | null
  code: string | null
  message: string
  network?: boolean
}

export interface SubmitErrorOutcome {
  status: DirectorLogicStatus
  retryable: boolean
  clearPending: boolean
  message: string
}

export const DIRECTOR_STORAGE_VERSION = 2

export function emptyDirectorState(now = new Date().toISOString()): DirectorStateShape {
  return {
    project_id: null,
    session_id: null,
    state_version: 0,
    status: 'idle',
    messages: [],
    ready_content: null,
    previous_ready_content: null,
    source_ready_content_id: null,
    pending_request: null,
    interaction: null,
    updated_at: now,
  }
}

export function normalizeLoadedDirectorState(state: DirectorStateShape): DirectorStateShape {
  return {
    ...state,
    messages: state.messages.map((message) => ({
      ...message,
      delivery: message.delivery === 'sending' ? 'failed' : message.delivery,
    })),
  }
}

export function pendingForRetry(state: DirectorStateShape): LogicPendingRequest | null {
  if (!state.pending_request || state.status === 'blocked' || state.status === 'ready') return null
  return state.pending_request
}

export function applyRevisionSession(
  state: DirectorStateShape,
  session: LogicSessionResponse,
): DirectorStateShape {
  return {
    ...state,
    session_id: session.session_id,
    state_version: 0,
    status: 'active',
    messages: [],
    pending_request: null,
    interaction: null,
    previous_ready_content: state.ready_content,
    ready_content: null,
    source_ready_content_id: session.source_ready_content_id,
  }
}

export function applySuccessfulTurn(
  state: DirectorStateShape,
  clientMessageId: string,
  response: LogicTurnResponse,
): DirectorStateShape {
  const messages = state.messages.map((message) => message.id === clientMessageId
    ? { ...message, delivery: 'sent' as const }
    : message)
  if (!messages.some((message) => message.id === response.message.id)) {
    messages.push({ ...response.message, delivery: 'sent' })
  }
  return {
    ...state,
    state_version: response.state_version,
    status: response.status === 'READY' ? 'ready' : 'active',
    messages,
    ready_content: response.status === 'READY' ? response.ready_content : state.ready_content,
    interaction: response.interaction,
    pending_request: null,
  }
}

export function applyFailedTurn(
  state: DirectorStateShape,
  clientMessageId: string,
  outcome: SubmitErrorOutcome,
): DirectorStateShape {
  return {
    ...state,
    status: outcome.status,
    messages: state.messages.map((message) => message.id === clientMessageId
      ? { ...message, delivery: 'failed' as const }
      : message),
    pending_request: outcome.clearPending ? null : state.pending_request,
  }
}

export function classifySubmitError(
  error: SubmitErrorInput,
  hasCurrentReadyContent: boolean,
): SubmitErrorOutcome {
  if (error.code === 'idempotency_conflict') {
    return { status: 'active', retryable: false, clearPending: true, message: '该消息请求发生冲突，请重新发送。' }
  }
  if (error.code === 'state_version_conflict') {
    return { status: 'blocked', retryable: false, clearPending: false, message: '该对话可能已在其他页面更新，请新建对话后继续。' }
  }
  if (error.code === 'invalid_direction_selection') {
    return { status: 'active', retryable: false, clearPending: true, message: '这个方向选择已失效，请从当前方向卡重新选择。' }
  }
  if (error.code === 'session_ready') {
    return hasCurrentReadyContent
      ? { status: 'ready', retryable: false, clearPending: false, message: '该对话已经完成，不能继续发送。' }
      : { status: 'blocked', retryable: false, clearPending: false, message: '该对话已在其他页面完成，但当前浏览器无法恢复最新结果，请新建对话。' }
  }
  if (error.status === 404) {
    return { status: 'blocked', retryable: false, clearPending: true, message: '资源已失效，需要新建对话。' }
  }
  if (error.status === 422) {
    return { status: 'active', retryable: false, clearPending: true, message: '请求数据无效，请检查后重新发送。' }
  }
  if (error.status === 400) {
    return { status: 'active', retryable: false, clearPending: true, message: '请求数据无效，请检查后重新发送。' }
  }
  if (error.network) {
    return { status: 'active', retryable: true, clearPending: false, message: '网络连接中断，结果可能未知。' }
  }
  if (error.status === 500) {
    return { status: 'active', retryable: true, clearPending: false, message: '服务暂时不可用。' }
  }
  if (error.status === 502) {
    return { status: 'active', retryable: true, clearPending: false, message: '模型输出暂时不可用。' }
  }
  if (error.status === 503) {
    return { status: 'active', retryable: true, clearPending: false, message: '服务暂时不可用。' }
  }
  return { status: 'active', retryable: false, clearPending: true, message: error.message }
}

export function hasDirectorData(state: DirectorStateShape): boolean {
  return state.messages.length > 0
    || state.pending_request !== null
    || state.ready_content !== null
    || state.previous_ready_content !== null
}

export function clearDirectorStateIfConfirmed(
  state: DirectorStateShape,
  confirmed: boolean,
  now = new Date().toISOString(),
): DirectorStateShape {
  return confirmed ? emptyDirectorState(now) : state
}

export function initialEntryParameters(mode: 'DISCOVER' | 'IDEA'): Record<string, unknown> {
  return { entry_mode: mode }
}

export function directionSelectionPayload(option: {
  id: string
  direction: string
}): { content: string; parameters: Record<string, unknown> } {
  return {
    content: `我选择这个方向：${option.direction}`,
    parameters: { action: 'SELECT_DIRECTION', direction_id: option.id },
  }
}

export function readyContentText(content: LogicReadyContent): string {
  return [content.title, content.script_text].join('\n\n')
}
