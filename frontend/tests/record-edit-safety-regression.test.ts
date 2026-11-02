import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, shallowMount } from '@vue/test-utils'
import { nextTick } from 'vue'
// 422 门槛用例要 reject 一个**组件实际会收到**的错误形状：
// API 层（lib/api.ts）统一包装成 ApiError（status 在 .status、响应体在 .raw）
import { ApiError } from '../src/lib/api'

const mocks = vi.hoisted(() => ({ post: vi.fn(), put: vi.fn(), get: vi.fn(),
  getRecord: vi.fn(), update: vi.fn(), create: vi.fn(), add: vi.fn(), list: vi.fn(), confirm: vi.fn(),
  push: vi.fn(), replace: vi.fn(), leave: vi.fn(), routeUpdate: vi.fn() }))
vi.mock('vue-router', async () => {
  const { reactive } = await import('vue')
  const route = reactive({ params: { id: '1' }, path: '/admin/records/1/edit', query: {} })
  return { useRoute: () => route, useRouter: () => ({ push: mocks.push, replace: mocks.replace }),
    onBeforeRouteLeave: mocks.leave, onBeforeRouteUpdate: mocks.routeUpdate }
})
vi.mock('element-plus', () => ({ ElMessage: { success: vi.fn(), info: vi.fn(), error: vi.fn(), warning: vi.fn() },
  ElMessageBox: { confirm: mocks.confirm } }))
vi.mock('../src/legacy/api', () => ({ http: { get: mocks.get, post: mocks.post, put: mocks.put },
  getRecord: mocks.getRecord, updateRecord: mocks.update, createRecord: mocks.create,
  addHoldings: mocks.add, listHoldings: mocks.list,
  getDefaultCurrency: async () => ({ code: 'CNY' }), getMarcFields: async () => ({ fields: [] }),
  getShelfNoSetting: async () => ({ enabled: true }), putShelfNoSetting: vi.fn(),
  getDictionary: async () => ({ items: [] }), lookupLanguages: async () => ({ items: [] }),
  lookupPlaces: async () => ({ items: [] }), lookupPublisherByIsbn: async () => ({ found: false }),
  lookupPlaceByPublisher: async () => ({ found: false }), apiErrorText: () => '请求失败',
  deleteRecord: vi.fn(), deleteHolding: vi.fn(), catalogSearch: vi.fn(), quickEntry: vi.fn(),
}))
const { useRoute } = await import('vue-router')
const { default: RecordEdit } = await import('../src/legacy/views/RecordEdit.vue')
const route = useRoute() as any
const originalFields = [
  { tag: '010', i1: ' ', i2: ' ', sf: [['a', '9780000000000'], ['b', '平装'], ['d', 'USD12.00']] },
  { tag: '200', i1: '1', i2: ' ', sf: [['a', '原书名']] },
  { tag: '690', i1: ' ', i2: ' ', sf: [['a', 'I247']] },
]
function record(id = 1) {
  return { id, fields: structuredClone(originalFields), leader: '00000nam0 2200000   4500',
    library_id: 7, search: { class_no: 'I247' }, volume: '上册', volume_seq: 1,
    shelf_no: 'G-1', item_type: '中文图书', reader_level: 'cd ', book_no: '77' }
}
function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>(r => { resolve = r })
  return { promise, resolve }
}
const wrappers: ReturnType<typeof shallowMount>[] = []
async function mountEditor() {
  const wrapper = shallowMount(RecordEdit, { global: { config: { warnHandler: () => {} },
    stubs: { ElCard: { template: '<div><slot /></div>' } }, directives: { loading: () => {} } } })
  wrappers.push(wrapper)
  await flushPromises()
  return { wrapper, state: (wrapper.vm.$ as any).setupState }
}
function leaveGuard() { return mocks.leave.mock.calls.at(-1)![0] }
function optimized(title: string) {
  const d = record()
  d.fields[1]!.sf[0]![1] = title
  return { data: { ...d, applied: ['规范题名'], findings: [] } }
}

