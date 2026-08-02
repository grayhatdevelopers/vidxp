import assert from 'node:assert/strict'
import test from 'node:test'

import {
  isServerTransferComplete,
  needsFileAuthorization,
  resumePollingForNewFile,
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

test('adding a file restarts polling after current work completed', () => {
  const completed = {
    session_state: 'open',
    terminal: true,
    poll_after_seconds: 0,
  }
  const resumed = resumePollingForNewFile(completed)

  assert.equal(resumed.session_state, 'open')
  assert.equal(resumed.terminal, false)
  assert.equal(resumed.poll_after_seconds, 2)
  assert.equal(completed.terminal, true)
})
