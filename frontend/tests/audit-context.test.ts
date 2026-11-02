import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { type Component, nextTick } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), readerGet: vi.fn(), readerPost: vi.fn() }))
vi.mock('../src/lib/api', () => ({
  ...api, put: vi.fn(), download: vi.fn(), downloadPost: vi.fn(),
  errorMessage: (error: unknown) => error instanceof Error ? error.message : String(error),
}))
vi.mock('../src/lib/auth', () => ({ can: () => true }))
vi.mock('../src/lib/readerAuth', () => ({ readerAuth: { reader: { id: 1, status: 'ACTIVE' } } }))
vi.mock('../src/lib/toast', () => ({ toast: vi.fn(), toastError: vi.fn() }))

import Circulation from '../src/admin/views/Circulation.vue'
import ImportExport from '../src/admin/views/ImportExport.vue'
import Labels from '../src/admin/views/Labels.vue'
import Seats from '../src/reader/Seats.vue'
import { libraryState, setCurrentLibrary } from '../src/stores/libraries'

function deferred<T = any>() {
  let resolve!: (value: T) => void
  let reject!: (error: Error) => void
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
const wrappers: VueWrapper<any>[] = []
async function open(component: Component, query = '') {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/page', component }, { path: '/:pathMatch(.*)*', component: { template: '<div />' } }],
  })
  await router.push(`/page${query}`)
  await router.isReady()
  const wrapper = mount(component, {
    global: {
      plugins: [router],
      stubs: { AppIcon: true, BookCover: true, BarcodeSvg: true, BaseModal: true, ConfirmDialog: true, PagerBar: true },
    },
  })
  wrappers.push(wrapper)
  await flushPromises()
  return { wrapper, router }
}
function button(wrapper: VueWrapper<any>, text: string) {
  const found = wrapper.findAll('button').find((item) => item.text() === text)
  if (!found) throw new Error(`Missing button: ${text}`)
  return found
}
function disabled(wrapper: VueWrapper<any>, text: string) {
  return (button(wrapper, text).element as HTMLButtonElement).disabled
}
const emptyList = { items: [], total: 0 }
function readerPanel(id: number, card = `R${id}`) {
  return {
    id, card_no: card, name: `读者${id}`, status: 'ACTIVE', status_label: '正常', reader_type_label: '学生',
    rules: { max_items: 10, loan_days: 30, max_renew: 1 }, active_count: 0, overdue_count: 0,
    active_loans: [], fines: { enabled: false, unpaid_count: 0 }, compensations: {},
  }
}
function item(barcode: string) {
  return { holding_id: Number(barcode.slice(1)), record_id: 10, barcode, title: `书${barcode}`, status: 'AVAILABLE', on_loan: false }
}
function stats(value: number) {
  return { summary: { today_borrowed: value, today_returned: 0, active: 0, overdue: 0, readers_active: 0, lost: 0 } }
}
function circulationDefaults() {
  api.get.mockImplementation(async (url: string, params: any) => {
    if (url === '/circulation/reader') return readerPanel(Number(params.card_no.slice(1)), params.card_no)
    if (url === '/circulation/item') return item(params.barcode)
    if (url === '/circulation/stats') return stats(params.library_id)
    if (url === '/circulation/rules') return { rules: { fines: { enabled: false } } }
    return emptyList
  })
  api.post.mockResolvedValue({ loan: { due_on: '2026-11-30' } })
}
async function identify(wrapper: VueWrapper<any>, card = 'R1') {
  await wrapper.get('#reader-card').setValue(card)
  await button(wrapper, '识别').trigger('click')
  await flushPromises()
}
async function scan(wrapper: VueWrapper<any>, barcode: string) {
  await wrapper.get('input[placeholder="图书条码"]').setValue(barcode)
  await button(wrapper, '添加').trigger('click')
  await flushPromises()
}

beforeEach(() => {
  vi.clearAllMocks()
  api.get.mockReset()
  api.post.mockReset()
  api.readerGet.mockReset()
  api.readerPost.mockReset()
  libraryState.items = [{ id: 1, name: '甲库', note: null }, { id: 2, name: '乙库', note: null }]
  libraryState.loaded = true
  libraryState.loading = false
  setCurrentLibrary(1)
})
afterEach(() => {
  wrappers.splice(0).forEach((wrapper) => wrapper.unmount())
})

