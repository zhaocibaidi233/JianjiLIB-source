import { watch } from 'vue'
import type { Ref } from 'vue'
import { http } from '../api'
import { bookNoFromCallNo } from '../volume'

/** 书次号预览：去抖只压请求，输入发生时立即使在途请求失效。 */
export function useBookNoSync(opts: {
  classNo: () => string
  setBookNo: (v: string) => void
  bookNoTouched: Ref<boolean>
  loadedClassNo: () => string
  currentLibId: Ref<number | undefined>
  recordIdentity: () => string
  /** 包括手工书次号变化，不能仅依赖 touched 首次从 false 变为 true。 */
  bookNo: () => string
}) {
  const { classNo, setBookNo, bookNoTouched, loadedClassNo, currentLibId, recordIdentity, bookNo } = opts
  let timer: ReturnType<typeof setTimeout> | undefined
  let seq = 0
  let disposed = false
  let writing = false

  function invalidate() {
    seq += 1
    clearTimeout(timer)
    timer = undefined
  }

  function write(value: string) {
    writing = true
    try { setBookNo(value) } finally { writing = false }
  }

  async function syncBookNo(opts: { force?: boolean } = {}): Promise<string | null> {
    invalidate()
    const force = opts.force === true
    if (disposed) return null
    // force 是用户显式在分类号按回车：忽略“手敲过”和“载入分类号相同”两道抑制，
    // 但“请求期间又有新输入”的守卫仍保留（见下方 response 检查）。
    if (!force && bookNoTouched.value) return null
    const cls = (classNo() || '').trim()
    if (!cls || !/^[A-Za-z]/.test(cls)) { write(''); return null }
    if (!force && cls === loadedClassNo()) return null // 保留已有记录现有号
    const requestSeq = seq
    const library = currentLibId.value
    const identity = recordIdentity()
    const previousBookNo = bookNo()
    try {
      const d = (await http.get('/cataloging/callno-preview',
        { params: { class_no: cls, library_id: library } })).data
      if (disposed || requestSeq !== seq || (!force && bookNoTouched.value)
        || cls !== (classNo() || '').trim() || library !== currentLibId.value
        || identity !== recordIdentity() || previousBookNo !== bookNo()) return null
      const value = bookNoFromCallNo(d.call_no)
      write(value)
      return value
    } catch { /* 预览失败不阻断编目 */ }
    return null
  }

  watch([classNo, currentLibId, recordIdentity], () => {
    invalidate() // 包括接下来 300ms 的去抖窗口
    if (!disposed) timer = setTimeout(syncBookNo, 300)
  }, { flush: 'sync' })
  watch([bookNoTouched, bookNo], () => {
    if (!writing) invalidate()
  }, { flush: 'sync' })

  function dispose() {
    disposed = true
    invalidate()
  }

  return { syncBookNo, invalidate, dispose }
}
