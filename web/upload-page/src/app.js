import Uppy from '@uppy/core'
import Dashboard from '@uppy/dashboard'
import GoldenRetriever from '@uppy/golden-retriever'
import Tus from '@uppy/tus'
import XHRUpload from '@uppy/xhr-upload'

import '@uppy/core/css/style.min.css'
import '@uppy/dashboard/css/style.min.css'
import './app.css'
import {
  isServerTransferComplete,
  needsFileAuthorization,
  resumePollingAfterFileAuthorization,
  shouldPollSession,
} from './recovery.js'

const elements = {
  summary: document.querySelector('#summary'),
  sessionState: document.querySelector('#session-state'),
  fileCount: document.querySelector('#file-count'),
  maximumFileSize: document.querySelector('#maximum-file-size'),
  maximumSessionSize: document.querySelector('#maximum-session-size'),
  transferBackend: document.querySelector('#transfer-backend'),
  reservedSummary: document.querySelector('#reserved-summary'),
  uploadedSummary: document.querySelector('#uploaded-summary'),
  expiresAt: document.querySelector('#expires-at'),
  closeSession: document.querySelector('#close-session'),
  uploadState: document.querySelector('#upload-state'),
  nextAction: document.querySelector('#next-action'),
  sessionFiles: document.querySelector('#session-files'),
  transferHint: document.querySelector('#transfer-hint'),
}

const POLL_INTERVAL_MS = 2000
const CANCELLABLE_STATES = new Set(['pending', 'accepted', 'failed'])

let uppy
let sessionStatus
let creationUrl
let pollTimer
let pollInFlight = false
const creationGrants = new Map()

function setText(element, value) {
  element.textContent = value == null || value === '' ? '—' : String(value)
}

function setUploadMessage(message, kind = '') {
  setText(elements.uploadState, message)
  elements.uploadState.classList.remove('error', 'success')
  if (kind) elements.uploadState.classList.add(kind)
}

