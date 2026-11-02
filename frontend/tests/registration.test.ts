import { beforeEach, describe, expect, it, vi } from 'vitest'

// 只替换网络层：auth.ts 的其余部分（响应式状态、Ready 缓存）都跑真代码。
// 这条链路的坑正是"后端字段有、前端没读"，所以必须从 /auth/status 的响应开始测。
const get = vi.fn()
const post = vi.fn()
const setToken = vi.fn()
const getToken = vi.fn(() => null)

class ApiError extends Error {
  status: number
  detail: string
  constructor(status: number, detail: string) {
    super(detail)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
  }
}

vi.mock('../src/lib/api', () => ({
  get: (...args: unknown[]) => get(...args),
  post: (...args: unknown[]) => post(...args),
  setToken: () => setToken(),
  getToken: () => getToken(),
  ApiError,
}))

const { auth, ensureAuthReady, register, logout } = await import('../src/lib/auth')

beforeEach(() => {
  get.mockReset()
  post.mockReset()
  setToken.mockReset()
})

describe('registration_open 的传递', () => {
  it('reads registration_open from /auth/status', async () => {
    get.mockResolvedValue({
      enabled: true,
      needs_setup: false,
      authenticated: false,
      user: null,
      registration_open: true,
    })
    await ensureAuthReady(true)
    expect(auth.registrationOpen).toBe(true)
  })

  it('treats a missing field as closed (old backend must not light up the entry)', async () => {
    get.mockResolvedValue({
      enabled: true, needs_setup: false, authenticated: false, user: null,
    })
    await ensureAuthReady(true)
    expect(auth.registrationOpen).toBe(false)
  })

  it('closes the entry when the backend is unreachable', async () => {
    auth.registrationOpen = true
    get.mockRejectedValue(new ApiError(0, '连不上'))
    await ensureAuthReady(true)
    expect(auth.registrationOpen).toBe(false)
  })
})

describe('register()', () => {
  it('posts to /auth/register and returns the server message', async () => {
    post.mockResolvedValue({ ok: true, id: 7, message: '申请已提交，等管理员批准' })
    const message = await register('zhang3', 'hunter22', '初一(3)班 语文')
    expect(post).toHaveBeenCalledWith('/auth/register', {
      username: 'zhang3',
      password: 'hunter22',
      note: '初一(3)班 语文',
    })
    expect(message).toBe('申请已提交，等管理员批准')
  })

  it('sends null (not an empty string) for a blank note', async () => {
    post.mockResolvedValue({ ok: true, id: 8, message: 'ok' })
    await register('li4', 'hunter22', '   ')
    expect(post.mock.calls[0][1]).toMatchObject({ note: null })
  })

  it('does not touch the token: an application is not a login', async () => {
    post.mockResolvedValue({ ok: true, id: 9, message: 'ok' })
    await register('wang5', 'hunter22')
    expect(setToken).not.toHaveBeenCalled()
    expect(auth.authenticated).toBe(false)
  })
})

describe('logout() 会丢弃登录态缓存', () => {
  it('forces the next ensureAuthReady to refetch, so the entry reflects the current switch', async () => {
    get.mockResolvedValue({
      enabled: true, needs_setup: false, authenticated: true,
      user: { id: 1, username: 'admin' }, registration_open: false,
    })
    await ensureAuthReady(true)
    expect(get).toHaveBeenCalledTimes(1)

    post.mockResolvedValue({})
    await logout()
    expect(auth.ready).toBe(false)

    get.mockResolvedValue({
      enabled: true, needs_setup: false, authenticated: false,
      user: null, registration_open: true,
    })
    await ensureAuthReady()
    expect(get).toHaveBeenCalledTimes(2)
    expect(auth.registrationOpen).toBe(true)
  })
})
