import {createRoot} from 'react-dom/client'
import '../src/index.css'
import {Toaster} from '../src/components/toast'
import {TemplateLibraryPage} from '../src/audit/template-library'
import {fixtureTemplates} from './audit-templates'

// Explicit UI fixture; `?scene=templates-error` or `?scene=templates-no-feishu`.
const adapter = fixtureTemplates(new URLSearchParams(location.search).get('scene') || '')
createRoot(document.getElementById('root')!).render(<main id="audit-main" tabIndex={-1} className="mx-auto max-w-6xl p-4 md:p-8"><TemplateLibraryPage adapter={adapter}/><Toaster/></main>)