describe('Circulation component context isolation', () => {
  it('clears the previous reader and list before identifying another reader', async () => {
    circulationDefaults()
    const { wrapper } = await open(Circulation)
    await identify(wrapper)
    await scan(wrapper, 'B1')
    const pending = deferred()
    api.get.mockImplementation((url: string) => url === '/circulation/reader' ? pending.promise : Promise.resolve(emptyList))
    await wrapper.get('#reader-card').setValue('R2')
    expect(wrapper.find('.lookup-panel').exists()).toBe(false)
    expect(wrapper.findAll('.borrow-item')).toHaveLength(0)
    expect(disabled(wrapper, '确认外借')).toBe(true)
    await button(wrapper, '识别').trigger('click')
    await button(wrapper, '确认外借').trigger('click')
    expect(api.post).not.toHaveBeenCalled()
    pending.resolve(readerPanel(2))
    await flushPromises()
    expect(wrapper.get('.lookup-panel').text()).toContain('读者2')
  })

  it('discards late reader and barcode responses after library changes', async () => {
    circulationDefaults()
    const oldReader = deferred()
    const getDefault = api.get.getMockImplementation()!
    api.get.mockImplementation((url: string, params: any) =>
      url === '/circulation/reader' && params.library_id === 1 ? oldReader.promise : getDefault(url, params))
    const { wrapper } = await open(Circulation)
    await wrapper.get('#reader-card').setValue('R1')
    await button(wrapper, '识别').trigger('click')
    setCurrentLibrary(2)
    await nextTick()
    await identify(wrapper, 'R2')
    oldReader.resolve(readerPanel(1))
    await flushPromises()
    expect(wrapper.get('.lookup-panel').text()).toContain('读者2')
    const oldItem = deferred()
    api.get.mockImplementation((url: string, params: any) => url === '/circulation/item' ? oldItem.promise : getDefault(url, params))
    await wrapper.get('input[placeholder="图书条码"]').setValue('B1')
    await button(wrapper, '添加').trigger('click')
    setCurrentLibrary(1)
    await flushPromises()
    oldItem.resolve(item('B1'))
    await flushPromises()
    expect(wrapper.findAll('.borrow-item')).toHaveLength(0)
    expect(wrapper.find('.lookup-panel').exists()).toBe(false)
  })

  it('freezes the entire borrow batch and blocks editing while posting', async () => {
    circulationDefaults()
    const { wrapper, router } = await open(Circulation)
    await identify(wrapper)
    await scan(wrapper, 'B1')
    await scan(wrapper, 'B2')
    const first = deferred()
    api.post.mockImplementationOnce(() => first.promise)
    await button(wrapper, '确认外借').trigger('click')
    expect((wrapper.get('#reader-card').element as HTMLInputElement).disabled).toBe(true)
    expect(disabled(wrapper, '添加')).toBe(true)
    expect(wrapper.findAll('[aria-label="移出待借清单"]').every((node) => (node.element as HTMLButtonElement).disabled)).toBe(true)
    await button(wrapper, '归还R').trigger('click')
    await router.push('/page?reader=R2&mode=return')
    expect(wrapper.find('#reader-card').exists()).toBe(true)
    expect((wrapper.get('#reader-card').element as HTMLInputElement).value).toBe('R1')
    // 全局库选择器属于外部组件：即使切库，后续写入也必须沿用提交时快照。
    setCurrentLibrary(2)
    await flushPromises()
    first.resolve({ loan: { due_on: '2026-11-30' } })
    await flushPromises()
    expect(api.post.mock.calls).toEqual([
      ['/circulation/borrow', { library_id: 1, reader_id: 1, barcode: 'B1' }],
      ['/circulation/borrow', { library_id: 1, reader_id: 1, barcode: 'B2' }],
    ])
    expect(wrapper.find('.lookup-panel').exists()).toBe(false)
    expect(wrapper.findAll('.borrow-item')).toHaveLength(0)
    expect(api.get.mock.calls.filter(([url]) => url === '/circulation/reader')).toHaveLength(1)
  })

  it('retains failed items when refreshing the same reader after a partial borrow', async () => {
    circulationDefaults()
    const { wrapper } = await open(Circulation)
    await identify(wrapper)
    await scan(wrapper, 'B1')
    await scan(wrapper, 'B2')
    api.post.mockRejectedValueOnce(new Error('不可借')).mockResolvedValueOnce({ loan: { due_on: '2026-11-30' } })
    await button(wrapper, '确认外借').trigger('click')
    await flushPromises()
    expect(wrapper.findAll('.borrow-item')).toHaveLength(1)
    expect(wrapper.get('.borrow-item').text()).toContain('B1')
    expect(wrapper.get('.lookup-panel').text()).toContain('读者1')
    expect(disabled(wrapper, '确认外借')).toBe(false)
  })

  it('keeps newer library statistics, preview and ledger when old responses finish last', async () => {
    circulationDefaults()
    const oldStats = deferred()
    const oldPreview = deferred()
    const oldList = deferred()
    const getDefault = api.get.getMockImplementation()!
    api.get.mockImplementation((url: string, params: any) => {
      if (params?.library_id === 1 && url === '/circulation/stats') return oldStats.promise
      if (params?.library_id === 1 && url === '/circulation/loans') return params.page_size === 6 ? oldPreview.promise : oldList.promise
      return getDefault(url, params)
    })
    const { wrapper } = await open(Circulation)
    setCurrentLibrary(2)
    await flushPromises()
    oldStats.resolve(stats(999))
    oldPreview.resolve({ items: [{ id: 1, title: '旧预览' }], total: 999 })
    oldList.resolve({ items: [{ id: 1, title: '旧列表' }], total: 999 })
    await flushPromises()
    expect(wrapper.findAll('.metric-value')[0].text()).toBe('2册')
    expect(wrapper.text()).not.toContain('旧预览')
    expect(wrapper.text()).not.toContain('旧列表')
  })

  it('discards earlier ledger tab requests within the same library', async () => {
    circulationDefaults()
    const { wrapper } = await open(Circulation)
    const old = deferred()
    const getDefault = api.get.getMockImplementation()!
    api.get.mockImplementation((url: string, params: any) =>
      url === '/circulation/loans' && params.status === 'overdue' && params.page_size === 20 ? old.promise : getDefault(url, params))
    await wrapper.findAll('[role="tab"]').find((node) => node.text().startsWith('逾期'))!.trigger('click')
    await wrapper.findAll('[role="tab"]').find((node) => node.text().startsWith('已归还'))!.trigger('click')
    await flushPromises()
    old.resolve({ items: [{ id: 1, title: '旧逾期记录' }], total: 1 })
    await flushPromises()
    expect(wrapper.text()).not.toContain('旧逾期记录')
    expect(wrapper.text()).toContain('当前没有已归还记录')
  })
})

