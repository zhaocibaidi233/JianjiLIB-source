import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { effectScope, reactive, ref } from 'vue'
import type { MarcField } from '../src/legacy/api'
import { usePanelSync } from '../src/legacy/composables/usePanelSync'
import { planClassNoEnter } from '../src/legacy/volume'

const get = vi.hoisted(() => vi.fn())
vi.mock('../src/legacy/api', () => ({ http: { get } }))
const { useBookNoSync } = await import('../src/legacy/composables/useBookNoSync')

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>(r => { resolve = r })
  return { promise, resolve }
}
const scopes: ReturnType<typeof effectScope>[] = []
function inScope<T>(fn: () => T): T {
  const scope = effectScope()
  scopes.push(scope)
  return scope.run(fn)!
}
function panel(price = 'CNY39.80') {
  return inScope(() => {
    const simple = reactive<Record<string, string>>({})
    const fields = ref<MarcField[]>([
      { tag: '010', i1: ' ', i2: ' ', sf: [['a', '9780000000000'], ['d', price], ['z', '保留']] },
      { tag: '200', i1: '1', i2: ' ', sf: [['a', '书名']] },
      { tag: '690', i1: ' ', i2: ' ', sf: [['a', 'I247'], ['v', '5']] },
      { tag: '690', i1: ' ', i2: ' ', sf: [['a', '第二分类']] },
    ])
    const priceDraft = ref('')
    const tab = ref('full')
    const currency = ref('CNY')
    const api = usePanelSync({ simple, fields, priceDraft, tab, currency })
    api.populateSimple(fields.value)
    return { simple, fields, priceDraft, tab, currency, ...api }
  })
}
const value = (fs: MarcField[], tag: string, code: string) =>
  fs.find(f => f.tag === tag)?.sf?.find(s => s[0] === code)?.[1]

beforeEach(() => { get.mockReset() })
afterEach(() => { scopes.splice(0).forEach(s => s.stop()); vi.useRealTimers() })

describe('全字段与侧栏保存基线', () => {
  it('原样保存 MarcPane 改动，不叠加旧分类号或定价，保留指示符和顺序', () => {
    const p = panel()
    p.fields.value[0]!.sf![1]![1] = 'USD12.00'
    p.fields.value[2]!.sf![0]![1] = 'G624'
    p.fields.value[2]!.i1 = '1'
    p.fields.value.reverse()
    expect(p.currentPayloadFields()).toEqual(p.fields.value)
  })
  it('全字段删子字段或整字段，不被侧栏旧值补回', () => {
    const p = panel()
    p.fields.value[0]!.sf!.splice(1, 1)
    p.fields.value = p.fields.value.filter(f => f.tag !== '690')
    expect(value(p.currentPayloadFields(), '010', 'd')).toBeUndefined()
    expect(p.currentPayloadFields().some(f => f.tag === '690')).toBe(false)
  })
  it('侧栏真正修改生效，只动目标第一实例，保留额外子字段', () => {
    const p = panel()
    p.simple['690a'] = 'G624'
    p.priceDraft.value = '27.00'
    const out = p.currentPayloadFields()
    expect(value(out, '690', 'a')).toBe('G624')
    expect(value(out, '010', 'd')).toBe('CNY27.00')
    expect(value(out, '010', 'z')).toBe('保留')
    expect(out.filter(f => f.tag === '690')[1]!.sf).toEqual([['a', '第二分类']])
  })
  it('侧栏清空可删除目标子字段，不删除其他内容', () => {
    const p = panel()
    p.simple['690a'] = ''
    p.priceDraft.value = ''
    const out = p.currentPayloadFields()
    expect(value(out, '010', 'd')).toBeUndefined()
    expect(value(out, '690', 'a')).toBeUndefined()
    expect(value(out, '690', 'v')).toBe('5')
  })
  it('改后恢复基线不覆盖全字段的新值', () => {
    const p = panel()
    p.simple['690a'] = 'G624'
    p.simple['690a'] = 'I247'
    p.priceDraft.value = '50'
    p.priceDraft.value = '39.80'
    p.fields.value[0]!.sf![1]![1] = 'CNY20'
    p.fields.value[2]!.sf![0]![1] = 'G1'
    expect(p.currentPayloadFields()).toEqual(p.fields.value)
  })
  it('未提交算式不落库，未改外币不变成本馆币种', () => {
    const p = panel('USD12.00')
    expect(value(p.currentPayloadFields(), '010', 'd')).toBe('USD12.00')
    p.priceDraft.value = '135/5'
    expect(value(p.currentPayloadFields(), '010', 'd')).toBe('USD12.00')
    p.tab.value = 'simple'
    p.simple['010d'] = '135/5'
    expect(value(p.currentPayloadFields(), '010', 'd')).toBe('USD12.00')
  })
  it('逐键输入算式时数字前缀也不作为最终价格保存，切换 tab 保留草稿', () => {
    const p = panel()
    p.priceDraft.value = '135'
    p.priceDraft.value = '135/5'
    expect(value(p.currentPayloadFields(), '010', 'd')).toBe('CNY39.80')
    p.tab.value = 'simple'
    expect(p.priceDraft.value).toBe('135/5')
    expect(value(p.currentPayloadFields(), '010', 'd')).toBe('CNY39.80')
    p.tab.value = 'full'
    expect(p.priceDraft.value).toBe('135/5')
    expect(value(p.currentPayloadFields(), '010', 'd')).toBe('CNY39.80')
    p.priceDraft.value = '27.00'
    expect(value(p.currentPayloadFields(), '010', 'd')).toBe('CNY27.00')
  })
  it('切换简编前合并真正侧栏修改，切回全字段建立新基线', () => {
    const p = panel()
    p.simple['690a'] = 'G624'
    p.tab.value = 'simple'
    expect(p.simple['690a']).toBe('G624')
    p.tab.value = 'full'
    p.fields.value.find(f => f.tag === '690')!.sf![0]![1] = 'G1'
    expect(value(p.currentPayloadFields(), '690', 'a')).toBe('G1')
  })
  it('新加载投影清除上一稿侧栏修改，载荷出口无副作用', () => {
    const p = panel()
    p.simple['690a'] = 'G624'
    p.populateSimple([{ tag: '690', sf: [['a', 'K1']] }])
    p.fields.value = [{ tag: '690', sf: [['a', 'K2']] }]
    const before = JSON.stringify({ simple: p.simple, draft: p.priceDraft.value })
    expect(value(p.currentPayloadFields(), '690', 'a')).toBe('K2')
    expect(JSON.stringify({ simple: p.simple, draft: p.priceDraft.value })).toBe(before)
  })
})

