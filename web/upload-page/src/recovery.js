const TRANSFER_COMPLETE_STATES = new Set([
  'processing',
  'ready',
  'indexed',
  'failed',
  'expired',
])

export function isServerTransferComplete(child) {
  return Boolean(child && TRANSFER_COMPLETE_STATES.has(child.state))
}

export function needsFileAuthorization(file, child) {
  if (isServerTransferComplete(child)) return false
  return !file?.tus?.uploadUrl
}
