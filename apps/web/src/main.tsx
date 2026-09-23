import {createRoot} from 'react-dom/client'
import App from './App'
import './ui/foundation.css'
import './style.css'
import './ui/shell.css'
import './audit/audit.css'

createRoot(document.getElementById('app')!).render(<App />)
