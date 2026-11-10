import './index.css'
import {registerServiceWorker} from './pwa'
import {canonicalAddress} from './audit/routes'

const address = new URL(location.href)
const target = canonicalAddress(address)
if (target === address.pathname + address.search + address.hash) void import('./audit/native-main')
else location.replace(target)
registerServiceWorker()
