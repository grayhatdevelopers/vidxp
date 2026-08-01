const card = document.querySelector('#download-card')
const title = document.querySelector('#download-title')
const status = document.querySelector('#download-status')
const details = document.querySelector('#artifact-details')
const filename = document.querySelector('#artifact-filename')
const mediaType = document.querySelector('#artifact-media-type')
const size = document.querySelector('#artifact-size')
const expiry = document.querySelector('#artifact-expiry')
const expiryTime = document.querySelector('#artifact-expiry-time')
const actions = document.querySelector('#download-actions')
const downloadAgain = document.querySelector('#download-again')

function setState(state, heading, message) {
  card.dataset.state = state
  title.textContent = heading
  status.textContent = message
}

function humanSize(bytes) {
  if (bytes < 1024) return `${bytes} B`
  const units = ['KB', 'MB', 'GB', 'TB']
  let value = bytes
  let unit = -1
  do {
    value /= 1024
    unit += 1
  } while (value >= 1024 && unit < units.length - 1)
  const precision = value >= 10 ? 1 : 2
  return `${Number(value.toFixed(precision))} ${units[unit]}`
}

function showFailure(heading, message) {
  details.hidden = true
  expiry.hidden = true
  actions.hidden = true
  setState('error', heading, message)
}

async function responseError(response) {
  try {
    const payload = await response.json()
    return payload?.error?.code ?? ''
  } catch {
    return ''
  }
}

function readCapability() {
  const parameters = new URLSearchParams(window.location.hash.slice(1))
  return parameters.get('capability')
}

async function startDownload() {
  let capability = readCapability()
  if (!capability) {
    showFailure(
      'Download link incomplete',
      'Open the complete VidXP link you received. Its private fragment is required to start the download.',
    )
    return
  }

  const base = window.location.pathname.replace(/\/$/, '')
  let response
  try {
    response = await fetch(`${base}/bootstrap`, {
      method: 'POST',
      credentials: 'same-origin',
      cache: 'no-store',
      headers: {
        Accept: 'application/json',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ capability }),
    })
  } catch {
    showFailure(
      'Download unavailable',
      'VidXP could not verify the link. Check your connection and try opening the link again.',
    )
    return
  }

  if (!response.ok) {
    const code = await responseError(response)
    const expired = code === 'artifact_download_capability_expired'
    showFailure(
      expired ? 'Download link expired' : 'Download link invalid',
      expired
        ? 'This private link has expired. Request a new artifact download link from VidXP.'
        : 'This private link is invalid or does not match the requested artifact.',
    )
    return
  }

  const payload = await response.json()
  window.history.replaceState(null, document.title, base)
  capability = null

  filename.textContent = payload.filename
  mediaType.textContent = payload.mime_type
  size.textContent = humanSize(payload.byte_size)
  downloadAgain.href = payload.content_url
  downloadAgain.download = payload.filename
  details.hidden = false
  actions.hidden = false

  const expiresAt = new Date(payload.expires_at)
  if (!Number.isNaN(expiresAt.valueOf())) {
    expiryTime.dateTime = expiresAt.toISOString()
    expiryTime.textContent = new Intl.DateTimeFormat(undefined, {
      dateStyle: 'medium',
      timeStyle: 'short',
    }).format(expiresAt)
    expiry.hidden = false
  }

  setState(
    'starting',
    'Starting your download',
    'The artifact is ready. VidXP is asking your browser to save it now…',
  )

  let contentReady = false
  try {
    const content = await fetch(payload.content_url, {
      method: 'HEAD',
      credentials: 'same-origin',
      cache: 'no-store',
    })
    contentReady = content.ok
  } catch {
    contentReady = false
  }

  if (!contentReady) {
    actions.hidden = true
    setState(
      'error',
      'Download failed',
      'The artifact was verified, but the file could not be opened. Request a fresh link and try again.',
    )
    return
  }

  downloadAgain.click()
  setState(
    'ready',
    'Download started',
    'Your browser should be saving the file. If it was blocked or you need another copy, use Download again.',
  )
}

startDownload().catch(() => {
  showFailure(
    'Download failed',
    'VidXP could not start this download. Request a fresh link and try again.',
  )
})
