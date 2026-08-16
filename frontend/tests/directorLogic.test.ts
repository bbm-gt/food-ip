import assert from 'node:assert/strict'
import test from 'node:test'

import {
  applyFailedTurn,
  applyRevisionSession,
  classifySubmitError,
  clearDirectorStateIfConfirmed,
  emptyDirectorState,
  normalizeLoadedDirectorState,
  pendingForRetry,
} from '../src/director/directorLogic.ts'

function pending() {
  return {
    client_message_id: '11111111-1111-4111-8111-111111111111',
    expected_state_version: 3,
    content: '我想拍一道每天现做的面。',
    parameters: { source: 'owner' },
  }
}

test('refresh keeps pending request and makes the original request retryable', () => {
  const request = pending()
  const loaded = normalizeLoadedDirectorState({
    ...emptyDirectorState('2026-08-16T00:00:00.000Z'),
    status: 'active',
    messages: [{ id: request.client_message_id, role: 'OWNER', content: request.content, delivery: 'sending' }],
    pending_request: request,
  })

  assert.deepEqual(loaded.pending_request, request)
  assert.equal(loaded.messages[0].delivery, 'failed')
  assert.deepEqual(pendingForRetry(loaded), request)
})

test('retry returns the exact pending request without creating a new message id', () => {
  const request = pending()
  const state = { ...emptyDirectorState(), status: 'active' as const, pending_request: request }
  const retry = pendingForRetry(state)

  assert.strictEqual(retry, request)
  assert.equal(retry?.client_message_id, request.client_message_id)
  assert.equal(retry?.expected_state_version, request.expected_state_version)
  assert.equal(retry?.content, request.content)
  assert.deepEqual(retry?.parameters, request.parameters)
})

test('revision moves current ready content to previous content and clears current content', () => {
  const ready = {
    id: 'ready-1',
    title: '上一版标题',
    script_text: '上一版正文',
    shooting_notes: ['上一版建议'],
  }
  const revised = applyRevisionSession(
    { ...emptyDirectorState(), status: 'ready' as const, ready_content: ready },
    { session_id: 'session-2', state_version: 0, source_ready_content_id: ready.id },
  )

  assert.deepEqual(revised.previous_ready_content, ready)
  assert.equal(revised.ready_content, null)
  assert.equal(revised.session_id, 'session-2')
  assert.equal(revised.state_version, 0)
  assert.deepEqual(revised.messages, [])
})

test('session_ready without current content blocks without promoting previous content', () => {
  const previous = { id: 'ready-1', title: '旧标题', script_text: '旧正文', shooting_notes: [] }
  const state = { ...emptyDirectorState(), status: 'active' as const, previous_ready_content: previous, pending_request: pending() }
  const outcome = classifySubmitError({ status: 409, code: 'session_ready', message: 'done' }, false)
  const next = applyFailedTurn(state, state.pending_request!.client_message_id, outcome)

  assert.equal(next.status, 'blocked')
  assert.equal(next.ready_content, null)
  assert.deepEqual(next.previous_ready_content, previous)
  assert.equal(outcome.message, '该对话已在其他页面完成，但当前浏览器无法恢复最新结果，请新建对话。')
})

test('cancelling new conversation confirmation keeps all data', () => {
  const state = {
    ...emptyDirectorState(),
    status: 'active' as const,
    messages: [{ id: 'message-1', role: 'OWNER' as const, content: '保留我', delivery: 'sent' as const }],
  }

  assert.strictEqual(clearDirectorStateIfConfirmed(state, false), state)
  assert.equal(clearDirectorStateIfConfirmed(state, true).messages.length, 0)
})

test('404 and 422 outcomes do not offer retry, while 500 remains retryable', () => {
  const notFound = classifySubmitError({ status: 404, code: null, message: 'not found' }, false)
  const invalid = classifySubmitError({ status: 422, code: null, message: 'invalid' }, false)
  const serverError = classifySubmitError({ status: 500, code: null, message: 'error' }, false)

  assert.equal(notFound.retryable, false)
  assert.equal(notFound.message, '资源已失效，需要新建对话。')
  assert.equal(invalid.retryable, false)
  assert.equal(serverError.retryable, true)
})