beforeEach(() => {
  vi.clearAllMocks()
  route.params = { id: '1' }
  route.path = '/admin/records/1/edit'
  route.query = {}
  localStorage.clear()
  sessionStorage.clear()
  localStorage.setItem('jianji.library', '3')
  mocks.getRecord.mockImplementation(async (id: number) => record(id))
  mocks.list.mockResolvedValue({ holdings: [{ id: 1, barcode: '00000001', call_no: 'I247/77' }] })
  mocks.get.mockImplementation(async (url: string) => ({ data: url === '/settings/optimize'
    ? { mode: 'manual' } : url === '/settings/barcode' ? { value: { digits: [8] } } : {} }))
  mocks.post.mockResolvedValue(optimized('规范书名'))
  mocks.put.mockResolvedValue({ data: {} })
  mocks.update.mockImplementation(async (_id: number, payload: any) => ({ ...record(), ...payload }))
  mocks.create.mockImplementation(async (payload: any) => ({ ...record(9), ...payload }))
  mocks.add.mockResolvedValue({ created: 1 })
  mocks.confirm.mockResolvedValue('confirm')
  mocks.push.mockResolvedValue(undefined)
  mocks.replace.mockResolvedValue(undefined)
})
afterEach(() => { wrappers.splice(0).forEach(w => w.unmount()) })

describe('整稿替换版本协议', () => {
  // 原 applyLocalOptimize（套录底稿的自动规范入口）已并入 useOptimize 的
  // autoOptimizeAfterLoad(tempPayload 分支)，入口名随之换；协议本身不变。
  it.each([
    ['runOptimize', (state: any) => state.runOptimize()],
    ['autoOptimizeAfterLoad', (state: any) => {
      state.optimizeMode = 'auto'
      return state.autoOptimizeAfterLoad(true)
    }],
  ])('%s 不覆盖请求期间继续输入', async (_action, run) => {
    const { state } = await mountEditor()
    const d = deferred<any>()
    mocks.post.mockReturnValue(d.promise)
    const pending = run(state)
    state.simple['200a'] = '继续输入'
    d.resolve(optimized('过期规范书名'))
    await pending
    expect(state.simple['200a']).toBe('继续输入')
    expect(state.optApplied).toEqual([])
    expect(state.isDirty).toBe(true)
    expect(state.optimizing).toBe(false)
  })
  it('恢复原输入也不接受旧编辑版本', async () => {
    const { state } = await mountEditor()
    const d = deferred<any>()
    mocks.post.mockReturnValue(d.promise)
    const pending = state.runOptimize()
    state.simple['200a'] = '修改'
    state.simple['200a'] = '原书名'
    d.resolve(optimized('过期'))
    await pending
    expect(state.simple['200a']).toBe('原书名')
  })
  it('最新请求先完成，较早响应不能覆盖或提前结束 optimizing', async () => {
    const { state } = await mountEditor()
    const first = deferred<any>(); const second = deferred<any>()
    mocks.post.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise)
    const a = state.runOptimize(); const b = state.runOptimize()
    first.resolve(optimized('早请求'))
    await a
    expect(state.optimizing).toBe(true)
    expect(state.simple['200a']).toBe('原书名')
    second.resolve(optimized('最新请求'))
    await b
    expect(state.simple['200a']).toBe('最新请求')
    expect(state.isDirty).toBe(true) // 只改界面，不等于保存
  })
  it('换记录时丢弃旧规范结果，新建页不继承旧库或其他字段', async () => {
    const { state } = await mountEditor()
    const d = deferred<any>()
    mocks.post.mockReturnValue(d.promise)
    const pending = state.runOptimize()
    route.params = {}
    route.path = '/admin/records/new'
    await flushPromises()
    d.resolve(optimized('上一条记录'))
    await pending
    expect(state.simple['200a']).toBe('')
    expect(state.extra.volume).toBe('')
    expect(state.currentLibId).toBe(3)
    expect(state.isDirty).toBe(false)
  })
  it('套录 prepare/规范在途输入变化时不安装过期底稿', async () => {
    const { state } = await mountEditor()
    route.params = {}
    route.path = '/admin/records/temp'
    await flushPromises()
    const d = deferred<any>()
    mocks.post.mockReturnValueOnce(d.promise)
    const pending = state.applyCatalogedRecord(record())
    await nextTick()
    state.simple['200a'] = '手工底稿'
    d.resolve({ data: { record: record() } })
    expect(await pending).toBe(false)
    expect(state.simple['200a']).toBe('手工底稿')
  })
  it('快速套录也应用同协议，不调用 prepare，保留来源记录级内容', async () => {
    const { state } = await mountEditor()
    route.params = {}
    route.path = '/admin/records/temp'
    await flushPromises()
    state.optimizeMode = 'auto'
    const d = deferred<any>()
    mocks.post.mockReturnValueOnce(d.promise)
    const pending = state.onQuickRecord(record())
    await nextTick()
    state.extra.shelfNo = '手工架位'
    d.resolve(optimized('旧底稿'))
    await pending
    expect(state.extra.shelfNo).toBe('手工架位')
    expect(mocks.post).toHaveBeenLastCalledWith('/ai/optimize-local', expect.anything())
    state.optimizeMode = 'manual'
    await state.onQuickRecord(record())
    expect(state.extra.volume).toBe('上册')
    expect(state.extra.bookNo).toBe('77')
    expect(state.isDirty).toBe(true)
  })
})