function inspection(sheet = '甲表', header = 1, name = '题名列') {
  return {
    sheets: ['甲表', '乙表'], sheet, header_row: header, columns: [{ index: 0, name, sample: '书名' }],
    mapping: { '0': 'title' }, fields: [{ key: 'title', label: '题名', required: true }, { key: 'barcode', label: '条码', required: false }],
    preview: [], sheets_info: [], data_rows: 1,
  }
}
async function excelPage() {
  api.get.mockResolvedValue([])
  const page = await open(ImportExport)
  await button(page.wrapper, '发货清单').trigger('click')
  return page
}
async function pickExcel(wrapper: VueWrapper<any>, file: File) {
  const input = wrapper.get('input[type="file"]')
  Object.defineProperty(input.element, 'files', { configurable: true, value: [file] })
  await input.trigger('change')
  await flushPromises()
}

describe('ImportExport inspection binding', () => {
  it('invalidates old inspection immediately and discards reverse-order header checks', async () => {
    api.post.mockResolvedValue(inspection())
    const { wrapper } = await excelPage()
    await pickExcel(wrapper, new File(['a'], '清单.xlsx'))
    const second = deferred()
    const third = deferred()
    api.post.mockImplementation((url: string, _form: FormData, options: any) => {
      if (url === '/import/delivery-inspect') return options.params.header_row === 2 ? second.promise : third.promise
      throw new Error('Unexpected import')
    })
    await wrapper.get('input[type="number"]').setValue(2)
    expect(disabled(wrapper, '解析预览')).toBe(true)
    expect(disabled(wrapper, '确认导入')).toBe(true)
    expect(wrapper.find('.io-mapping-table').exists()).toBe(false)
    await button(wrapper, '确认导入').trigger('click')
    await wrapper.get('input[type="number"]').setValue(3)
    third.resolve(inspection('甲表', 3, '第三行'))
    await flushPromises()
    second.resolve(inspection('甲表', 2, '旧第二行'))
    await flushPromises()
    expect(wrapper.text()).toContain('第三行')
    expect(wrapper.text()).not.toContain('旧第二行')
    expect((wrapper.get('input[type="number"]').element as HTMLInputElement).value).toBe('3')
    expect(disabled(wrapper, '解析预览')).toBe(false)
    expect(api.post.mock.calls.some(([url]) => url === '/import/delivery-excel')).toBe(false)
  })

  it('binds to File identity and permits manual mappings with the checked sheet/header', async () => {
    const old = deferred()
    const current = deferred()
    const { wrapper } = await excelPage()
    api.post.mockImplementationOnce(() => old.promise).mockImplementationOnce(() => current.promise)
    const firstFile = new File(['old'], '同名.xlsx')
    const secondFile = new File(['new'], '同名.xlsx')
    await pickExcel(wrapper, firstFile)
    await pickExcel(wrapper, secondFile)
    current.resolve(inspection('乙表', 4, '当前列'))
    await flushPromises()
    old.resolve(inspection('甲表', 1, '旧文件列'))
    await flushPromises()
    expect(wrapper.text()).not.toContain('旧文件列')
    await wrapper.get('.io-mapping-table select').setValue('barcode')
    expect(disabled(wrapper, '解析预览')).toBe(false)
    api.post.mockResolvedValue({ rows: [], read_rows: 0, summary: { with_warnings: 0 } })
    await button(wrapper, '解析预览').trigger('click')
    await flushPromises()
    const call = api.post.mock.calls.find(([url]) => url === '/import/delivery-excel')!
    // FormData 可以复制 File 包装对象；比较内容，确保提交的不是同名旧文件。
    expect(await call[1].get('file').text()).toBe('new')
    expect(call[2].params).toMatchObject({ sheet: '乙表', header_row: 4, mapping: '{"0":"barcode"}', create: false })
  })

  it('checks sheet changes and rejects parse responses after manual mapping changes', async () => {
    api.post.mockResolvedValue(inspection())
    const { wrapper } = await excelPage()
    await pickExcel(wrapper, new File(['a'], '清单.xlsx'))
    const sheet = deferred()
    api.post.mockImplementationOnce(() => sheet.promise)
    await wrapper.get('.form-grid select').setValue('乙表')
    expect(disabled(wrapper, '确认导入')).toBe(true)
    sheet.resolve(inspection('乙表'))
    await flushPromises()
    const parse = deferred()
    api.post.mockImplementationOnce(() => parse.promise)
    await button(wrapper, '解析预览').trigger('click')
    await wrapper.get('.io-mapping-table select').setValue('barcode')
    parse.resolve({ rows: [{ seq: '1', title: '旧解析书', missing_required: [] }], read_rows: 1, summary: { with_warnings: 0 } })
    await flushPromises()
    expect(wrapper.text()).not.toContain('旧解析书')
    expect(disabled(wrapper, '解析预览')).toBe(false)
    expect((wrapper.get('.io-mapping-table select').element as HTMLSelectElement).value).toBe('barcode')
  })

  it('keeps imports disabled for invalid headers and failed current inspections', async () => {
    api.post.mockResolvedValue(inspection())
    const { wrapper } = await excelPage()
    await pickExcel(wrapper, new File(['a'], '清单.xlsx'))
    await wrapper.get('input[type="number"]').setValue(0)
    expect(disabled(wrapper, '确认导入')).toBe(true)
    expect(wrapper.text()).toContain('表头行必须是正整数')
    api.post.mockRejectedValueOnce(new Error('读取失败'))
    await wrapper.get('input[type="number"]').setValue(2)
    await flushPromises()
    expect(wrapper.text()).toContain('读取失败')
    expect(disabled(wrapper, '解析预览')).toBe(true)
  })
})

