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

export function resumePollingAfterFileAuthorization(
  sessionStatus,
  fileStatus,
  pollAfterSeconds = 2,
) {
  if (!fileStatus?.intent_id || !sessionStatus?.terminal) return sessionStatus
  return {
    ...sessionStatus,
    terminal: false,
    poll_after_seconds: pollAfterSeconds,
  }
}

export function shouldPollSession(sessionStatus) {
  return !sessionStatus?.terminal
}
