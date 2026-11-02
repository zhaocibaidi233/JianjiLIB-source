import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { DOMWrapper, flushPromises, mount } from '@vue/test-utils'

// 套录源弹窗的两类回归：
//   1. 那两个**存了但从不被读**的字段（字符集 / 记录格式）必须从表单里消失——
//      留着就是"看起来能调、实际没作用"的假开关；
//   2. 新建时也能测连接，且是"先保存再测"（探活接口按 source_id），
//      测试失败要把刚建的源自动停用，别让它拖慢每次套录。
//
// ⚠️ BaseModal 用 `<Teleport to="body">`，所以弹窗内容不在 wrapper 里 ——
//    必须从 document.body 查，否则 find('#src-host') 永远拿不到。

const get = vi.fn()
const post = vi.fn()
const put = vi.fn()
const del = vi.fn()

vi.mock('../src/lib/api', () => ({
  get: (...a: unknown[]) => get(...a),
  post: (...a: unknown[]) => post(...a),
  put: (...a: unknown[]) => put(...a),
  del: (...a: unknown[]) => del(...a),
  errorMessage: (e: unknown) => (e instanceof Error ? e.message : String(e)),
}))

const toast = vi.fn()
const toastError = vi.fn()
vi.mock('../src/lib/toast', () => ({
  toast: (...a: unknown[]) => toast(...a),
  toastError: (...a: unknown[]) => toastError(...a),
}))

const Settings = (await import('../src/admin/views/Settings.vue')).default

let sourceRows: any[] = []
let wrapper: ReturnType<typeof mount> | null = null

/** 弹窗被 teleport 到 body，统一从 body 查 */
const page = () => new DOMWrapper(document.body)
const input = (selector: string) => page().find(selector)
function buttonByText(text: string) {
  const all = Array.from(document.body.querySelectorAll('button'))
    .map((el) => new DOMWrapper<HTMLButtonElement>(el))
  return all.find((b) => (b.text() || '').includes(text))
}

async function openSourceModal() {
  const nav = buttonByText('套录源')
  expect(nav, '设置页里没有「套录源」分区入口').toBeTruthy()
  await nav!.trigger('click')
  await flushPromises()

  const add = buttonByText('新增套录源')
  expect(add, '没有「新增套录源」按钮').toBeTruthy()
  await add!.trigger('click')
  await flushPromises()
}

beforeEach(() => {
  sourceRows = []
  get.mockReset()
  post.mockReset()
  put.mockReset()
  del.mockReset()
  toast.mockReset()
  toastError.mockReset()
  get.mockImplementation(async (url: string) => {
    if (url === '/sources') return sourceRows
    if (url === '/book-libraries') return [{ id: 1, name: '测试库', note: null, record_count: 0 }]
    return {}
  })
  // attachTo 是必须的：弹窗 Teleport 到 body，组件本身不挂进文档就什么都查不到
  wrapper = mount(Settings, {
    attachTo: document.body,
    global: { stubs: { RouterLink: true } },
  })
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  document.body.innerHTML = ''
})

describe('套录源弹窗', () => {
  it('exposes charset but not record syntax', async () => {
    await flushPromises()
    await openSourceModal()

    expect(page().text()).toContain('新增套录源')
    // 字符集**必须可设**：后端用它把检索词编成目标能识别的字节
    // （z39.50.py::_query_encoding）。选错 = 静默 0 命中，所以不能藏起来。
    // ⚠️ 这条断言曾反过来（当时 charset 存进 config 却从不被读）。
    //    修了读取侧之后，隐藏它就等于让所有源都钉死在 gbk。
    expect(input('#src-charset').exists()).toBe(true)
    // 记录格式仍然不让人选：按 MARC 头标区自动识别，那个 record_syntax
    // 字段从来没被读过，已从表单移除。
    expect(input('#src-syntax').exists()).toBe(false)
    // 真正生效的字段还在
    expect(input('#src-host').exists()).toBe(true)
    expect(input('#src-database').exists()).toBe(true)
    expect(input('#src-priority').exists()).toBe(true)
    expect(page().text()).toContain('记录格式按 MARC 头标区自动识别')
  })

  it('refuses to submit when the required fields are missing', async () => {
    await flushPromises()
    await openSourceModal()

    const testBtn = buttonByText('保存并测试')
    expect(testBtn, '弹窗里没有「保存并测试」').toBeTruthy()
    await testBtn!.trigger('click')
    await flushPromises()

    expect(toastError).toHaveBeenCalledWith('请填写套录源名称')
    expect(post).not.toHaveBeenCalled()
  })

  it('saves first, then probes — because the probe endpoint needs a source id', async () => {
    post.mockImplementation(async (url: string) => {
      if (url === '/sources') {
        sourceRows = [{ id: 7, name: '国图', type: 'z3950', enabled: true, config: {},
          last_ok_at: null, last_fail_at: null }]
        return { id: 7, name: '国图' }
      }
      if (url === '/sources/7/test') return { status: 'ok', message: '12 条命中' }
      return {}
    })

    await flushPromises()
    await openSourceModal()

    await input('#src-name').setValue('国图')
    await input('#src-host').setValue('z3950.nlc.cn')
    await input('#src-database').setValue('UNIMARC')
    await buttonByText('保存并测试')!.trigger('click')
    await flushPromises()

    expect(post.mock.calls.map((c) => c[0])).toEqual(['/sources', '/sources/7/test'])
    expect(page().text()).toContain('连接正常：12 条命中')
    expect(toast).toHaveBeenCalledWith('套录源连接正常')
  })

  it('disables a source that was just created and immediately failed to connect', async () => {
    post.mockImplementation(async (url: string) => {
      if (url === '/sources') {
        sourceRows = [{ id: 9, name: '坏源', type: 'z3950', enabled: true, config: {},
          last_ok_at: null, last_fail_at: null }]
        return { id: 9, name: '坏源' }
      }
      if (url === '/sources/9/test') return { status: 'fail', detail: '连接被拒绝' }
      return {}
    })
    put.mockResolvedValue({})

    await flushPromises()
    await openSourceModal()

    await input('#src-name').setValue('坏源')
    await input('#src-host').setValue('127.0.0.1')
    await input('#src-database').setValue('UNIMARC')
    await buttonByText('保存并测试')!.trigger('click')
    await flushPromises()

    // 连不上的新源必须落成停用：否则每次套录都要白等一个 30s 超时
    const putCall = put.mock.calls.find((c) => String(c[0]).startsWith('/sources/'))
    expect(putCall, '没有把失败的源停用').toBeTruthy()
    expect(putCall![1]).toMatchObject({ enabled: false })
    expect(page().text()).toContain('已自动停用')
  })
})
