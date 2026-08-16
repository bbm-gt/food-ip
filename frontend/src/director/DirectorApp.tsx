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
  emptyDirectorState,
  loadDirectorState,
  saveDirectorState,
} from './directorStorage'
import type {
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

function notesText(notes: ReadyContent['shooting_notes']): string {
  return Array.isArray(notes) ? notes.join('\n') : notes
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
        <h3>脚本正文</h3>
        <p>{summary ? `${content.script_text.slice(0, 180)}${content.script_text.length > 180 ? '…' : ''}` : content.script_text}</p>
      </div>
      <div className="ready-section">
        <h3>拍摄建议</h3>
        <p>{summary ? `${Array.isArray(content.shooting_notes) ? content.shooting_notes.length : 1} 条建议` : notesText(content.shooting_notes)}</p>
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
  const stateRef = useRef(state)
  const endOfMessagesRef = useRef<HTMLDivElement>(null)

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
      commit((latest) => {
        const nextMessages = updateMessage(latest.messages, pending.client_message_id, 'sent')
        if (!nextMessages.some((message) => message.id === response.message.id)) {
          nextMessages.push({
            id: response.message.id,
            role: 'DIRECTOR',
            content: response.message.content,
            delivery: 'sent',
          })
        }
        return {
          ...latest,
          state_version: response.state_version,
          status: response.status === 'READY' ? 'ready' : 'active',
          messages: nextMessages,
          ready_content: response.ready_content ?? latest.ready_content,
          pending_request: null,
        }
      })
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
    const status = isApiError ? reason.status : null
    const code = isApiError ? reason.code : null
    const apiMessage = isApiError ? reason.message : '请求未完成，请稍后重试。'
    const markFailed = (current: DirectorLocalState) => ({
      ...current,
      messages: updateMessage(current.messages, pending.client_message_id, 'failed'),
    })

    if (code === 'idempotency_conflict') {
      commit((current) => ({ ...markFailed(current), pending_request: null }))
      setNotice({ kind: 'error', text: '该消息请求发生冲突，请重新发送。' })
      return
    }
    if (code === 'state_version_conflict') {
      commit((current) => ({ ...markFailed(current), status: 'blocked' }))
      setNotice({ kind: 'error', text: '该对话可能已在其他页面更新，请新建对话后继续。' })
      return
    }
    if (code === 'session_ready') {
      commit((current) => ({ ...markFailed(current), status: 'ready' }))
      setNotice({ kind: 'info', text: '该对话已经完成，不能继续发送。' })
      return
    }

    commit(markFailed)
    if (status === 502) {
      setNotice({ kind: 'error', text: '模型输出暂时不可用。', retryable: true })
    } else if (status === 503) {
      setNotice({ kind: 'error', text: '服务暂时不可用。', retryable: true })
    } else if (!isApiError) {
      setNotice({ kind: 'error', text: '网络连接中断，结果可能未知。', retryable: true })
    } else {
      setNotice({ kind: 'error', text: apiMessage, retryable: true })
    }
  }

  function handleSend(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault()
    const content = draft.trim()
    const current = stateRef.current
    if (!content || busy || current.pending_request || current.status === 'ready' || current.status === 'blocked') return

    const pending: PendingRequest = {
      client_message_id: newClientMessageId(),
      expected_state_version: current.state_version,
      content,
      parameters: {},
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

  function retryPending(): void {
    const pending = stateRef.current.pending_request
    if (!pending || busy || stateRef.current.status === 'blocked' || stateRef.current.status === 'ready') return
    void submitPending(pending)
  }

  function startNewConversation(): void {
    if (busy) return
    clearDirectorState()
    const next = emptyDirectorState()
    stateRef.current = next
    setState(next)
    setDraft('')
    setNotice(null)
  }

  async function copyReadyContent(): Promise<void> {
    const content = stateRef.current.ready_content
    if (!content) return
    const text = [content.title, content.script_text, '拍摄建议', notesText(content.shooting_notes)].join('\n\n')
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
      commit((latest) => ({
        ...latest,
        session_id: session.session_id,
        state_version: 0,
        status: 'active',
        messages: [],
        pending_request: null,
        source_ready_content_id: session.source_ready_content_id,
      }))
      setNotice({ kind: 'info', text: '正在基于上一版继续修改，请告诉我你想改什么。' })
    } catch (reason) {
      setNotice({ kind: 'error', text: reason instanceof DirectorApiError ? reason.message : '网络连接中断，请稍后重试。', retryable: true })
    } finally {
      setBusy(false)
    }
  }

  const canCompose = !busy && state.status !== 'ready' && state.status !== 'blocked' && !state.pending_request
  const pending = state.pending_request
  const showPreviousSummary = state.status !== 'ready' && state.ready_content

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
        {showPreviousSummary && state.ready_content && <ReadyCard content={state.ready_content} summary busy={busy} onCopy={() => void copyReadyContent()} onRevise={() => void continueRevision()} />}

        <section className="chat-panel" aria-label="Director 聊天记录">
          {state.messages.length === 0 ? (
            <div className="director-empty-state">
              <span className="empty-mark">✦</span>
              <p>告诉我你最近想拍什么、遇到了什么问题，<br />或者让我帮你找一个值得拍的方向。</p>
            </div>
          ) : (
            <div className="message-list">
              {state.messages.map((message) => (
                <article key={message.id} className={`chat-message ${message.role.toLowerCase()}`}>
                  <span className="message-role">{message.role}</span>
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
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            placeholder={state.status === 'blocked' ? '请新建对话后继续' : state.status === 'ready' ? '这段对话已经完成' : '写下你想拍的内容或遇到的问题…'}
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
