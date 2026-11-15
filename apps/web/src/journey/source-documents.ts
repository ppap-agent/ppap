import type {LocalFileRef} from './local-files'
export type SourceFile = {name: string; revision: string; sourceVersion: number; pending?: boolean; localFile?: LocalFileRef; material?: import('../api').Material; roundId?: string}
export type SourceFocus = {location: string; requestId: number}
