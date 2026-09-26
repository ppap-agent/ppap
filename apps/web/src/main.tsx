import {createRoot} from 'react-dom/client'
import './index.css'
import App from './App'
import {registerServiceWorker} from './pwa'

createRoot(document.getElementById('app')!).render(<App />)
registerServiceWorker()
