import './index.css'
import {canonicalAddress} from './audit/routes'

// The app was once installable; drop a service worker left in browsers from then.
void navigator.serviceWorker?.getRegistrations().then(registrations => registrations.forEach(registration => void registration.unregister())).catch(() => {})
const address = new URL(location.href)
const target = canonicalAddress(address)
if (target === address.pathname + address.search + address.hash) void import('./audit/native-main')
else location.replace(target)
