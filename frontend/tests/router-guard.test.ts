import { beforeEach, describe, expect, it } from 'vitest'
import { auth, can, type AuthUser } from '../src/lib/auth'
import { readerAuth } from '../src/lib/readerAuth'
import { router } from '../src/router'

// 路由守卫与被动 401 跳转（2026-10-25 审查 P1-5 / P2-1）。
//
// 守卫是权限矩阵在**客户端的最后一道防线**：后端会拒绝，但用户应该被引导到
// 「暂无访问权限」而不是看到一个保存必然失败的页面。这里直接改 `auth` 的
// reactive 状态驱动守卫（`auth.ready = true` 让 ensureAuthReady 短路，不碰网络）。

function loginAs(levels: Record<string, string>, is_admin = false) {
  auth.enabled = true
  auth.ready = true
  auth.needsSetup = false
  auth.authenticated = true
  auth.user = {
    id: 1, username: 'tester', display_name: '测试员', enabled: true,
    role_id: null, role_name: null, role_title: null,
    permissions: {}, levels, is_admin,
    last_login_at: null, created_at: null,
  } as AuthUser
}

beforeEach(async () => {
  // 重置到「已登录管理员」以外的干净起点，再回到 /admin 基准页
  auth.enabled = true
  auth.ready = true
  auth.authenticated = false
  auth.user = null
  readerAuth.authenticated = false
  readerAuth.reader = null
  await router.push('/')
  await router.isReady()
})

describe('route guard: authentication', () => {
  it('redirects anonymous users from /admin to /login with redirect query', async () => {
    await router.push('/admin/circulation')
    expect(router.currentRoute.value.path).toBe('/login')
    expect(router.currentRoute.value.query.redirect).toBe('/admin/circulation')
  })

  it('lets authenticated users through the module gate', async () => {
    loginAs({}, true)
    await router.push('/admin/circulation')
    expect(router.currentRoute.value.path).toBe('/admin/circulation')
  })

  it('sends an authenticated user on /login back to /admin', async () => {
    loginAs({}, true)
    await router.push('/login')
    expect(router.currentRoute.value.path).toBe('/admin')
  })
})

describe('route guard: write level (meta.write)', () => {
  it('blocks a read-level librarian from the record edit page', async () => {
    loginAs({ records: 'read' })
    await router.push('/admin/records/123/edit')
    expect(router.currentRoute.value.path).toBe('/admin/no-access')
  })

  it('admits a write-level librarian to the record edit page', async () => {
    loginAs({ records: 'write' })
    await router.push('/admin/records/123/edit')
    expect(router.currentRoute.value.path).toBe('/admin/records/123/edit')
  })

  it('blocks a read-level librarian from global settings and user management', async () => {
    loginAs({ records: 'read' })
    await router.push('/admin/settings')
    expect(router.currentRoute.value.path).toBe('/admin/no-access')
    await router.push('/admin/users')
    expect(router.currentRoute.value.path).toBe('/admin/no-access')
  })

  it('keeps read-level pages open to a read-level librarian', async () => {
    loginAs({ records: 'read' })
    await router.push('/admin/records/123')
    expect(router.currentRoute.value.path).toBe('/admin/records/123')
  })

  it('admins pass the write gate everywhere', async () => {
    loginAs({}, true)
    await router.push('/admin/settings')
    expect(router.currentRoute.value.path).toBe('/admin/settings')
  })
})

describe('can(module, level)', () => {
  it('treats write as implying read', () => {
    loginAs({ records: 'write' })
    expect(can('records', 'read')).toBe(true)
    expect(can('records', 'write')).toBe(true)
  })

  it('does not treat read as implying write', () => {
    loginAs({ records: 'read' })
    expect(can('records', 'read')).toBe(true)
    expect(can('records', 'write')).toBe(false)
  })

  it('denies unknown modules and honours admin bypass', () => {
    loginAs({})
    expect(can('barcodes')).toBe(false)
    loginAs({}, true)
    expect(can('barcodes', 'write')).toBe(true)
  })
})

describe('passive 401 redirect', () => {
  // 监听器里的 router.push 是异步的：事件发出后要等微任务排空才能断言路由
  const flush = () => new Promise((resolve) => setTimeout(resolve, 0))

  it('sends the user to /login with the current path when a 401 fires', async () => {
    loginAs({}, true)
    await router.push('/admin/circulation')
    window.dispatchEvent(new CustomEvent('jianji:unauthorized'))
    await flush()
    // auth.ts 的监听器清状态，router 的监听器负责跳转
    expect(router.currentRoute.value.path).toBe('/login')
    expect(router.currentRoute.value.query.redirect).toBe('/admin/circulation')
  })

  it('stays on /login when a 401 fires on the login page itself', async () => {
    loginAs({}, true)
    await router.push('/login')
    window.dispatchEvent(new CustomEvent('jianji:unauthorized'))
    await flush()
    expect(router.currentRoute.value.path).toBe('/login')
  })

  it('routes a reader-session 401 back to the reader login', async () => {
    loginAs({}, true)
    readerAuth.authenticated = true
    readerAuth.reader = { id: 1, card_no: '0001', name: '读者', display_name: '读者' } as never
    await router.push('/reader/loans')
    window.dispatchEvent(new CustomEvent('jianji:reader-unauthorized'))
    await flush()
    expect(router.currentRoute.value.path).toBe('/login')
    expect(router.currentRoute.value.query.portal).toBe('reader')
    expect(router.currentRoute.value.query.redirect).toBe('/reader/loans')
  })
})