describe('完整保存基线与离开确认', () => {
  it('刚载入的有内容底稿尚未入库，也需确认后才能离开', async () => {
    route.params = {}
    route.path = '/admin/records/temp'
    sessionStorage.setItem('cataloging_temp', JSON.stringify(record()))
    const { state } = await mountEditor()
    expect(state.isDirty).toBe(true)
    mocks.confirm.mockRejectedValueOnce('cancel')
    expect(await leaveGuard()({ fullPath: '/admin/records' })).toBe(false)
  })
  it('保存保留未求值价格草稿，不把数字前缀落库或误清脏', async () => {
    const { state } = await mountEditor()
    state.priceDraft = '135'
    state.priceDraft = '135/5'
    await state.saveAndHoldings()
    const payload = mocks.update.mock.calls.at(-1)![1]
    expect(payload.fields.find((f: any) => f.tag === '010').sf.find((s: any) => s[0] === 'd')[1]).toBe('USD12.00')
    expect(state.priceDraft).toBe('135/5')
    expect(state.isDirty).toBe(true)
    state.commitPrice()
    await state.saveAndHoldings()
    expect(state.isDirty).toBe(false)
    expect(state.priceDraft).toBe('27.00')
  })
  it.each(['fields', 'leader', 'volume', 'volumeSeq', 'shelfNo', 'bookNo', 'itemType', 'readerLevel', 'location', 'barcode', 'priceDraft'])
  ('%s 修改独立触发脏状态和离开确认', async key => {
    const { state } = await mountEditor()
    expect(state.isDirty).toBe(false)
    if (key === 'fields') { state.tab = 'full'; state.fields[1].sf[0][1] = '修改书名' }
    else if (key === 'leader') state.detail.leader = '新的 leader'
    else if (key === 'location') state.location = '阅览室'
    else if (key === 'barcode') state.barcodeInputValue = '00000009'
    else if (key === 'priceDraft') state.priceDraft = '135/5'
    else state.extra[key] = key === 'volumeSeq' ? 2 : '修改值'
    expect(state.isDirty).toBe(true)
    mocks.confirm.mockRejectedValueOnce('cancel')
    expect(await leaveGuard()({ fullPath: '/admin/records' })).toBe(false)
    expect(state.isDirty).toBe(true)
    expect(mocks.confirm).toHaveBeenCalledTimes(1)
  })
  it('未修改的加载和保存成功不确认；保存失败/馆藏失败不清脏', async () => {
    const { state } = await mountEditor()
    expect(await leaveGuard()({ fullPath: '/admin/records' })).toBe(true)
    expect(mocks.confirm).not.toHaveBeenCalled()
    state.simple['200a'] = '改后的题名'
    mocks.update.mockRejectedValueOnce(new Error('save failed'))
    await state.saveAndHoldings()
    expect(state.isDirty).toBe(true)
    state.barcodeInputValue = '00000009'
    mocks.add.mockRejectedValueOnce(new Error('holdings failed'))
    await state.saveAndHoldings()
    expect(state.isDirty).toBe(true)
    expect(state.barcodeInputValue).toBe('00000009')
    await state.saveAndHoldings()
    expect(state.isDirty).toBe(false)
    expect(state.barcodeInputValue).toBe('')
  })
  it('质检修复复用版本协议，切换记录清掉旧强制保存入口', async () => {
    const { state } = await mountEditor()
    state.simple['200a'] = '待保存'
    mocks.update.mockRejectedValueOnce(new ApiError(422, '质检未过',
      { detail: { errors: [{ level: 'ERROR' }] } }))
    await state.saveAndHoldings()
    expect(state.gateDlg).toBe(true)
    expect(state.isDirty).toBe(true)
    const d = deferred<any>()
    mocks.post.mockReturnValue(d.promise)
    const pending = state.optimizeFromGate()
    state.simple['200a'] = '继续修改'
    d.resolve(optimized('旧质检修复'))
    await pending
    expect(state.simple['200a']).toBe('继续修改')
    route.params = { id: '2' }
    route.path = '/admin/records/2/edit'
    await flushPromises()
    expect(state.gateDlg).toBe(false)
    const calls = mocks.update.mock.calls.length
    await state.forceSave()
    expect(mocks.update).toHaveBeenCalledTimes(calls)
  })
  it('保存期间继续编辑不被响应覆盖，不清脏', async () => {
    const { state } = await mountEditor()
    state.simple['200a'] = '待保存'
    const d = deferred<any>()
    mocks.update.mockReturnValue(d.promise)
    const pending = state.saveAndHoldings()
    state.simple['200a'] = '保存期间继续输入'
    d.resolve(record())
    await pending
    expect(state.simple['200a']).toBe('保存期间继续输入')
    expect(state.isDirty).toBe(true)
  })
  it('刷新保护注册/移除；临时底稿回写全部记录级内容和待录条码', async () => {
    route.params = {}
    route.path = '/admin/records/temp'
    sessionStorage.setItem('cataloging_temp', JSON.stringify(record()))
    const { state, wrapper } = await mountEditor()
    state.extra.shelfNo = 'G-99'
    state.location = '阅览室'
    state.barcodeInputValue = '00000009'
    state.priceDraft = '135/5'
    const event = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(event)
    expect(event.defaultPrevented).toBe(true)
    const temp = JSON.parse(sessionStorage.getItem('cataloging_temp')!)
    expect(temp).toMatchObject({ leader: record().leader, volume: '上册', volume_seq: 1,
      shelf_no: 'G-99', item_type: '中文图书', reader_level: 'cd ', book_no: '77',
      location: '阅览室', barcode: '00000009', priceDraft: '135/5' })
    expect(JSON.stringify(temp.fields)).not.toContain('135/5')
    wrapper.unmount()
    wrappers.splice(wrappers.indexOf(wrapper), 1)
    const after = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(after)
    expect(after.defaultPrevented).toBe(false)
    const restored = await mountEditor()
    expect(restored.state.extra.shelfNo).toBe('G-99')
    expect(restored.state.barcodeInputValue).toBe('00000009')
    expect(restored.state.isDirty).toBe(true)
  })
  it('套录替换取消保留原稿，确认后才替换；返回按钮经过相同路由守卫', async () => {
    const { state } = await mountEditor()
    state.extra.shelfNo = '修改架位'
    mocks.confirm.mockRejectedValueOnce('cancel')
    await state.onQuickRecord(record())
    expect(mocks.push).not.toHaveBeenCalled()
    expect(state.extra.shelfNo).toBe('修改架位')
    expect(state.isDirty).toBe(true)
    await state.onQuickRecord(record())
    expect(mocks.push).toHaveBeenCalledWith('/admin/records/temp?from=quick')
    const to = { fullPath: '/admin/records' }
    expect(await leaveGuard()(to)).toBe(true)
    expect(mocks.confirm).toHaveBeenCalledTimes(3)
  })
  it('新建保存自动跳转不二次确认，条码回车入口仍创建含条码记录', async () => {
    route.params = {}
    route.path = '/admin/records/new'
    const { state } = await mountEditor()
    state.simple['200a'] = '新书'
    state.barcodeInputValue = '00000009'
    mocks.replace.mockImplementation(async (target: string) => {
      expect(await leaveGuard()({ fullPath: target })).toBe(true)
      route.params = { id: '9' }
      route.path = target
    })
    await state.saveAndHoldings(true)
    await flushPromises()
    expect(mocks.create).toHaveBeenCalledWith(expect.objectContaining({ barcodes: ['00000009'], library_id: 3 }))
    expect(mocks.replace).toHaveBeenCalledWith('/admin/records/9/edit')
    expect(mocks.confirm).not.toHaveBeenCalled()
    expect(state.isDirty).toBe(false)
  })
  it('新建保存期间新输入随新记录身份保留，不二次创建', async () => {
    route.params = {}
    route.path = '/admin/records/new'
    const { state } = await mountEditor()
    state.simple['200a'] = '待保存'
    state.barcodeInputValue = '00000009'
    const d = deferred<any>()
    mocks.create.mockReturnValue(d.promise)
    mocks.replace.mockImplementation(async (target: string) => {
      expect(await leaveGuard()({ fullPath: target })).toBe(true)
      route.params = { id: '9' }
      route.path = target
    })
    const pending = state.saveNew()
    state.simple['200a'] = '继续输入'
    d.resolve(record(9))
    await pending
    await flushPromises()
    expect(state.simple['200a']).toBe('继续输入')
    expect(state.recordIdOrNull).toBe(9)
    expect(state.isDirty).toBe(true)
    expect(mocks.confirm).not.toHaveBeenCalled()
  })
})
