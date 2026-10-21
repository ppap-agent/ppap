import {createRoot} from 'react-dom/client'
import './index.css'
import App from './journey/intake'
import {registerServiceWorker} from './pwa'
import {canonicalAddress} from './audit/routes'

const address = new URL(location.href)
const legacyContext = ['case', 'draft', 'round'].some(key => address.searchParams.has(key))
if (legacyContext || ['/dev', '/rules'].includes(address.pathname)) createRoot(document.getElementById('app')!).render(<App />)
else {
  const target = canonicalAddress(address)
  if (target === address.pathname + address.search + address.hash) void import('./audit/native-main')
  else location.replace(target)
}
registerServiceWorker()
