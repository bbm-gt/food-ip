export type DirectorMessageRole = 'OWNER' | 'DIRECTOR'
export type DirectorMessageDelivery = 'sending' | 'sent' | 'failed'
export type DirectorStatus = 'idle' | 'active' | 'ready' | 'blocked'

export interface DirectorMessage {
  id: string
  role: DirectorMessageRole
  content: string
  delivery: DirectorMessageDelivery
}

export interface ReadyContent {
  id: string
  title: string
  script_text: string
  shooting_notes: string[] | string
}

export interface PendingRequest {
  client_message_id: string
  expected_state_version: number
  content: string
  parameters: Record<string, unknown>
}

export interface DirectorLocalState {
  project_id: string | null
  session_id: string | null
  state_version: number
  status: DirectorStatus
  messages: DirectorMessage[]
  ready_content: ReadyContent | null
  previous_ready_content: ReadyContent | null
  source_ready_content_id: string | null
  pending_request: PendingRequest | null
  updated_at: string
}

export interface CreateDirectorSessionResponse {
  session_id: string
  lifecycle_status: 'ACTIVE'
  state_version: 0
  source_ready_content_id: string | null
}

export interface DirectorTurnResponse {
  session_id: string
  turn_id: string
  state_version: number
  message: {
    id: string
    role: 'DIRECTOR'
    content: string
  }
  status: 'WAITING_FOR_OWNER' | 'READY'
  ready_content: ReadyContent | null
  replayed: boolean
}
