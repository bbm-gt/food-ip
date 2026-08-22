import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'

import {
  createDirectorProject,
  createDirectorSession,
  DirectorApiError,
  submitDirectorMessage,
} from './directorApi'
import {
  clearDirectorState,
  loadDirectorState,
  saveDirectorState,
} from './directorStorage'
import {
  applyRevisionSession,
  applyFailedTurn,
  applySuccessfulTurn,
  classifySubmitError,
  clearDirectorStateIfConfirmed,
  hasDirectorData,
  initialEntryParameters,
  pendingForRetry,
  directionSelectionPayload,
  readyContentText,
} from './directorLogic'
import type {
  DirectionOption,
  DirectorLocalState,
  DirectorMessage,
  PendingRequest,
  ReadyContent,
} from './directorTypes'
import './director.css'

interface Notice {
  kind: 'info' | 'success' | 'error'
  text: string
  retryable?: boolean
}

function newClientMessageId(): string {
  return crypto.randomUUID()
}

function projectNameFromMessage(content: string): string {
  const compact = content.trim().replace(/\s+/g, ' ')
  return `Director 对话：${compact.slice(0, 48) || '未命名内容'}`
}

function updateMessage(
  messages: DirectorMessage[],
  id: string,
  delivery: DirectorMessage['delivery'],
): DirectorMessage[] {
  return messages.map((message) => message.id === id ? { ...message, delivery } : message)
}

function ReadyCard({
  content,
  summary,
  busy,
  onCopy,
  onRevise,
}: {
  content: ReadyContent
  summary?: boolean
  busy: boolean
  onCopy: () => void
  onRevise: () => void
}) {
  return (
    <section className={`ready-card${summary ? ' ready-summary' : ''}`}>
      <div className="ready-card-heading">
        <div>
          <span className="eyebrow">{summary ? '上一版最终内容摘要' : '最终内容'}</span>
          <h2>{content.title}</h2>
        </div>
        {!summary && <span className="ready-badge">可以拍了</span>}
      </div>
      <div className="ready-section">
        <h3>口播稿</h3>
        <p>{summary ? `${content.script_text.slice(0, 180)}${content.script_text.length > 180 ? '…' : ''}` : content.script_text}</p>
      </div>
      {!summary && (
        <div className="ready-actions">
          <button className="primary-button" type="button" onClick={onCopy}>复制脚本</button>
          <button className="secondary-button" type="button" onClick={onRevise} disabled={busy}>继续修改</button>
        </div>
      )}
    </section>
  )
}

