type ReadState = {readScope: string | null; errorScope: string | null; busy: boolean; error: string}

/** A cache miss is not an empty server result, including before debounce fires. */
export function taskListStatus(state: ReadState, scope: string) {
  const error = state.errorScope === scope ? state.error : ''
  const ready = state.readScope === scope && !error
  return {ready, error, busy: !error && (state.busy || !ready)}
}
