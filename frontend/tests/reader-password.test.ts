import { beforeEach, describe, expect, it, vi } from 'vitest'

// 读者改密码最容易漏的一步：后端改密会清掉该读者**全部会话**再换发令牌，
// 前端不写回新令牌的话，下一个请求就 401 —— 表现成"刚改完密码被踢出去"。

const readerPost = vi.fn()
const setReaderToken = vi.fn()

vi.mock('../src/lib/api', () => ({
  readerGet: vi.fn(),
  readerPost: (...a: unknown[]) => readerPost(...a),
  setReaderToken: (...a: unknown[]) => setReaderToken(...a),
  getReaderToken: () => 'old-token',
  ApiError: class extends Error {},
}))

const { readerAuth, readerChangePassword } = await import('../src/lib/readerAuth')

beforeEach(() => {
  readerPost.mockReset()
  setReaderToken.mockReset()
  readerAuth.authenticated = false
})

describe('readerChangePassword', () => {
  it('stores the rotated token the backend hands back', async () => {
    readerPost.mockResolvedValue({ ok: true, token: 'fresh-token', message: '密码已修改' })

    const message = await readerChangePassword('reader123', 'reader456')

    expect(readerPost).toHaveBeenCalledWith('/portal/auth/password', {
      old_password: 'reader123',
      new_password: 'reader456',
    })
    // 不写回就等于把读者踢下线
    expect(setReaderToken).toHaveBeenCalledWith('fresh-token')
    expect(readerAuth.authenticated).toBe(true)
    expect(message).toBe('密码已修改')
  })

  it('does not touch the token when the backend rejects the change', async () => {
    readerPost.mockRejectedValue(new Error('原密码不正确'))

    await expect(readerChangePassword('bad', 'reader456')).rejects.toThrow('原密码不正确')
    expect(setReaderToken).not.toHaveBeenCalled()
  })
})
