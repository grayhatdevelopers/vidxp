import Uppy from '@uppy/core'
import Dashboard from '@uppy/dashboard'
import GoldenRetriever from '@uppy/golden-retriever'
import Tus from '@uppy/tus'

import '@uppy/core/css/style.min.css'
import '@uppy/dashboard/css/style.min.css'
import './app.css'

const elements = {
  summary: document.querySelector('#summary'),
  expectedFilename: document.querySelector('#expected-filename'),
  expectedSize: document.querySelector('#expected-size'),
  maximumSize: document.querySelector('#maximum-size'),
  expiresAt: document.querySelector('#expires-at'),
  uploadState: document.querySelector('#upload-state'),
  intentState: document.querySelector('#intent-state'),
  nextAction: document.querySelector('#next-action'),
  intentId: document.querySelector('#intent-id'),
  jobId: document.querySelector('#job-id'),
  mediaId: document.querySelector('#media-id'),
}

const TERMINAL_STATES = new Set(['ready', 'failed', 'expired'])
const TRANSFER_STATES = new Set(['pending', 'accepted', 'uploading'])
const POLL_INTERVAL_MS = 2000

let uppy
let expected
let creationUrl
let resumeUrl
let creationGrant
let pollTimer
let pollInFlight = false
let currentIntentState = 'pending'

function setText(element, value) {
  element.textContent = value == null || value === '' ? '—' : String(value)
}

function setUploadMessage(message, kind = '') {
  setText(elements.uploadState, message)
  elements.uploadState.classList.remove('error', 'success')
  if (kind) elements.uploadState.classList.add(kind)
}

function formatBytes(value) {
  if (!Number.isFinite(value)) return '—'
  if (value === 0) return '0 bytes'

  const units = ['bytes', 'KiB', 'MiB', 'GiB', 'TiB']
  const exponent = Math.min(
    Math.floor(Math.log(value) / Math.log(1024)),
    units.length - 1,
  )
  const amount = value / 1024 ** exponent
  const digits = exponent === 0 || amount >= 10 ? 0 : 1
  return `${amount.toFixed(digits)} ${units[exponent]}`
}

function formatDate(value) {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return String(value)
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(date)
}

function readCapabilityFragment() {
  const fragment = window.location.hash.slice(1)
  if (!fragment) return null

  const parameters = new URLSearchParams(fragment)
  const named = parameters.get('capability')
  if (named) return named

  if (!fragment.includes('=')) {
    try {
      return decodeURIComponent(fragment)
    } catch {
      return null
    }
  }
  return null
}

function clearFragment() {
  window.history.replaceState(
    null,
    document.title,
    `${window.location.pathname}${window.location.search}`,
  )
}

