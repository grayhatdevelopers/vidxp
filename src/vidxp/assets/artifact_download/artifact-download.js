const status = document.querySelector('#download-status')

function readCapability() {
  const parameters = new URLSearchParams(window.location.hash.slice(1))
  return parameters.get('capability')
}

async function startDownload() {
  const capability = readCapability()
  if (!capability) throw new Error('The complete VidXP download link is required.')
  const base = window.location.pathname.replace(/\/$/, '')
  const response = await fetch(`${base}/bootstrap`, {
    method: 'POST',
    credentials: 'same-origin',
    cache: 'no-store',
    headers: {
      Accept: 'application/json',
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ capability }),
  })
  if (!response.ok) throw new Error('The VidXP download link is invalid or expired.')
  const payload = await response.json()
  window.history.replaceState(null, document.title, base)
  status.textContent = 'Your download is starting…'
  window.location.replace(payload.content_url)
}

startDownload().catch((error) => {
  status.textContent = error instanceof Error ? error.message : 'The download failed.'
})
