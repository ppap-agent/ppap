import {useEffect, useState} from 'react'
import {Check} from 'lucide-react'

// A brief confirmation for actions whose own surface closes on success; failures stay inline.
type Toast = {id: number; text: string}
const listeners = new Set<(toast: Toast) => void>()
let sequence = 0

export function showToast(text: string) {
  const toast = {id: ++sequence, text}
  listeners.forEach(listener => listener(toast))
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
  return <div aria-live="polite" className="pointer-events-none fixed inset-x-0 bottom-6 z-[60] flex flex-col items-center gap-2 px-4">
    {toasts.map(toast => <div key={toast.id} role="status" className="flex items-center gap-2 rounded-lg bg-foreground px-4 py-2.5 text-sm text-background shadow-lg animate-in fade-in-0 slide-in-from-bottom-2 duration-200 motion-reduce:animate-none"><Check aria-hidden="true" className="size-4"/>{toast.text}</div>)}
  </div>
}