function formatBytes(value) {
  const size = Number(value)
  if (!Number.isFinite(size)) return '—'
  if (size === 0) return '0 bytes'
  const units = ['bytes', 'KiB', 'MiB', 'GiB', 'TiB']
  const exponent = Math.min(
    Math.floor(Math.log(size) / Math.log(1024)),
    units.length - 1,
  )
  const amount = size / 1024 ** exponent
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

function randomClientFileKey() {
  if (typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID().replaceAll('-', '')
  }
  const bytes = crypto.getRandomValues(new Uint8Array(16))
  return Array.from(bytes, (value) => value.toString(16).padStart(2, '0')).join('')
}

function childByKey(key) {
  return sessionStatus?.items?.find((item) => item.client_file_key === key) ?? null
}

function renderFiles() {
  elements.sessionFiles.replaceChildren()
  const items = sessionStatus?.items ?? []
  if (items.length === 0) {
    const empty = document.createElement('li')
    empty.className = 'empty-state'
    empty.textContent = 'No files have been selected for this session yet.'
    elements.sessionFiles.append(empty)
    return
  }
  for (const item of items) {
    const row = document.createElement('li')
    row.className = 'session-file'

    const identity = document.createElement('div')
    const name = document.createElement('strong')
    name.textContent = item.original_filename
    const details = document.createElement('div')
    details.className = 'file-details'
    const size = document.createElement('span')
    size.textContent = formatBytes(item.byte_size)
    const state = document.createElement('span')
    state.className = 'file-state'
    state.textContent = item.phase ?? item.state
    details.append(size, state)
    if (item.job_id) {
      const job = document.createElement('span')
      job.textContent = `Job ${item.job_id}`
      details.append(job)
    }
    if (item.media_id) {
      const media = document.createElement('span')
      media.textContent = `Media ${item.media_id}`
      details.append(media)
    }
    identity.append(name, details)
    row.append(identity)

    if (CANCELLABLE_STATES.has(item.state)) {
      const cancel = document.createElement('button')
      cancel.type = 'button'
      cancel.className = 'secondary'
      cancel.textContent = 'Cancel file'
      cancel.setAttribute('aria-label', `Cancel ${item.original_filename}`)
      cancel.addEventListener('click', async () => {
        try {
          await cancelFile(item.intent_id, cancel)
        } catch (error) {
          setUploadMessage(safeUploadError(error), 'error')
        }
      })
      row.append(cancel)
    }

    const action = document.createElement('p')
    action.className = 'file-action'
    action.textContent = item.next_action
    row.append(action)
    elements.sessionFiles.append(row)
  }
}

function syncUppyFiles(resumeUrls = {}) {
  if (!uppy) return
  for (const file of uppy.getFiles()) {
    const key = file.meta?.client_file_key
    if (!key) continue
    const child = childByKey(key)
    if (child && file.meta.intent_id !== child.intent_id) {
      uppy.setFileMeta(file.id, { intent_id: child.intent_id })
    }
    if (isServerTransferComplete(child) && !file.progress?.uploadComplete) {
      uppy.setFileState(file.id, {
        progress: {
          ...file.progress,
          uploadComplete: true,
          percentage: 100,
          bytesUploaded: file.size,
          bytesTotal: file.size,
        },
      })
    }
    const resumeUrl = resumeUrls[key]
    if (resumeUrl && file.tus?.uploadUrl !== resumeUrl) {
      uppy.setFileState(file.id, {
        tus: { ...file.tus, uploadUrl: normalizedUrl(resumeUrl) },
      })
    }
  }
}

function applySession(payload) {
  sessionStatus = payload?.status ?? payload
  if (payload?.creation_url) creationUrl = normalizedUrl(payload.creation_url)
  setText(elements.sessionState, `${sessionStatus.session_state} · ${sessionStatus.aggregate_state}`)
  setText(
    elements.fileCount,
    `${sessionStatus.file_count} of ${sessionStatus.maximum_files}`,
  )
  setText(elements.maximumFileSize, formatBytes(sessionStatus.maximum_file_bytes))
  setText(elements.maximumSessionSize, formatBytes(sessionStatus.maximum_aggregate_bytes))
  setText(
    elements.transferBackend,
    sessionStatus.resumable
      ? 'Resumable tus transfer'
      : 'Bounded direct multipart transfer',
  )
  setText(
    elements.transferHint,
    sessionStatus.resumable
      ? `Files upload directly to the configured tus service and can resume after interruption. The effective per-file limit is ${formatBytes(sessionStatus.maximum_file_bytes)}.`
      : `Files upload directly to this VidXP API. This backend is not resumable; the effective per-file limit is ${formatBytes(sessionStatus.maximum_file_bytes)}.`,
  )
  setText(
    elements.reservedSummary,
    `${sessionStatus.reserved_file_count} files · ${formatBytes(sessionStatus.reserved_bytes)}`,
  )
  setText(
    elements.uploadedSummary,
    `${sessionStatus.uploaded_file_count} files · ${formatBytes(sessionStatus.uploaded_bytes)}`,
  )
  setText(elements.expiresAt, formatDate(sessionStatus.expires_at))
  setText(elements.summary, sessionStatus.status)
  setText(elements.nextAction, sessionStatus.next_action)
  elements.closeSession.disabled = sessionStatus.session_state !== 'open'
  renderFiles()
  syncUppyFiles(payload?.resume_urls ?? {})
  const dashboard = uppy?.getPlugin('Dashboard')
  dashboard?.setOptions({
    disabled: sessionStatus.session_state === 'expired',
    disableLocalFiles: sessionStatus.session_state !== 'open',
  })
}

async function authorizeFile(file) {
  const current = uppy.getFile(file.id)
  const key = current?.meta?.client_file_key
  if (!current || !key) throw new Error('VidXP could not identify the selected file.')
  if (!needsFileAuthorization(current, childByKey(key))) return
  const payload = await requestJson(apiUrl('./files'), {
    method: 'POST',
    body: JSON.stringify({
      client_file_key: key,
      original_filename: current.name,
      byte_size: current.size,
      declared_mime_type: current.type || null,
    }),
  })
  if (!payload.status?.intent_id) {
    throw new Error('VidXP did not bind the selected file.')
  }
  uppy.setFileMeta(current.id, {
    client_file_key: key,
    intent_id: payload.status.intent_id,
  })
  if (payload.grant) creationGrants.set(key, payload.grant)
  if (payload.resume_url) {
    uppy.setFileState(current.id, {
      tus: { ...current.tus, uploadUrl: normalizedUrl(payload.resume_url) },
    })
  }
  sessionStatus = resumePollingAfterFileAuthorization(
    sessionStatus,
    payload.status,
    POLL_INTERVAL_MS / 1000,
  )
  scheduleStatusPoll(0)
}

async function authorizeFiles(fileIDs) {
  for (const fileID of fileIDs) {
    const file = uppy.getFile(fileID)
    if (file) await authorizeFile(file)
  }
}

function recoveryLifetime() {
  const expiry = new Date(sessionStatus.expires_at).getTime()
  if (!Number.isFinite(expiry)) return 60 * 60 * 1000
  return Math.max(1000, expiry - Date.now())
}

function safeUploadError(error) {
  const code = error?.originalResponse?.getHeader?.('X-VidXP-Error')
  if (typeof code === 'string' && /^[a-z0-9_-]{1,80}$/i.test(code)) {
    return `VidXP rejected a file (${code}).`
  }
  return error instanceof Error
    ? error.message
    : 'The transfer was interrupted. Check the connection and retry.'
}

function configureUppy() {
  const scopedId = `vidxp-upload-session-${sessionStatus.session_id}`
  uppy = new Uppy({
    id: scopedId,
    autoProceed: false,
    restrictions: {
      maxFileSize: sessionStatus.maximum_file_bytes,
      maxTotalFileSize: sessionStatus.maximum_aggregate_bytes,
      maxNumberOfFiles: sessionStatus.maximum_files,
    },
    onBeforeFileAdded(file, files) {
      const recoveredGhost = Object.values(files).find(
        (candidate) =>
          candidate.isGhost &&
          candidate.name === file.name &&
          candidate.size === file.size &&
          candidate.type === file.type,
      )
      if (recoveredGhost) {
        return {
          ...recoveredGhost,
          data: file.data,
          isGhost: false,
          source: file.source,
        }
      }
      if (file.meta?.client_file_key) return file
      const key = randomClientFileKey()
      return {
        ...file,
        id: `${file.id}-${key}`,
        meta: { ...file.meta, client_file_key: key },
      }
    },
  })

  uppy.addPreProcessor(authorizeFiles)
  uppy.on('file-added', () => setUploadMessage('Files are ready to upload.'))
  uppy.on('file-removed', (file) => {
    const intentID = file.meta?.intent_id
    if (intentID) cancelFile(intentID).catch(() => {})
    setUploadMessage('File removed. You may choose another while the session is open.')
  })
  uppy.on('restriction-failed', (_file, error) => {
    setUploadMessage(error?.message ?? 'A session limit rejected the file.', 'error')
  })
  uppy.on('upload-pause', (_file, isPaused) => {
    setUploadMessage(isPaused ? 'A file upload is paused.' : 'A file upload resumed.')
  })
  uppy.on('upload-error', (file, error) => {
    creationGrants.delete(file.meta?.client_file_key)
    setUploadMessage(safeUploadError(error), 'error')
    scheduleStatusPoll(0)
  })
  uppy.on('upload-success', (file) => {
    creationGrants.delete(file.meta?.client_file_key)
    setUploadMessage('Transfer complete. VidXP is validating and importing the video.', 'success')
    scheduleStatusPoll(0)
  })
  uppy.on('restored', () => {
    setUploadMessage('The previous browser upload state was restored.')
    syncUppyFiles()
    scheduleStatusPoll(0)
  })

  uppy.use(Dashboard, {
    target: '#uppy-dashboard',
    inline: true,
    height: 420,
    theme: 'dark',
    showProgressDetails: true,
    proudlyDisplayPoweredByUppy: false,
    disableLocalFiles: sessionStatus.session_state !== 'open',
    disabled: sessionStatus.session_state === 'expired',
    note: `Up to ${sessionStatus.maximum_files} videos; ${formatBytes(sessionStatus.maximum_file_bytes)} per file.`,
  })
  if (sessionStatus.transfer_backend === 'tus') {
    uppy.use(Tus, {
      endpoint: creationUrl,
      allowedMetaFields: ['intent_id'],
      limit: 1,
      parallelUploads: 1,
      overridePatchMethod: false,
      uploadDataDuringCreation: false,
      withCredentials: false,
      removeFingerprintOnSuccess: true,
      async onBeforeRequest(request, file) {
        if (!isCreationRequest(request)) return
        const key = file.meta?.client_file_key
        const grant = creationGrants.get(key)
        if (!grant) throw new Error('VidXP did not issue a creation grant for this file.')
        request.setHeader('Authorization', `VidXP-Handoff ${grant}`)
      },
    })
  } else {
    uppy.use(XHRUpload, {
      endpoint(file) {
        const intentID = file.meta?.intent_id
        if (!intentID) throw new Error('VidXP did not bind the selected file.')
        return apiUrl(`./files/${intentID}/content`)
      },
      fieldName: 'upload',
      formData: true,
      method: 'POST',
      limit: 1,
      withCredentials: true,
      headers: { Accept: 'application/json' },
    })
  }
  uppy.use(GoldenRetriever, {
    id: `GoldenRetriever:${scopedId}`,
    expires: recoveryLifetime(),
    serviceWorker: false,
    // Persist recovery metadata and tus URLs, never local video blobs. A user
    // reselects the source file after reload so multi-GiB media is not copied
    // into browser storage.
    indexedDB: { maxFileSize: 0, maxTotalSize: 0 },
  })
}

async function cancelFile(intentID, button = null) {
  if (button) button.disabled = true
  try {
    const payload = await requestJson(apiUrl(`./files/${intentID}/cancel`), {
      method: 'POST',
      body: '{}',
    })
    applySession(payload)
    setUploadMessage('The selected file was cancelled without affecting its siblings.')
  } catch (error) {
    if (button) button.disabled = false
    throw error
  }
}

async function closeSession() {
  elements.closeSession.disabled = true
  try {
    const payload = await requestJson(apiUrl('./close'), {
      method: 'POST',
      body: '{}',
    })
    applySession(payload)
    setUploadMessage('The session is closed to new files. Active files may still finish.')
  } catch (error) {
    elements.closeSession.disabled = false
    setUploadMessage(safeUploadError(error), 'error')
  }
}

async function pollStatus() {
  if (pollInFlight || !shouldPollSession(sessionStatus)) return
  pollInFlight = true
  try {
    const payload = await requestJson(apiUrl('./status'))
    applySession(payload)
    scheduleStatusPoll()
  } catch {
    setText(elements.nextAction, 'The status check failed. This page will retry automatically.')
    scheduleStatusPoll()
  } finally {
    pollInFlight = false
  }
}

function scheduleStatusPoll(delay = POLL_INTERVAL_MS) {
  window.clearTimeout(pollTimer)
  if (!shouldPollSession(sessionStatus)) return
  const serverDelay = Number(sessionStatus?.poll_after_seconds) * 1000
  const requestedDelay = Number.isFinite(serverDelay) && serverDelay > 0
    ? serverDelay
    : delay
  const effectiveDelay = document.hidden
    ? Math.max(requestedDelay, 15_000)
    : requestedDelay
  pollTimer = window.setTimeout(pollStatus, effectiveDelay)
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
  if (!payload.status?.session_id || !payload.creation_url) {
    throw new Error('VidXP returned an incomplete upload session.')
  }
  creationUrl = normalizedUrl(payload.creation_url)
  applySession(payload)
  configureUppy()
  syncUppyFiles(payload.resume_urls ?? {})
  elements.closeSession.addEventListener('click', closeSession)
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden && shouldPollSession(sessionStatus)) scheduleStatusPoll(0)
  })
  setUploadMessage(
    sessionStatus.resumable
      ? 'Choose one or more videos. Transfers use resumable tus.'
      : 'Choose one or more videos. Transfers use bounded direct upload to VidXP and are not resumable.',
  )
  scheduleStatusPoll(0)
}

bootstrap().catch((error) => {
  const message = error instanceof Error ? error.message : 'The upload session could not start.'
  setText(elements.summary, message)
  setUploadMessage(message, 'error')
  setText(elements.nextAction, 'Reopen the complete capability link or request a new upload session.')
})
