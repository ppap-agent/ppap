export type DraftPhase = 'idle' | 'saving' | 'accepting'
export type ReviewPhase = 'clean' | 'dirty' | 'saving'

export function beginDraftSave(phase: DraftPhase): DraftPhase | null {
  return phase === 'idle' ? 'saving' : null
}

export function beginDraftAccept(phase: DraftPhase): DraftPhase | null {
  return phase === 'idle' ? 'accepting' : null
}

export function finishDraft(phase: DraftPhase): DraftPhase {
  return phase === 'idle' ? phase : 'idle'
}

export function markReviewDirty(phase: ReviewPhase): ReviewPhase {
  return phase === 'saving' ? phase : 'dirty'
}

export function markReviewClean(): ReviewPhase {
  return 'clean'
}

export function beginReviewSave(phase: ReviewPhase): ReviewPhase | null {
  return phase === 'dirty' ? 'saving' : null
}