function candidate(record: number) {
  return { holding_id: record + 1000, record_id: record, library_id: 2, barcode: `L${record}`, title: `记录${record}`, copy_index: 1 }
}
function labelDefaults() {
  api.get.mockImplementation(async (url: string, params: any) => {
    if (url === '/settings/label_format') throw new Error('use default format')
    if (url === '/holdings/locations') return { locations: [] }
    const items = params.record_ids ? params.record_ids.split(',').map((id: string) => candidate(Number(id))) : []
    return { items, total: items.length, truncated: false }
  })
}

describe('Labels explicit record selection', () => {
  it('loads record IDs without the default seven-day or current-library restrictions and keeps filters within that scope', async () => {
    labelDefaults()
    const { wrapper } = await open(Labels, '?ids=10,20')
    const request = api.get.mock.calls.find(([url]) => url === '/labels/candidates')!
    expect(request[1]).toMatchObject({ record_ids: '10,20' })
    expect(request[1].created_from).toBeUndefined()
    expect(request[1].created_to).toBeUndefined()
    expect(request[1].library_id).toBeUndefined()
    expect(wrapper.findAll('.row-check[aria-label^="选择"]')).toHaveLength(2)
    await wrapper.get('[aria-label="关键词"]').setValue('记录10')
    await button(wrapper, '筛选').trigger('click')
    await flushPromises()
    expect(api.get.mock.calls.filter(([url]) => url === '/labels/candidates').at(-1)![1]).toMatchObject({ record_ids: '10,20', q: '记录10' })
    await button(wrapper, '重置').trigger('click')
    await flushPromises()
    const reset = api.get.mock.calls.filter(([url]) => url === '/labels/candidates').at(-1)![1]
    expect(reset.record_ids).toBe('10,20')
    expect(reset.created_from).toBeUndefined()
    expect(reset.q).toBeUndefined()
  })

  it('resets selections on query changes and drops the previous scope response', async () => {
    labelDefaults()
    const first = deferred()
    const getDefault = api.get.getMockImplementation()!
    api.get.mockImplementation((url: string, params: any) =>
      url === '/labels/candidates' && params.record_ids === '10' ? first.promise : getDefault(url, params))
    const { wrapper, router } = await open(Labels, '?ids=10')
    await router.push('/page?ids=20')
    await flushPromises()
    expect(wrapper.text()).toContain('记录20')
    first.resolve({ items: [candidate(10)], total: 1, truncated: false })
    await flushPromises()
    expect(wrapper.text()).not.toContain('记录10')
    expect(wrapper.get('[aria-label="选择 L20"]').attributes('type')).toBe('checkbox')
    // 重新挂载模拟浏览器刷新，仍从 route.query 保留范围。
    wrapper.unmount()
    const reloaded = await open(Labels, '?ids=20')
    expect(reloaded.wrapper.text()).toContain('记录20')
    expect(api.get.mock.calls.filter(([url]) => url === '/labels/candidates').at(-1)![1].record_ids).toBe('20')
  })

  it('fails closed for malformed explicit IDs and restores seven-day defaults when IDs are removed', async () => {
    labelDefaults()
    const { wrapper, router } = await open(Labels, '?ids=10,bad')
    expect(wrapper.text()).toContain('选书记录 ID 无效')
    expect(api.get.mock.calls.some(([url]) => url === '/labels/candidates')).toBe(false)
    await router.push('/page')
    await flushPromises()
    const params = api.get.mock.calls.filter(([url]) => url === '/labels/candidates').at(-1)![1]
    expect(params.record_ids).toBeUndefined()
    expect(params.library_id).toBe(1)
    expect(params.created_from).toMatch(/^\d{4}-\d{2}-\d{2}$/)
  })
})