function book() {
  return inScope(() => {
    const classNo = ref('I247')
    const currentLibId = ref<number | undefined>(7)
    const identity = ref('new')
    const bookNo = ref('')
    const bookNoTouched = ref(false)
    const loadedClassNo = ref('')
    const api = useBookNoSync({ classNo: () => classNo.value, currentLibId,
      recordIdentity: () => identity.value, bookNo: () => bookNo.value,
      setBookNo: v => { bookNo.value = v }, bookNoTouched, loadedClassNo: () => loadedClassNo.value })
    return { ...api, classNo, currentLibId, identity, bookNo, bookNoTouched, loadedClassNo }
  })
}

describe('书次号在途请求失效', () => {
  it('使用注入的记录库，不读取旧 current_lib', async () => {
    localStorage.setItem('current_lib', '999')
    get.mockResolvedValue({ data: { call_no: 'I247/12:上册' } })
    const b = book()
    await b.syncBookNo()
    expect(get).toHaveBeenCalledWith('/cataloging/callno-preview', { params: { class_no: 'I247', library_id: 7 } })
    expect(b.bookNo.value).toBe('12')
  })
  it.each(['classNo', 'currentLibId', 'identity'] as const)('%s 变化即刻失效，去抖期间旧响应不写回', async key => {
    vi.useFakeTimers()
    const d = deferred<any>()
    get.mockReturnValue(d.promise)
    const b = book()
    const request = b.syncBookNo()
    if (key === 'currentLibId') b.currentLibId.value = 8
    else b[key].value = key === 'classNo' ? 'I247.5' : 'temp'
    d.resolve({ data: { call_no: 'I247/12' } })
    await request
    expect(b.bookNo.value).toBe('')
    expect(get).toHaveBeenCalledTimes(1)
  })
  it('手工输入和 touched 阻止旧响应，第二次手工输入仍失效', async () => {
    const d = deferred<any>()
    get.mockReturnValue(d.promise)
    const b = book()
    const request = b.syncBookNo()
    b.bookNo.value = '88'
    b.bookNoTouched.value = true
    b.bookNo.value = '89'
    d.resolve({ data: { call_no: 'I247/12' } })
    await request
    expect(b.bookNo.value).toBe('89')
  })
  it('手工改值即使未置 touched 也使旧响应失效', async () => {
    const d = deferred<any>()
    get.mockReturnValue(d.promise)
    const b = book()
    const request = b.syncBookNo()
    b.bookNo.value = '88'
    d.resolve({ data: { call_no: 'I247/12' } })
    await request
    expect(b.bookNo.value).toBe('88')
  })
  it('dispose 同时取消去抖、在途请求和后续主动请求', async () => {
    vi.useFakeTimers()
    const d = deferred<any>()
    get.mockReturnValue(d.promise)
    const b = book()
    const request = b.syncBookNo()
    b.classNo.value = 'G624'
    b.dispose()
    d.resolve({ data: { call_no: 'I247/12' } })
    await request
    await vi.advanceTimersByTimeAsync(400)
    await b.syncBookNo()
    expect(b.bookNo.value).toBe('')
    expect(get).toHaveBeenCalledTimes(1)
  })
  it('载入分类号相同保留已有书次号，不发预览请求', async () => {
    const b = book()
    b.bookNo.value = '77'
    b.loadedClassNo.value = 'I247'
    await b.syncBookNo()
    expect(get).not.toHaveBeenCalled()
    expect(b.bookNo.value).toBe('77')
  })

  it('force 预览忽略 touched 与载入分类号，返回并写回新号', async () => {
    get.mockResolvedValue({ data: { call_no: 'I247/16' } })
    const b = book()
    b.bookNo.value = '77'
    b.bookNoTouched.value = true
    b.loadedClassNo.value = 'I247'
    const result = await b.syncBookNo({ force: true })
    expect(result).toBe('16')
    expect(b.bookNo.value).toBe('16')
    expect(get).toHaveBeenCalledTimes(1)
  })

  it('force 预览仍受最新输入守卫：请求期间手改不覆盖', async () => {
    const d = deferred<any>()
    get.mockReturnValue(d.promise)
    const b = book()
    b.bookNo.value = '77'
    b.bookNoTouched.value = true
    const request = b.syncBookNo({ force: true })
    b.bookNo.value = '99'
    d.resolve({ data: { call_no: 'I247/16' } })
    await request
    expect(b.bookNo.value).toBe('99')
  })
})