export default function DirectorApp() {
  const [state, setState] = useState<DirectorLocalState>(() => loadDirectorState())
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<Notice | null>(null)
  const [entryMode, setEntryMode] = useState<'DISCOVER' | 'IDEA' | null>(null)
  const stateRef = useRef(state)
  const endOfMessagesRef = useRef<HTMLDivElement>(null)
  const composerRef = useRef<HTMLTextAreaElement>(null)

  function commit(updater: (current: DirectorLocalState) => DirectorLocalState): DirectorLocalState {
    const next = { ...updater(stateRef.current), updated_at: new Date().toISOString() }
    stateRef.current = next
    setState(next)
    saveDirectorState(next)
    return next
  }

  useEffect(() => {
    endOfMessagesRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
  }, [state.messages.length, busy])

  useEffect(() => {
    if (!state.pending_request) return
    setNotice({
      kind: 'info',
      text: '上次请求结果未知，可使用原消息重新尝试。',
      retryable: state.status !== 'blocked' && state.status !== 'ready',
    })
  }, [])

  async function submitPending(pending: PendingRequest): Promise<void> {
    if (busy) return
    setBusy(true)
    setNotice(null)
    commit((current) => ({
      ...current,
      messages: updateMessage(current.messages, pending.client_message_id, 'sending'),
    }))

    try {
      let current = stateRef.current
      let projectId = current.project_id
      if (!projectId) {
        const project = await createDirectorProject(projectNameFromMessage(pending.content))
        projectId = project.id
        current = commit((latest) => ({ ...latest, project_id: projectId, status: 'active' }))
      }

      let sessionId = current.session_id
      if (!sessionId) {
        const session = await createDirectorSession(projectId, current.source_ready_content_id ?? undefined)
        sessionId = session.session_id
        current = commit((latest) => ({
          ...latest,
          session_id: sessionId,
          state_version: session.state_version,
          source_ready_content_id: session.source_ready_content_id,
          status: 'active',
        }))
      }

      const response = await submitDirectorMessage(projectId, sessionId, pending)
      commit((latest) => applySuccessfulTurn(latest, pending.client_message_id, response))
      setNotice(response.status === 'READY'
        ? { kind: 'success', text: '这段内容已经准备好了。' }
        : null)
    } catch (reason) {
      handleSubmitError(reason)
    } finally {
      setBusy(false)
    }
  }

  function handleSubmitError(reason: unknown): void {
    const pending = stateRef.current.pending_request
    if (!pending) return

    const isApiError = reason instanceof DirectorApiError
    const outcome = classifySubmitError({
      status: isApiError ? reason.status : null,
      code: isApiError ? reason.code : null,
      message: isApiError ? reason.message : '请求未完成，请稍后重试。',
      network: !isApiError,
    }, Boolean(stateRef.current.ready_content))
    commit((current) => applyFailedTurn(current, pending.client_message_id, outcome))
    setNotice({ kind: outcome.status === 'ready' ? 'info' : 'error', text: outcome.message, retryable: outcome.retryable })
  }

  function queueOwnerMessage(content: string, parameters: Record<string, unknown>): void {
    const current = stateRef.current
    if (!content || busy || current.pending_request || current.status === 'ready' || current.status === 'blocked') return

    const pending: PendingRequest = {
      client_message_id: newClientMessageId(),
      expected_state_version: current.state_version,
      content,
      parameters,
    }
    commit((latest) => ({
      ...latest,
      status: 'active',
      pending_request: pending,
      messages: [...latest.messages, {
        id: pending.client_message_id,
        role: 'OWNER',
        content,
        delivery: 'sending',
      }],
    }))
    setDraft('')
    void submitPending(pending)
  }

  function handleSend(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault()
    const content = draft.trim()
    const current = stateRef.current
    const isFirstTurn = current.state_version === 0 && current.messages.length === 0
    queueOwnerMessage(content, isFirstTurn ? initialEntryParameters(entryMode ?? 'IDEA') : {})
  }

  function startDiscover(): void {
    setEntryMode('DISCOVER')
    queueOwnerMessage('请帮我找一个现在最值得拍的方向。', initialEntryParameters('DISCOVER'))
  }

  function startWithIdea(): void {
    setEntryMode('IDEA')
    composerRef.current?.focus()
  }

  function selectDirection(option: DirectionOption): void {
    const payload = directionSelectionPayload(option)
    queueOwnerMessage(payload.content, payload.parameters)
  }

  function retryPending(): void {
    const pending = pendingForRetry(stateRef.current)
    if (!pending || busy) return
    void submitPending(pending)
  }

  function startNewConversation(): void {
    if (busy) return
    const current = stateRef.current
    if (hasDirectorData(current) && !window.confirm('新建对话后，当前聊天记录将无法恢复。确定继续吗？')) return
    clearDirectorState()
    const next = clearDirectorStateIfConfirmed(current, true)
    stateRef.current = next
    setState(next)
    setDraft('')
    setEntryMode(null)
    setNotice(null)
  }

  async function copyReadyContent(): Promise<void> {
    const content = stateRef.current.ready_content
    if (!content) return
    const text = readyContentText(content)
    try {
      await navigator.clipboard.writeText(text)
      setNotice({ kind: 'success', text: '脚本已复制。' })
    } catch {
      setNotice({ kind: 'error', text: '复制失败，请手动选择内容复制。' })
    }
  }

  async function continueRevision(): Promise<void> {
    const current = stateRef.current
    const sourceId = current.ready_content?.id
    if (!current.project_id || !sourceId || busy) return
    setBusy(true)
    setNotice(null)
    try {
      const session = await createDirectorSession(current.project_id, sourceId)
      commit((latest) => applyRevisionSession(latest, session))
      setNotice({ kind: 'info', text: '正在基于上一版继续修改，请告诉我你想改什么。' })
    } catch (reason) {
      setNotice({ kind: 'error', text: reason instanceof DirectorApiError ? reason.message : '网络连接中断，请稍后重试。', retryable: true })
    } finally {
      setBusy(false)
    }
  }

  const canCompose = !busy && state.status !== 'ready' && state.status !== 'blocked' && !state.pending_request
  const pending = state.pending_request
  return (
    <div className="director-app">
      <header className="director-header">
        <div className="director-brand">
          <span className="director-brand-mark">食</span>
          <div>
            <strong>Food-IP AI 编导</strong>
            <span>把真实想法，变成值得拍的内容</span>
          </div>
        </div>
        <button className="new-chat-button" type="button" onClick={startNewConversation} disabled={busy}>新对话</button>
      </header>

      <main className="director-main">
        {state.previous_ready_content && <ReadyCard content={state.previous_ready_content} summary busy={busy} onCopy={() => void copyReadyContent()} onRevise={() => void continueRevision()} />}

        <section className="chat-panel" aria-label="聊天记录">
          {state.messages.length === 0 ? (
            <div className="director-empty-state">
              <span className="empty-mark">✦</span>
              <p>这次从哪里开始？</p>
              <div className="entry-actions">
                <button type="button" className="entry-card recommended" onClick={startDiscover} disabled={busy}>
                  <strong>帮我找方向</strong><span>让 AI 编导先判断现在最值得拍什么</span>
                </button>
                <button type="button" className="entry-card" onClick={startWithIdea} disabled={busy}>
                  <strong>我已有想法</strong><span>说说你的想法，先判断再创作</span>
                </button>
              </div>
            </div>
          ) : (
            <div className="message-list">
              {state.messages.map((message) => (
                <article key={message.id} className={`chat-message ${message.role.toLowerCase()}`}>
                  <span className="message-role">{message.role === 'OWNER' ? '你' : 'AI 编导'}</span>
                  <div className="message-bubble">
                    <p>{message.content}</p>
                    {message.delivery === 'failed' && <small>发送未完成</small>}
                  </div>
                </article>
              ))}
              {busy && <div className="thinking-indicator" role="status" aria-live="polite"><span className="thinking-dot" />AI 编导正在思考</div>}
              <div ref={endOfMessagesRef} />
            </div>
          )}
        </section>

        {state.interaction?.kind === 'DIRECTION_SELECTION' && state.status === 'active' && (
          <section className="direction-panel" aria-label="方向选择">
            <div className="direction-heading"><span className="eyebrow">选择内容方向</span><strong>一个首推，两个备选</strong></div>
            <div className="direction-grid">
              {state.interaction.options.map((option) => (
                <button key={option.id} type="button" className={`direction-card${option.recommended ? ' recommended' : ''}`} onClick={() => selectDirection(option)} disabled={!canCompose}>
                  {option.recommended && <span className="recommend-badge">首推</span>}
                  <strong>{option.direction}</strong>
                  <span>{option.reason}</span>
                </button>
              ))}
            </div>
          </section>
        )}

        {state.status === 'ready' && state.ready_content && (
          <ReadyCard content={state.ready_content} busy={busy} onCopy={() => void copyReadyContent()} onRevise={() => void continueRevision()} />
        )}

        {notice && (
          <div className={`director-notice ${notice.kind}`} role={notice.kind === 'error' ? 'alert' : 'status'}>
            <span>{notice.text}</span>
            {notice.retryable && pending && <button type="button" onClick={retryPending} disabled={busy}>重试</button>}
          </div>
        )}

        <form className="director-composer" onSubmit={handleSend}>
          <textarea
            ref={composerRef}
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            placeholder={state.status === 'blocked' ? '请新建对话后继续' : state.status === 'ready' ? '这段对话已经完成' : entryMode === 'IDEA' ? '说说你已有的内容想法…' : '写下你想拍的内容或遇到的问题…'}
            disabled={!canCompose}
            rows={3}
            aria-label="输入消息"
          />
          <button className="send-button" type="submit" disabled={!canCompose || !draft.trim()}>发送</button>
        </form>
      </main>
    </div>
  )
}