const noBan = { banned: false, until: null, no_show_count: 0 }
function area(id: number) {
  return { id, name: `区域${id}`, rows: 1, cols: 1, open_time: '08:00', close_time: '20:00', enabled: true,
    slots: [{ start: '08:00', end: '12:00' }, { start: '12:00', end: '20:00' }] }
}
function seatMap(params: any, id = params.area_id * 10) {
  return { area: area(params.area_id), date: params.date, slot: { start: params.slot, end: '20:00' },
    seats: [{ id, code: `座位${id}`, row: 1, col: 1, enabled: true, booked: false, mine: false }],
    my_booking: null, ban: noBan }
}
function seatsDefaults() {
  api.readerGet.mockImplementation(async (url: string, params: any) => {
    if (url === '/portal/seats/areas') return { areas: [area(1), area(2)], ban: noBan }
    if (url === '/portal/seats/bookings') return { items: [], ban: noBan }
    return seatMap(params)
  })
  api.readerPost.mockResolvedValue({})
}

describe('Seats map context', () => {
  it('reloads an area even if its date and time slot are unchanged, clearing old map and seat immediately', async () => {
    seatsDefaults()
    const { wrapper } = await open(Seats)
    await wrapper.get('.seat-cell').trigger('click')
    expect(disabled(wrapper, '确认预约')).toBe(false)
    const pending = deferred()
    const getDefault = api.readerGet.getMockImplementation()!
    api.readerGet.mockImplementation((url: string, params: any) => url === '/portal/seats' ? pending.promise : getDefault(url, params))
    const previousSlot = (wrapper.get('[aria-label="预约时段"]').element as HTMLSelectElement).value
    await wrapper.get('[aria-label="座位区域"]').setValue('2')
    expect((wrapper.get('[aria-label="预约时段"]').element as HTMLSelectElement).value).toBe(previousSlot)
    expect(wrapper.find('.seat-cell').exists()).toBe(false)
    expect(wrapper.text()).toContain('尚未选择')
    expect(disabled(wrapper, '确认预约')).toBe(true)
    await button(wrapper, '确认预约').trigger('click')
    expect(api.readerPost).not.toHaveBeenCalled()
    const params = api.readerGet.mock.calls.filter(([url]) => url === '/portal/seats').at(-1)![1]
    expect(params.area_id).toBe(2)
    pending.resolve(seatMap(params))
    await flushPromises()
    expect(wrapper.get('.seat-cell').text()).toBe('座位20')
  })

  it('ignores reverse-order date and slot map responses and submits only the current map seat', async () => {
    seatsDefaults()
    const { wrapper } = await open(Seats)
    const requests: { pending: ReturnType<typeof deferred>; params: any }[] = []
    const getDefault = api.readerGet.getMockImplementation()!
    api.readerGet.mockImplementation((url: string, params: any) => {
      if (url !== '/portal/seats') return getDefault(url, params)
      const pending = deferred()
      requests.push({ pending, params })
      return pending.promise
    })
    await wrapper.findAll('.reader-date-chip')[1].trigger('click')
    const slot = (wrapper.get('[aria-label="预约时段"]').element as HTMLSelectElement).value === '08:00' ? '12:00' : '08:00'
    await wrapper.get('[aria-label="预约时段"]').setValue(slot)
    expect(requests).toHaveLength(2)
    requests[1].pending.resolve(seatMap(requests[1].params, 22))
    await flushPromises()
    requests[0].pending.resolve(seatMap(requests[0].params, 11))
    await flushPromises()
    expect(wrapper.get('.seat-cell').text()).toBe('座位22')
    await wrapper.get('.seat-cell').trigger('click')
    await button(wrapper, '确认预约').trigger('click')
    await nextTick()
    expect(api.readerPost).toHaveBeenCalledWith('/portal/seats/bookings', {
      seat_id: 22, booking_date: requests[1].params.date, start_time: requests[1].params.slot,
    })
  })

  it('rejects a map whose response context differs from the requested context', async () => {
    seatsDefaults()
    const getDefault = api.readerGet.getMockImplementation()!
    api.readerGet.mockImplementation((url: string, params: any) =>
      url === '/portal/seats' ? Promise.resolve(seatMap({ ...params, area_id: 2 })) : getDefault(url, params))
    const { wrapper } = await open(Seats)
    expect(wrapper.text()).toContain('座位图与当前预约条件不一致')
    expect(wrapper.find('.seat-cell').exists()).toBe(false)
    expect(disabled(wrapper, '确认预约')).toBe(true)
  })
})
