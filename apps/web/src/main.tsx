import {createRoot} from 'react-dom/client'
import './index.css'
import App from './journey/intake'
import {registerServiceWorker} from './pwa'

createRoot(document.getElementById('app')!).render(<App />)
registerServiceWorker()
