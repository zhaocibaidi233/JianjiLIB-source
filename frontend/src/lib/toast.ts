import { reactive } from 'vue'

export type ToastKind = 'success' | 'error' | 'info'

export interface ToastItem {
  id: number
  message: string
  kind: ToastKind
}

export const toasts = reactive<ToastItem[]>([])

let seq = 0

export function dismissToast(id: number) {
  const index = toasts.findIndex((item) => item.id === id)
  if (index >= 0) toasts.splice(index, 1)
}

export function toast(message: string, kind: ToastKind = 'success', timeout = 3200) {
  const id = ++seq
  toasts.push({ id, message, kind })
  window.setTimeout(() => dismissToast(id), timeout)
  return id
}

export function toastError(message: string) {
  return toast(message, 'error', 4600)
}
