import { reactive } from 'vue'
import {
  readerGet,
  readerPost,
  setReaderToken,
  getReaderToken,
  ApiError,
} from './api'

export interface ReaderProfile {
  id: number
  library_id: number
  card_no: string
  name: string
  reader_type: string
  grade: string | null
  class_name: string | null
  phone: string | null
  status: string
  status_label: string
  rules: { loan_days: number; max_items: number; max_renew: number; renew_days: number }
  stats: { active: number; overdue: number; total: number }
  fines: { enabled: boolean; unpaid: number; unpaid_count: number; overdue_estimate: number }
  compensations: { lost_count: number; unpaid: number; unpaid_count: number }
  account: { username: string; enabled: boolean; last_login_at: string | null } | null
}

interface ReaderAuthState {
  ready: boolean
  authenticated: boolean
  reader: ReaderProfile | null
  error: string
}

export const readerAuth = reactive<ReaderAuthState>({
  ready: false,
  authenticated: false,
  reader: null,
  error: '',
})

let readyPromise: Promise<void> | null = null

export function ensureReaderReady(force = false): Promise<void> {
  if (readerAuth.ready && !force) return Promise.resolve()
  if (readyPromise && !force) return readyPromise
  if (force) readyPromise = null
  readyPromise = (async () => {
    const token = getReaderToken()
    if (!token) {
      readerAuth.ready = true
      readerAuth.authenticated = false
      readerAuth.reader = null
      return
    }
    try {
      const data = await readerGet<{ authenticated: boolean; reader: ReaderProfile | null }>(
        '/portal/auth/status',
      )
      readerAuth.authenticated = !!data.authenticated
      readerAuth.reader = data.reader
      readerAuth.error = ''
      if (!data.authenticated) setReaderToken(null)
    } catch (error) {
      readerAuth.authenticated = false
      readerAuth.reader = null
      readerAuth.error = error instanceof ApiError ? error.detail : '无法连接读者服务'
    } finally {
      readerAuth.ready = true
    }
  })()
  return readyPromise
}

/**
 * 读者登录。
 *
 * `libraryId` 只在「同一借书证号在多个图书库中都存在」时才需要：证号只在库内
 * 唯一，两个库各自从 0001 编起时同一个证号对应两个读者。登录页拿到后端的
 * 「请指定图书库」之后会让读者选一次，再把 id 带上来。
 */
export async function readerLogin(
  cardNo: string,
  password: string,
  libraryId?: number | null,
): Promise<void> {
  const body: { card_no: string; password: string; library_id?: number } = {
    card_no: cardNo, password,
  }
  if (libraryId) body.library_id = libraryId
  const data = await readerPost<{ token: string; reader: ReaderProfile }>(
    '/portal/auth/login',
    body,
  )
  setReaderToken(data.token)
  readerAuth.ready = true
  readerAuth.authenticated = true
  readerAuth.reader = data.reader
  readerAuth.error = ''
}

export type ReaderLibrary = { id: number; name: string }

/** 登录页消歧用：本系统有哪些图书库（只有 id 与名称）。 */
export async function readerLibraries(): Promise<ReaderLibrary[]> {
  const data = await readerGet<{ libraries: ReaderLibrary[] }>('/portal/auth/libraries')
  return data.libraries || []
}


/**
 * 读者自助改密码（`POST /portal/auth/password`）。
 *
 * ⚠️ 后端在改密时会 `purge_sessions()` **清掉该读者的全部会话**，然后签发一个新令牌
 * —— 所以必须把返回的 token 写回本地，否则改完密码下一个请求就是 401（表现为
 * "刚改完密码就被踢出去"，且看起来像改失败）。
 */
export async function readerChangePassword(
  oldPassword: string,
  newPassword: string,
): Promise<string> {
  const data = await readerPost<{ ok: boolean; token: string; message: string }>(
    '/portal/auth/password',
    { old_password: oldPassword, new_password: newPassword },
  )
  if (data.token) setReaderToken(data.token)
  readerAuth.authenticated = true
  readerAuth.ready = true
  return data.message || '密码已修改'
}

export async function refreshReader(): Promise<void> {  if (!getReaderToken()) return
  try {
    readerAuth.reader = await readerGet<ReaderProfile>('/portal/me')
    readerAuth.authenticated = true
  } catch {
    readerAuth.reader = null
    readerAuth.authenticated = false
  }
}

export async function readerLogout(): Promise<void> {
  try {
    await readerPost('/portal/auth/logout')
  } catch {
    /* 令牌已失效也要清本地 */
  }
  setReaderToken(null)
  readerAuth.authenticated = false
  readerAuth.reader = null
}

if (typeof window !== 'undefined') {
  window.addEventListener('jianji:reader-unauthorized', () => {
    readerAuth.authenticated = false
    readerAuth.reader = null
  })
}
