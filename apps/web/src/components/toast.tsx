import {useEffect, useState} from 'react'
import {iconCheck} from './icons'
import {Icon} from './ui/icon'
import {feishuToast} from '../feishu-native'

// A brief confirmation (Feishu message: white, top centre, a green check) for actions that succeeded; failures stay inline.
type Toast = {id: number; text: string}
const listeners = new Set<(toast: Toast) => void>()
let sequence = 0

export function showToast(text: string) {
  void feishuToast(text).then(shown => {
    if (shown) return
    const toast = {id: ++sequence, text}
    listeners.forEach(listener => listener(toast))
  })
}

export function Toaster() {
  const [toasts, setToasts] = useState<Toast[]>([])
  useEffect(() => {
    const add = (toast: Toast) => {
      setToasts(rows => [...rows.slice(-2), toast])
      window.setTimeout(() => setToasts(rows => rows.filter(row => row.id !== toast.id)), 3200)
    }
    listeners.add(add)
    return () => {listeners.delete(add)}
  }, [])
  return <div aria-live="polite" className="pointer-events-none fixed inset-x-0 top-16 z-[60] flex flex-col items-center gap-2 px-4">
    {toasts.map(toast => <div key={toast.id} role="status" className="flex items-center gap-2 rounded-md bg-popover px-4 py-2.5 text-sm text-popover-foreground shadow-lg animate-in fade-in-0 slide-in-from-top-2 duration-200 motion-reduce:animate-none"><Icon icon={iconCheck} aria-hidden="true" className="size-4 text-success"/>{toast.text}</div>)}
  </div>
}