function apiUrl(relative) {
  const pageBase = new URL(`${window.location.pathname}/`, window.location.origin)
  return new URL(relative.replace(/^\.\//, ''), pageBase).href
}

function safeServerMessage(payload, fallback) {
  const candidate =
    payload?.error?.message ??
    payload?.error?.detail?.message ??
    payload?.message ??
    fallback
  return typeof candidate === 'string' && candidate.length <= 300
    ? candidate
    : fallback
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, {
    cache: 'no-store',
    credentials: 'same-origin',
    ...options,
    headers: {
      Accept: 'application/json',
      ...(options.body ? { 'Content-Type': 'application/json' } : {}),
      ...options.headers,
    },
  })

  let payload
  try {
    payload = await response.json()
  } catch {
    payload = null
  }

  if (!response.ok) {
    throw new Error(
      safeServerMessage(payload, `VidXP rejected the request (${response.status}).`),
    )
  }
  return payload ?? {}
}

function statusRecord(envelope) {
  const status = envelope?.status ?? envelope ?? {}
  return status.intent ?? status
}

function firstDefined(record, names) {
  for (const name of names) {
    if (record?.[name] != null) return record[name]
  }
  return null
}

function expectedRecord(envelope) {
  const record = statusRecord(envelope)
  const byteSize = Number(
    firstDefined(record, ['byte_size', 'declared_byte_size', 'size']),
  )
  const maximumBytes = Number(
    firstDefined(record, [
      'configured_maximum_bytes',
      'maximum_bytes',
      'max_bytes',
    ]),
  )
  return {
    intentId: firstDefined(record, ['intent_id', 'id']),
    filename: firstDefined(record, [
      'original_filename',
      'filename',
      'declared_filename',
    ]),
    byteSize,
    maximumBytes,
    expiresAt: firstDefined(record, ['expires_at', 'expiry', 'expires']),
  }
}

function renderExpectedFile() {
  setText(elements.expectedFilename, expected.filename)
  setText(elements.expectedSize, formatBytes(expected.byteSize))
  setText(elements.maximumSize, formatBytes(expected.maximumBytes))
  setText(elements.expiresAt, formatDate(expected.expiresAt))
  setText(
    elements.summary,
    `Select ${expected.filename} (${formatBytes(expected.byteSize)}) to upload directly to VidXP.`,
  )
}

function statusGuidance(record) {
  return firstDefined(record, [
    'next_action',
    'guidance',
    'message',
    'status_message',
  ])
}

function applyStatus(envelope) {
  const record = statusRecord(envelope)
  const state = String(record.state ?? record.status ?? 'pending').toLowerCase()
  currentIntentState = state

  setText(elements.intentState, state)
  setText(elements.intentId, firstDefined(record, ['intent_id', 'id']))
  setText(elements.jobId, record.job_id)
  setText(elements.mediaId, record.media_id)
  setText(
    elements.nextAction,
    statusGuidance(record) ?? 'VidXP is waiting for the upload to continue.',
  )

  if (record.media_id) {
    setUploadMessage('The video is ready in VidXP.', 'success')
  } else if (state === 'failed' || state === 'expired') {
    setUploadMessage(
      state === 'expired'
        ? 'This upload handoff has expired.'
        : 'VidXP could not finish importing this upload.',
      'error',
    )
  }
  uppy?.getPlugin('Dashboard')?.setOptions({
    disabled: !TRANSFER_STATES.has(state),
  })
  return state
}

function activeFile() {
  return uppy?.getFiles()[0] ?? null
}

function validateExpectedFile(file) {
  if (file.name !== expected.filename) {
    return `Choose the expected file named ${expected.filename}.`
  }
  if (file.size !== expected.byteSize) {
    return `The selected file is ${formatBytes(file.size)}; VidXP expects ${formatBytes(expected.byteSize)}.`
  }
  return null
}

function normalizedUrl(value) {
  const url = new URL(value, window.location.href)
  url.hash = ''
  return url.href
}

function isCreationRequest(request) {
  return (
    request.getMethod().toUpperCase() === 'POST' &&
    normalizedUrl(request.getURL()) === creationUrl
  )
}

function responseGrant(payload) {
  return payload?.grant ?? payload?.creation_grant ?? payload?.capability ?? null
}

async function getCreationGrant() {
  if (creationGrant) return creationGrant

  const payload = await requestJson(apiUrl('./creation-grant'), {
    method: 'POST',
    body: '{}',
  })
  if (payload.status) applyStatus(payload)
  const grant = responseGrant(payload)
  if (typeof grant !== 'string' || !grant) {
    throw new Error('VidXP did not issue an upload creation grant.')
  }
  creationGrant = grant
  return grant
}

function recoveryLifetime() {
  const expiry = new Date(expected.expiresAt).getTime()
  if (!Number.isFinite(expiry)) return 60 * 60 * 1000
  return Math.max(1000, expiry - Date.now())
}

function safeUploadError(error) {
  const code = error?.originalResponse?.getHeader?.('X-VidXP-Error')
  if (typeof code === 'string' && /^[a-z0-9_-]{1,80}$/i.test(code)) {
    return `VidXP rejected the upload (${code}).`
  }
  return 'The transfer was interrupted. Check the connection and retry.'
}

function applyResumeUrl(value) {
  if (!value || !uppy) return
  resumeUrl = normalizedUrl(value)
  uppy.getPlugin('Tus')?.setOptions({ uploadUrl: resumeUrl })

  const file = activeFile()
  if (file && file.tus?.uploadUrl !== resumeUrl) {
    uppy.setFileState(file.id, {
      tus: { ...file.tus, uploadUrl: resumeUrl },
    })
  }
}

function configureUppy() {
  const scopedId = `vidxp-upload-${String(expected.intentId).replace(/[^a-z0-9_-]/gi, '_')}`
  uppy = new Uppy({
    id: scopedId,
    autoProceed: false,
    meta: { intent_id: String(expected.intentId) },
    restrictions: {
      maxNumberOfFiles: 1,
      minFileSize: expected.byteSize,
      maxFileSize: expected.byteSize,
    },
    onBeforeFileAdded(file) {
      const problem = validateExpectedFile(file)
      if (problem) setUploadMessage(problem, 'error')
      return problem == null
    },
  })

  uppy.on('file-added', () => {
    setUploadMessage('The expected file is ready to upload.')
  })
  uppy.on('file-removed', () => {
    setUploadMessage('Choose the expected file to begin.')
  })
  uppy.on('restriction-failed', () => {
    setUploadMessage('The selected file does not match this upload handoff.', 'error')
  })
  uppy.on('upload-pause', (_file, isPaused) => {
    setUploadMessage(isPaused ? 'Upload paused.' : 'Upload resumed.')
  })
  uppy.on('upload-error', (_file, error) => {
    creationGrant = null
    setUploadMessage(safeUploadError(error), 'error')
  })
  uppy.on('upload-success', () => {
    setUploadMessage('Transfer complete. VidXP is validating and importing the video.', 'success')
    scheduleStatusPoll(0)
  })
  uppy.on('restored', () => {
    setUploadMessage('The previous upload session was restored.')
    scheduleStatusPoll(0)
  })

  uppy.use(Dashboard, {
    target: '#uppy-dashboard',
    inline: true,
    height: 360,
    theme: 'dark',
    showProgressDetails: true,
    proudlyDisplayPoweredByUppy: false,
    disabled: !TRANSFER_STATES.has(currentIntentState),
    note: `Only ${expected.filename} (${formatBytes(expected.byteSize)}) is accepted.`,
  })

  uppy.use(Tus, {
    endpoint: creationUrl,
    ...(resumeUrl ? { uploadUrl: normalizedUrl(resumeUrl) } : {}),
    allowedMetaFields: ['intent_id'],
    parallelUploads: 1,
    overridePatchMethod: false,
    uploadDataDuringCreation: false,
    withCredentials: false,
    removeFingerprintOnSuccess: true,
    async onBeforeRequest(request) {
      if (!isCreationRequest(request)) return
      const grant = await getCreationGrant()
      request.setHeader('Authorization', `VidXP-Handoff ${grant}`)
    },
    onAfterResponse(request) {
      if (isCreationRequest(request)) creationGrant = null
    },
  })
  uppy.use(GoldenRetriever, {
    id: `GoldenRetriever:${scopedId}`,
    expires: recoveryLifetime(),
    serviceWorker: false,
  })

}

async function pollStatus() {
  if (pollInFlight) return
  pollInFlight = true
  try {
    const payload = await requestJson(apiUrl('./status'))
    applyResumeUrl(payload.resume_url)
    const state = applyStatus(payload)
    if (!TERMINAL_STATES.has(state)) scheduleStatusPoll(POLL_INTERVAL_MS)
  } catch {
    setText(
      elements.nextAction,
      'The status check failed. This page will try again automatically.',
    )
    scheduleStatusPoll(POLL_INTERVAL_MS)
  } finally {
    pollInFlight = false
  }
}

function scheduleStatusPoll(delay = POLL_INTERVAL_MS) {
  window.clearTimeout(pollTimer)
  pollTimer = window.setTimeout(pollStatus, delay)
}

async function bootstrap() {
  const capability = readCapabilityFragment()
  const payload = capability
    ? await requestJson(apiUrl('./bootstrap'), {
        method: 'POST',
        body: JSON.stringify({ capability }),
      })
    : await requestJson(apiUrl('./status'))
  if (capability) clearFragment()
  expected = expectedRecord(payload)
  if (
    !expected.intentId ||
    !expected.filename ||
    !Number.isFinite(expected.byteSize) ||
    expected.byteSize < 0
  ) {
    throw new Error('VidXP returned an incomplete upload handoff.')
  }
  if (!Number.isFinite(expected.maximumBytes)) {
    expected.maximumBytes = expected.byteSize
  }
  if (!payload.creation_url) {
    throw new Error('VidXP did not provide the tus creation endpoint.')
  }

  creationUrl = normalizedUrl(payload.creation_url)
  resumeUrl = payload.resume_url ? normalizedUrl(payload.resume_url) : null
  creationGrant = responseGrant(payload)

  renderExpectedFile()
  applyStatus(payload)
  configureUppy()
  scheduleStatusPoll(0)
}

bootstrap().catch((error) => {
  const message = error instanceof Error ? error.message : 'The upload page could not start.'
  setText(elements.summary, message)
  setUploadMessage(message, 'error')
  setText(elements.nextAction, 'Request a new upload handoff from VidXP.')
})
