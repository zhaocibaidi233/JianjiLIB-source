import { reactive } from 'vue'

export type ThemeMode = 'light' | 'dark'

const KEY = 'jianji.theme'

function initial(): ThemeMode {
  const saved = localStorage.getItem(KEY)
  if (saved === 'dark' || saved === 'light') return saved
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

export const themeState = reactive<{ mode: ThemeMode }>({ mode: 'light' })

export function applyTheme(mode: ThemeMode) {
  themeState.mode = mode
  document.documentElement.dataset.theme = mode
  // 旧版 Element Plus 页面按 html.dark 切换暗色，与新主题保持同步
  document.documentElement.classList.toggle('dark', mode === 'dark')
  document.documentElement.style.colorScheme = mode
  localStorage.setItem(KEY, mode)
}

export function toggleTheme() {
  applyTheme(themeState.mode === 'dark' ? 'light' : 'dark')
}

export function initTheme() {
  applyTheme(initial())
}
