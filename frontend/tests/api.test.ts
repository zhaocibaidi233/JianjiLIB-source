import { beforeEach, describe, expect, it } from 'vitest'
import {
  ApiError,
  errorMessage,
  getOperator,
  getReaderToken,
  getToken,
  setOperator,
  setReaderToken,
  setToken,
} from '../src/lib/api'

// 令牌存取看着是两行 localStorage，但它是「刷新页面还在不在登录态」的全部依据：
// 写错 key 或忘了 remove，表现为"登出了但一刷新又回来了"。

beforeEach(() => {
  localStorage.clear()
})

describe('token storage', () => {
  it('round-trips the staff token', () => {
    expect(getToken()).toBeNull()
    setToken('abc123')
    expect(getToken()).toBe('abc123')
    expect(localStorage.getItem('jianji.token')).toBe('abc123')
  })

  it('removes the key when set to null (logout must leave no trace)', () => {
    setToken('abc123')
    setToken(null)
    expect(getToken()).toBeNull()
    expect(localStorage.getItem('jianji.token')).toBeNull()
  })

  it('keeps the reader session independent from the staff session', () => {
    setToken('staff-token')
    setReaderToken('reader-token')
    expect(getToken()).toBe('staff-token')
    expect(getReaderToken()).toBe('reader-token')
    setReaderToken(null)
    expect(getToken()).toBe('staff-token')
    expect(getReaderToken()).toBeNull()
  })

  it('defaults the operator name to an empty string', () => {
    expect(getOperator()).toBe('')
    setOperator('张三')
    expect(getOperator()).toBe('张三')
  })
})

describe('errorMessage', () => {
  it('prefers the server-provided detail for ApiError', () => {
    expect(errorMessage(new ApiError(403, '没有该模块的权限'))).toBe('没有该模块的权限')
  })

  it('falls back to Error.message', () => {
    expect(errorMessage(new Error('网络断了'))).toBe('网络断了')
  })

  it('stringifies anything else instead of throwing', () => {
    expect(errorMessage('纯字符串')).toBe('纯字符串')
    expect(errorMessage(42)).toBe('42')
  })
})

describe('ApiError', () => {
  it('keeps status, detail and the raw payload for debugging', () => {
    const raw = { detail: '条码重复' }
    const err = new ApiError(400, '条码重复', raw)
    expect(err).toBeInstanceOf(Error)
    expect(err.name).toBe('ApiError')
    expect(err.status).toBe(400)
    expect(err.detail).toBe('条码重复')
    expect(err.raw).toBe(raw)
  })
})
