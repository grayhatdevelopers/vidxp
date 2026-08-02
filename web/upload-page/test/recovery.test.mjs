import assert from 'node:assert/strict'
import test from 'node:test'

import {
  isServerTransferComplete,
  needsFileAuthorization,
  resumePollingAfterFileAuthorization,
  shouldPollSession,
} from '../src/recovery.js'

test('reload before tus creation requests a fresh one-use grant', () => {
  const restored = { meta: { intent_id: 'intent-1' } }
  assert.equal(needsFileAuthorization(restored, { state: 'pending' }), true)
})

test('reload after tus creation reuses the durable upload URL', () => {
  const restored = {
    meta: { intent_id: 'intent-1' },
    tus: { uploadUrl: 'https://uploads.example/uploads/upload-1' },
  }
  assert.equal(needsFileAuthorization(restored, { state: 'accepted' }), false)
})

test('completed transfer recovery never creates another upload', () => {
  for (const state of ['processing', 'ready', 'indexed', 'failed', 'expired']) {
    const child = { state }
    assert.equal(isServerTransferComplete(child), true)
    assert.equal(needsFileAuthorization({ meta: { intent_id: 'intent-1' } }, child), false)
  }
})

test('accepted file restarts polling after current work completed', () => {
  const completed = {
    session_state: 'open',
    terminal: true,
    poll_after_seconds: 0,
  }
  const resumed = resumePollingAfterFileAuthorization(completed, {
    intent_id: 'intent-2',
  })

  assert.equal(resumed.session_state, 'open')
  assert.equal(resumed.terminal, false)
  assert.equal(resumed.poll_after_seconds, 2)
  assert.equal(completed.terminal, true)
})

test('pre-authorization terminal response cannot suppress accepted-file polling', () => {
  const completed = {
    session_state: 'open',
    terminal: true,
    poll_after_seconds: 0,
  }

  // Local selection is not server acceptance. An intervening status response
  // therefore remains terminal until the file authorization succeeds.
  const preAuthorization = { ...completed }
  assert.equal(shouldPollSession(preAuthorization), false)

  const accepted = resumePollingAfterFileAuthorization(preAuthorization, {
    intent_id: 'intent-2',
    state: 'pending',
  })
  assert.equal(shouldPollSession(accepted), true)

  const processing = { ...accepted, terminal: false, poll_after_seconds: 2 }
  assert.equal(shouldPollSession(processing), true)
  const finished = { ...processing, terminal: true, poll_after_seconds: 0 }
  assert.equal(shouldPollSession(finished), false)
})

test('rejected authorization does not resume terminal-session polling', () => {
  const completed = { session_state: 'open', terminal: true }
  const rejected = resumePollingAfterFileAuthorization(completed, {
    error: { code: 'upload_quota_exceeded' },
  })

  assert.equal(rejected, completed)
  assert.equal(shouldPollSession(rejected), false)
})

test('each additionally accepted file can resume an open terminal session', () => {
  let status = { session_state: 'open', terminal: true, poll_after_seconds: 0 }
  for (const intent_id of ['intent-2', 'intent-3', 'intent-4']) {
    status = resumePollingAfterFileAuthorization(status, { intent_id })
    assert.equal(shouldPollSession(status), true)
    status = { ...status, terminal: true, poll_after_seconds: 0 }
  }
})
