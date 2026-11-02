import { beforeEach, describe, expect, it, vi } from 'vitest'
import { computed, ref } from 'vue'

// 「一键套录」把后端早已实现的 POST /api/quick-entry 接进编目页（原本零调用）。
// 这里用假 router + 假回调直接驱动 composable，钉住三件事：
//   1. 只有 ISBN 形态才允许触发；
//   2. 三种状态各自的分支（ok / exists / miss）都走对；
//   3. **ok 时绝不调用 onCataloged**（那条路会再 prepare 一次，而 localize 不幂等）。

const quickEntry = vi.fn()
const catalogSearch = vi.fn()
const confirm = vi.fn()

vi.mock('../src/legacy/api', () => ({
  quickEntry: (...a: unknown[]) => quickEntry(...a),
  catalogSearch: (...a: unknown[]) => catalogSearch(...a),
  apiErrorText: (_e: unknown, what = '') => `${what}失败`,
}))

vi.mock('element-plus', () => ({
  ElMessage: { success: vi.fn(), warning: vi.fn(), error: vi.fn(), info: vi.fn() },
  ElMessageBox: { confirm: (...a: unknown[]) => confirm(...a) },
}))

const { useCatalogLookup } = await import('../src/legacy/composables/useCatalogLookup')
const { ElMessage } = await import('element-plus')

function build(opts: { isNew?: boolean; isTemp?: boolean } = {}) {
  const push = vi.fn()
  const onCataloged = vi.fn()
  const onQuickRecord = vi.fn()
  const api = useCatalogLookup({
    router: { push, replace: push } as any,
    isNew: computed(() => opts.isNew ?? true),
    isTemp: computed(() => opts.isTemp ?? false),
    recordIdOrNull: computed(() => null),
    currentLibId: computed(() => 1),
    barcodeInput: ref(null),
    candOpenBtn: ref(null),
    onCataloged,
    onQuickRecord,
  })
  return { api, push, onCataloged, onQuickRecord }
}

const okPayload = {
  status: 'ok',
  hit_source: '本地资源库',
  record: { leader: '00000nam0 2200000   4500', fields: [{ tag: '001', data: '012X' }], mrk: '' },
}

beforeEach(() => {
  quickEntry.mockReset()
  catalogSearch.mockReset()
  confirm.mockReset()
  vi.mocked(ElMessage.warning).mockReset()
  vi.mocked(ElMessage.success).mockReset()
})

describe('isIsbnLike', () => {
  it('accepts 10/13 digit ISBNs (with dashes or spaces) and rejects words', () => {
    const { api } = build()
    for (const v of ['9787560645308', '978-7-5606-4530-8', '7-5606-4530-0', '756064530X']) {
      api.isbnLookup.value = v
      expect(api.isIsbnLike.value, v).toBe(true)
    }
    for (const v of ['三体', 'ISBN 9787560645308', '12345', '']) {
      api.isbnLookup.value = v
      expect(api.isIsbnLike.value, v).toBe(false)
    }
  })
})

describe('primaryLookup', () => {
  it('routes ISBN input to the one-step entry', async () => {
    quickEntry.mockResolvedValue(okPayload)
    const { api } = build()
    api.isbnLookup.value = '9787560645308'

    await api.primaryLookup()

    expect(quickEntry).toHaveBeenCalledWith('9787560645308', 1)
    expect(catalogSearch).not.toHaveBeenCalled()
  })

  it('routes title/author input to the general candidate search', async () => {
    catalogSearch.mockResolvedValue({ candidates: [] })
    const { api } = build()
    api.isbnLookup.value = '三体'

    await api.primaryLookup()

    // 不传 limit：命中条数由后端按「全局设置 → 资源库 → 套录命中条数」决定。
    // 前端写死一个数字会让那个设置形同虚设，候选列表 / 扩展检索 / 批量套录
    // 各写一个数字也迟早不一致。
    // 带 page（第 0 页）：翻页复用同一个接口，首屏也要显式给页号
    expect(catalogSearch).toHaveBeenCalledWith({ q: '三体', library_id: 1, page: 0 })
    expect(quickEntry).not.toHaveBeenCalled()
  })
})

describe('quickLookup', () => {
  it('hands the localized record to onQuickRecord and never touches onCataloged', async () => {
    quickEntry.mockResolvedValue(okPayload)
    const { api, onQuickRecord, onCataloged } = build()
    api.isbnLookup.value = '9787560645308'

    await api.quickLookup()

    expect(quickEntry).toHaveBeenCalledWith('9787560645308', 1)
    expect(onQuickRecord).toHaveBeenCalledWith(okPayload.record, '本地资源库')
    // 关键不变量：不能再走 prepare 那条路（会重复 localize）
    expect(onCataloged).not.toHaveBeenCalled()
    expect(catalogSearch).not.toHaveBeenCalled()
  })

  it('asks before jumping to an existing record, and prefixes /admin', async () => {
    quickEntry.mockResolvedValue({ status: 'exists', record_id: 2, detail: { title: '设计中的设计' } })
    confirm.mockResolvedValue('confirm')
    const { api, push } = build()
    api.isbnLookup.value = '9787563394180'

    await api.quickLookup()

    expect(confirm).toHaveBeenCalled()
    expect(push).toHaveBeenCalledWith('/admin/records/2')
  })

  it('stays put when the librarian dismisses the confirm', async () => {
    quickEntry.mockResolvedValue({ status: 'exists', record_id: 2, detail: null })
    confirm.mockRejectedValue(new Error('cancel'))
    const { api, push, onQuickRecord } = build()
    api.isbnLookup.value = '9787563394180'

    await api.quickLookup()

    expect(push).not.toHaveBeenCalled()
    expect(onQuickRecord).not.toHaveBeenCalled()
  })

  it('tells the user when every source missed instead of failing silently', async () => {
    quickEntry.mockResolvedValue({ status: 'miss', hint: '全部源未命中：可手工编目' })
    const { api, onQuickRecord, push } = build()
    api.isbnLookup.value = '9780000000002'

    await api.quickLookup()

    expect(ElMessage.warning).toHaveBeenCalledWith('全部源未命中：可手工编目')
    expect(onQuickRecord).not.toHaveBeenCalled()
    expect(push).not.toHaveBeenCalled()
  })

  it('does nothing on empty input or while already busy', async () => {
    quickEntry.mockResolvedValue(okPayload)
    const { api } = build()
    api.isbnLookup.value = '   '
    await api.quickLookup()
    expect(quickEntry).not.toHaveBeenCalled()

    api.isbnLookup.value = '9787560645308'
    api.quickBusy.value = true
    await api.quickLookup()
    expect(quickEntry).not.toHaveBeenCalled()
  })
})
