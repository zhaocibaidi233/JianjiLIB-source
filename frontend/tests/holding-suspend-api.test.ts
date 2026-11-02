import { afterEach, describe, expect, it, vi } from 'vitest'
import { http } from '../src/lib/api'
import { releaseHoldings, suspendHoldings } from '../src/legacy/api'

// 停流通 / 解除停流通的请求体必须与后端 `SuspendIn` / `ReleaseIn` 逐字对齐：
// 字段名写错不会报错——可选字段会被 FastAPI 当成"没传"，现象是「填了审查批次，
// 详情/审计里却没有」，很难查。这组用例把 body 的形状钉住。

afterEach(() => {
  vi.restoreAllMocks()
})

describe('holding suspend / release payloads', () => {
  it('posts suspend with ids + reason and all four audit fields', async () => {
    const post = vi.spyOn(http, 'post')
      .mockResolvedValue({ data: { suspended: 1, failed: [] } } as any)

    await suspendHoldings([7], {
      reason: '审查清理-不适宜',
      review_batch_no: 'SH-2026-01',
      approval_ref: '校图审〔2026〕3号',
      destination: '闭架库 A',
    })

    expect(post).toHaveBeenCalledWith('/holdings/suspend', {
      ids: [7],
      reason: '审查清理-不适宜',
      review_batch_no: 'SH-2026-01',
      approval_ref: '校图审〔2026〕3号',
      destination: '闭架库 A',
    })
  })

  it('does not invent optional fields when only the reason is given', async () => {
    const post = vi.spyOn(http, 'post')
      .mockResolvedValue({ data: { suspended: 1, failed: [] } } as any)

    await suspendHoldings([9], { reason: '外观差' })

    const body = post.mock.calls[0][1] as Record<string, unknown>
    expect(body).toEqual({ ids: [9], reason: '外观差' })
    expect('review_batch_no' in body).toBe(false)
  })

  it('posts release with ids only', async () => {
    const post = vi.spyOn(http, 'post')
      .mockResolvedValue({ data: { released: 1, failed: [] } } as any)

    await releaseHoldings([11])

    expect(post).toHaveBeenCalledWith('/holdings/release', { ids: [11] })
  })

  it('returns the per-item outcome so the page can show failed[].reason', async () => {
    vi.spyOn(http, 'post').mockResolvedValue({
      data: { succeeded: [], failed: [{ id: 3, reason: '该册有未归还的借阅记录（外借中），不能停流通' }],
              suspended: 0, skipped: 1 },
    } as any)

    const d = await suspendHoldings([3], { reason: '审查清理' })

    // 后端把业务拒绝放在 200 的 failed[] 里，不是 400 —— 页面必须读这个数组
    expect(d.suspended).toBe(0)
    expect(d.failed[0].reason).toContain('外借中')
  })
})
