import {createRoot} from 'react-dom/client'
import '../src/index.css'
import {FactQuestions} from '../src/audit/fact-questions'

// Explicit UI fixture. The browser test supplies the HTTP boundary separately.
createRoot(document.getElementById('root')!).render(<main className="mx-auto max-w-5xl"><FactQuestions roundId="facts-preview" files={[{id: 'file-1', file_id: 'document-1', original_filename: '零件提交保证书.pdf', content_type: 'application/pdf', byte_size: 100, status: 'ready', sha256: null}]} findings={['R1', 'R2', 'R3', 'R4'].map(id => ({item_key: id, item: `核对条款 ${id}`, status: 'UNABLE' as const}))}/></main>)