describe('分类号回车判定', () => {
  it('分类号统一大写后返回', () => {
    expect(planClassNoEnter({
      input: ' i242.1 ', currentBookNo: '', loadedClassNo: 'I242.1',
      loadedBookNo: '',
    })).toEqual({ classNo: 'I242.1', action: 'preview' })
  })

  it('分类号没变且有书次号：保留', () => {
    expect(planClassNoEnter({
      input: 'I247', currentBookNo: '15', loadedClassNo: 'I247',
      loadedBookNo: '15',
    })).toEqual({ classNo: 'I247', action: 'keep' })
  })

  it('分类号没变、书次号被删：恢复进入编辑前的原号', () => {
    expect(planClassNoEnter({
      input: 'I247', currentBookNo: '', loadedClassNo: 'I247',
      loadedBookNo: '15',
    })).toEqual({ classNo: 'I247', action: 'restore', bookNo: '15' })
  })

  it('分类号没变、没有原号：按规则预览', () => {
    expect(planClassNoEnter({
      input: 'I247', currentBookNo: '', loadedClassNo: 'I247',
      loadedBookNo: '',
    })).toEqual({ classNo: 'I247', action: 'preview' })
  })

  it('分类号改过：忽略原号，按新分类号预览', () => {
    expect(planClassNoEnter({
      input: 'g624', currentBookNo: '15', loadedClassNo: 'I247',
      loadedBookNo: '15',
    })).toEqual({ classNo: 'G624', action: 'preview' })
  })

  it('空分类号或非字母开头：清空书次号', () => {
    expect(planClassNoEnter({
      input: '  ', currentBookNo: '15', loadedClassNo: 'I247',
      loadedBookNo: '15',
    })).toEqual({ classNo: '', action: 'clear' })
    expect(planClassNoEnter({
      input: '1A', currentBookNo: '15', loadedClassNo: 'I247',
      loadedBookNo: '15',
    })).toEqual({ classNo: '1A', action: 'clear' })
  })
})
