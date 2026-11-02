import { reactive } from 'vue'
import { get, post, setToken, getToken, ApiError } from './api'

export interface AuthUser {
  id: number
  username: string
  display_name: string
  enabled: boolean
  role_id: number | null
  role_name: string | null
  role_title: string | null
  permissions: Record<string, string>
  levels: Record<string, string>
  is_admin: boolean
  last_login_at: string | null
  created_at: string | null
}

interface AuthState {
  ready: boolean
  enabled: boolean
  needsSetup: boolean
  authenticated: boolean
  /** 管理员是否打开了「允许自助注册」。开着时登录页才显示「申请开通」入口。 */
  registrationOpen: boolean
  user: AuthUser | null
  error: string
}

export const auth = reactive<AuthState>({
  ready: false,
  enabled: false,
  needsSetup: false,
  authenticated: false,
  registrationOpen: false,
  user: null,
  error: '',
})

let readyPromise: Promise<void> | null = null

interface StatusResponse {
  enabled: boolean
  needs_setup: boolean
  authenticated: boolean
  user: AuthUser | null
  registration_open?: boolean
}

export function ensureAuthReady(force = false): Promise<void> {
  if (auth.ready && !force) return Promise.resolve()
  if (readyPromise && !force) return readyPromise
  if (force) readyPromise = null
  readyPromise = (async () => {
    try {
      const data = await get<StatusResponse>('/auth/status')
      auth.enabled = !!data.enabled
      auth.needsSetup = !!data.needs_setup
      auth.authenticated = !!data.authenticated
      // 后端已经算好「登录已启用 **且** 管理员开了开关」；前端不要再自己拼条件，
      // 否则两边口径迟早分叉（漏一个就变成"开关开了但入口不出现"）。
      auth.registrationOpen = !!data.registration_open
      auth.user = data.user
      auth.error = ''
      if (!data.authenticated) setToken(null)
    } catch (error) {
      // 后端不可达时不把用户挡在登录页外面：页面自己会显示连接失败。
      auth.enabled = false
      auth.authenticated = false
      auth.user = null
      auth.registrationOpen = false
      auth.error = error instanceof ApiError ? error.detail : '无法连接后端服务'
    } finally {
      auth.ready = true
    }
  })()
  return readyPromise
}

export async function login(username: string, password: string): Promise<void> {
  const data = await post<{ token: string; user: AuthUser; message?: string }>('/auth/login', {
    username,
    password,
  })
  setToken(data.token)
  auth.enabled = true
  auth.needsSetup = false
  auth.authenticated = true
  auth.user = data.user
  auth.error = ''
}

export async function setupAdmin(
  username: string,
  password: string,
  displayName: string,
): Promise<void> {
  const data = await post<{ token: string; user: AuthUser; message?: string }>('/auth/setup', {
    username,
    password,
    display_name: displayName || null,
  })
  setToken(data.token)
  auth.enabled = true
  auth.needsSetup = false
  auth.authenticated = true
  auth.user = data.user
  auth.error = ''
}

/**
 * 提交自助注册**申请**（不产生账号、不会登录）。
 *
 * 只在 `auth.registrationOpen` 为真时才有入口，但**不能只靠前端判断**：
 * 后端 `/api/auth/register` 自己会再校验一次开关（前端判断只是不给死按钮）。
 * 管理员可能在页面打开后才把开关关掉，这时后端返 403，把 detail 原样带给用户即可。
 */
export async function register(
  username: string,
  password: string,
  note = '',
): Promise<string> {
  const data = await post<{ ok: boolean; id: number; message: string }>('/auth/register', {
    username,
    password,
    note: note.trim() || null,
  })
  return data.message || '申请已提交，管理员批准后即可用这个账号和密码登录'
}

export async function logout(): Promise<void> {
  try {
    await post('/auth/logout')
  } catch {
    /* 令牌已失效也应清掉本地状态 */
  }
  setToken(null)
  auth.authenticated = false
  auth.user = null
  // 丢掉缓存：登录页会重新取一次 /auth/status。管理员是在**别的**会话里
  // 改「允许自助注册」开关的，不重新取的话这里永远显示的是登录前的旧值
  // ——「开关明明开了、登录页却没有申请入口」有一半是这么来的。
  auth.ready = false
  readyPromise = null
}

export async function refreshMe(): Promise<void> {
  if (!getToken()) return
  try {
    auth.user = await get<AuthUser>('/auth/me')
    auth.authenticated = true
  } catch {
    auth.authenticated = false
    auth.user = null
  }
}

export function isLoggedIn(): boolean {
  return !auth.enabled || auth.authenticated
}

export function can(module: string | undefined, level: 'read' | 'write' = 'read'): boolean {
  if (!module) return true
  if (!auth.enabled) return true
  if (!auth.user) return false
  if (auth.user.is_admin) return true
  const granted = auth.user.levels?.[module] || 'none'
  return level === 'read' ? granted === 'read' || granted === 'write' : granted === 'write'
}

export function defaultRoute(): string {
  return '/admin'
}

if (typeof window !== 'undefined') {
  // 这里只清状态；「跳回登录页」由 router.ts 监听同一事件完成（路由才认识页面）。
  window.addEventListener('jianji:unauthorized', () => {
    auth.authenticated = false
    auth.user = null
  })
}
