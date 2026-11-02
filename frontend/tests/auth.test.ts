import { beforeEach, describe, expect, it } from 'vitest'
import { auth, can, type AuthUser } from '../src/lib/auth'

function makeUser(overrides: Partial<AuthUser> = {}): AuthUser {
  return {
    id: 1,
    username: 'staff',
    display_name: '测试馆员',
    enabled: true,
    role_id: 2,
    role_name: 'librarian',
    role_title: '馆员',
    permissions: {},
    levels: {},
    is_admin: false,
    last_login_at: null,
    created_at: null,
    ...overrides,
  }
}

beforeEach(() => {
  Object.assign(auth, {
    ready: true,
    enabled: true,
    needsSetup: false,
    authenticated: true,
    user: null,
    error: '',
  })
})

describe('can()', () => {
  it('does not gate anything when authentication is disabled', () => {
    auth.enabled = false
    expect(can('circulation')).toBe(true)
  })

  it('denies module access when signed out', () => {
    auth.authenticated = false
    expect(can('circulation')).toBe(false)
  })

  it('lets admins into every module', () => {
    auth.user = makeUser({ is_admin: true, levels: {} })
    expect(can('circulation', 'write')).toBe(true)
    expect(can('settings')).toBe(true)
  })

  it('maps module levels to read and write checks', () => {
    auth.user = makeUser({ levels: { circulation: 'write', records: 'read' } })
    expect(can('circulation', 'write')).toBe(true)
    expect(can('records')).toBe(true)
    expect(can('records', 'write')).toBe(false)
    expect(can('settings')).toBe(false)
    expect(can(undefined, 'write')).toBe(true)
  })
})
