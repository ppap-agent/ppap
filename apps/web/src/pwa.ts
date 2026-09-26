import {useEffect, useState} from 'react'

type BeforeInstallPromptEvent = Event & {
  prompt: () => Promise<void>
  userChoice: Promise<{outcome: 'accepted' | 'dismissed'; platform: string}>
}

export function registerServiceWorker() {
  if (!import.meta.env.PROD || !('serviceWorker' in navigator)) return
  void navigator.serviceWorker.register('/sw.js', {scope: '/', updateViaCache: 'none'})
}

export function useInstallPrompt() {
  const [deferredPrompt, setDeferredPrompt] = useState<BeforeInstallPromptEvent | null>(null)

  useEffect(() => {
    const handleBeforeInstallPrompt = (event: Event) => {
      event.preventDefault()
      setDeferredPrompt(event as BeforeInstallPromptEvent)
    }
    const handleInstalled = () => setDeferredPrompt(null)
    window.addEventListener('beforeinstallprompt', handleBeforeInstallPrompt)
    window.addEventListener('appinstalled', handleInstalled)
    return () => {
      window.removeEventListener('beforeinstallprompt', handleBeforeInstallPrompt)
      window.removeEventListener('appinstalled', handleInstalled)
    }
  }, [])

  async function install() {
    if (!deferredPrompt) return false
    const prompt = deferredPrompt
    try {
      await prompt.prompt()
      const result = await prompt.userChoice
      return result.outcome === 'accepted'
    } finally {
      setDeferredPrompt(null)
    }
  }

  return {canInstall: deferredPrompt !== null, install}
}

export function useOnlineStatus() {
  const [online, setOnline] = useState(() => typeof navigator === 'undefined' || navigator.onLine)
  useEffect(() => {
    const setOnlineState = () => setOnline(true)
    const setOfflineState = () => setOnline(false)
    window.addEventListener('online', setOnlineState)
    window.addEventListener('offline', setOfflineState)
    return () => {
      window.removeEventListener('online', setOnlineState)
      window.removeEventListener('offline', setOfflineState)
    }
  }, [])
  return online
}
