import type {SemanticSource} from '../api'

/** Where the original reader opens: the structured location it jumps to and the server's wording of it. */
export type SourceFocus = {locator: SemanticSource['locator']; label: string}
