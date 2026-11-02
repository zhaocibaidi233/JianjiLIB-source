import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import MrkPreview from '../src/legacy/components/MrkPreview.vue'

/**
 * 注意用 `element.textContent` 而不是 test-utils 的 `.text()`：
 * `.text()` 会把首尾空白 trim 掉，而这里要验的正是"空格一个不多一个不少"。
 */
function rowTexts(wrapper: ReturnType<typeof mount>): string[] {
  return wrapper.findAll('.mrk-row').map((r) => (r.element as HTMLElement).textContent ?? '')
}

/**
 * MARC 预览改成结构化着色后，**文字内容与排版必须逐字不变**——
 * 这个面板经常被整段复制到邮件/工单里，多一个空格都算改数据。
 *
 * 所以这里直接拿"改造前那段纯文本"当基准来比：
 *   =tag + 两个空格 + 指示符 + $a值$b值…   （控制字段没有指示符）
 */

const FIELDS = [
  { tag: '001', data: '0120200000493' },
  { tag: '005', data: '20201104230634.0' },
  { tag: '010', i1: ' ', i2: ' ', sf: [['a', '9787570723135'], ['b', ''], ['c', 'CNY18.00']] },
  { tag: '011', i1: ' ', i2: ' ', sf: [['a', '7-80723-115-0']] },
  // 指示符只给一个：另一个必须补空格，否则列会塌陷
  { tag: '100', i1: '0', i2: undefined, sf: [['a', '20201104d2020    em y0chiy0110    ea']] },
  { tag: '200', i1: '1', i2: ' ', sf: [['a', '体育与健康'], ['f', '毛振明主编']] },
  // 没有任何子字段的数据字段：`$` 部分为空，但指示符位仍在
  { tag: '905', i1: ' ', i2: ' ', sf: [] },
]

/** 改造前的实现，逐字抄在这里当基准（不要跟着新实现改）。 */
function legacyText(fields: typeof FIELDS): string {
  return fields.map(f => {
    if (f.sf || f.i1 !== undefined) {
      const sf = (f.sf ?? []).map((s: string[]) => `$${s[0]}${s[1]}`).join('')
      return `=${f.tag}  ${f.i1 ?? ' '}${f.i2 ?? ' '}${sf}`
    }
    return `=${f.tag}  ${f.data ?? ''}`
  }).join('\n')
}

describe('MARC 预览', () => {
  it('每行文字与改造前逐字一致', () => {
    const wrapper = mount(MrkPreview, { props: { fields: FIELDS as never } })
    const expected = legacyText(FIELDS).split('\n')
    const got = rowTexts(wrapper)
    expect(got.length).toBe(expected.length)
    got.forEach((text, i) => {
      expect(text).toBe(expected[i])
    })
  })

  it('整体拼回去（行间补换行）也一致', () => {
    const wrapper = mount(MrkPreview, { props: { fields: FIELDS as never } })
    expect(rowTexts(wrapper).join('\n')).toBe(legacyText(FIELDS))
  })

  it('四个色块各自落在该在的位置', () => {
    const wrapper = mount(MrkPreview, { props: { fields: FIELDS as never } })
    // 第 3 行（010）是数据字段：字段号 / 指示符 / 子字段代码都要有
    const row = wrapper.findAll('.mrk-row')[2]
    expect(row.find('.mrk-tag').text()).toBe('=010')
    expect((row.find('.mrk-ind').element as HTMLElement).textContent).toBe('  ')
    const codes = row.findAll('.mrk-code').map(c => c.text())
    expect(codes).toEqual(['$a', '$b', '$c'])
    // 控制字段没有指示符，也不该渲染子字段代码
    const ctrl = wrapper.findAll('.mrk-row')[0]
    expect(ctrl.find('.mrk-ind').exists()).toBe(false)
    expect(ctrl.find('.mrk-data').text()).toBe('0120200000493')
    expect(ctrl.find('.mrk-code').exists()).toBe(false)
  })

  it('空字段列表不报错', () => {
    const wrapper = mount(MrkPreview, { props: { fields: [] } })
    expect(wrapper.findAll('.mrk-row').length).toBe(0)
  })

  it('子字段值里的空格原样保留', () => {
    const wrapper = mount(MrkPreview, {
      props: { fields: [{ tag: '200', i1: '1', i2: ' ', sf: [['a', ' 前后都有空格 ']] }] as never },
    })
    const expected = legacyText([
      { tag: '200', i1: '1', i2: ' ', sf: [['a', ' 前后都有空格 ']] },
    ] as never)
    expect(rowTexts(wrapper)[0]).toBe(expected)
  })
})
