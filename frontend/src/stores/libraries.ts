import { reactive } from 'vue'
import { get } from '../lib/api'

export interface LibraryItem {
  id: number
  name: string
  note: string | null
  record_count?: number
}

const LAST_LIBRARY_KEY = 'jianji.library'

export const libraryState = reactive({
  items: [] as LibraryItem[],
  loaded: false,
  loading: false,
  currentId: Number(localStorage.getItem(LAST_LIBRARY_KEY)) || null,
  error: '',
})

export function currentLibrary(): LibraryItem | null {
  return libraryState.items.find((item) => item.id === libraryState.currentId) || null
}

export function setCurrentLibrary(id: number | null) {
  libraryState.currentId = id
  if (id) localStorage.setItem(LAST_LIBRARY_KEY, String(id))
  else localStorage.removeItem(LAST_LIBRARY_KEY)
}

export async function loadLibraries(force = false): Promise<void> {
  if (libraryState.loaded && !force) return
  if (libraryState.loading) return
  libraryState.loading = true
  try {
    const items = await get<LibraryItem[]>('/book-libraries')
    libraryState.items = items
    libraryState.loaded = true
    libraryState.error = ''
    if (!libraryState.currentId && items.length) {
      setCurrentLibrary(items[0].id)
    } else if (libraryState.currentId
      && !items.some((item) => item.id === libraryState.currentId)) {
      // 当前库被删除后不能继续指向它，否则后续请求会 404
      setCurrentLibrary(items[0]?.id ?? null)
    }
  } catch {
    libraryState.error = '无法读取图书库'
  } finally {
    libraryState.loading = false
  }
}
