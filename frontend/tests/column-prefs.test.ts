import { beforeEach, describe, expect, it } from 'vitest'
import { useColumnPrefs } from '../src/legacy/composables/useColumnPrefs'

const COLS = [
  { key: 'title', label: '题名' },
  { key: 'isbn', label: 'ISBN' },
  { key: 'year', label: '出版年' },
]

describe('列显示偏好', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('默认全部显示', () => {
    const c = useColumnPrefs('t1', COLS)
    expect(c.hidden).toEqual([])
    expect(c.visibleCount).toBe(3)
    expect(c.isVisible('title')).toBe(true)
  })

  it('隐藏后立刻生效，并且下次读回来还是隐藏', () => {
    const c = useColumnPrefs('t1', COLS)
    c.toggle('isbn', false)
    expect(c.isVisible('isbn')).toBe(false)
    expect(c.visibleCount).toBe(2)
    // 换一个实例（模拟刷新页面）应读回同样的偏好——持久化是同步的，
    // 不依赖 watch 的 flush 时机
    const again = useColumnPrefs('t1', COLS)
    expect(again.hidden).toEqual(['isbn'])
  })

  it('最后一列不允许隐藏（否则只剩表头，看着像坏了）', () => {
    const c = useColumnPrefs('t1', COLS)
    c.toggle('title', false)
    c.toggle('isbn', false)
    expect(c.visibleCount).toBe(1)
    c.toggle('year', false) // 最后一个，应被挡掉
    expect(c.isVisible('year')).toBe(true)
    expect(c.visibleCount).toBe(1)
  })

  it('「全部显示」清空隐藏集合，并删掉本地键', () => {
    const c = useColumnPrefs('t1', COLS)
    c.toggle('title', false)
    expect(localStorage.getItem('jianji.column-prefs.t1')).toBeTruthy()
    c.showAll()
    expect(c.hidden).toEqual([])
    // 没有隐藏项时不留空数组——否则 localStorage 里攒一堆 "[]"
    expect(localStorage.getItem('jianji.column-prefs.t1')).toBeNull()
  })

  it('版本升级新增的列默认显示（存的是"隐藏了哪些"）', () => {
    const c = useColumnPrefs('t1', COLS)
    c.toggle('isbn', false)
    // 新版本表格多了「责任者」一列，老用户没设过它
    const upgraded = useColumnPrefs('t1', [...COLS, { key: 'author', label: '责任者' }])
    expect(upgraded.isVisible('author')).toBe(true)
    expect(upgraded.isVisible('isbn')).toBe(false)
  })

  it('表里已经不存在的列，偏好里会被丢掉', () => {
    localStorage.setItem('jianji.column-prefs.t1', JSON.stringify(['gone', 'isbn']))
    const c = useColumnPrefs('t1', COLS)
    expect(c.hidden).toEqual(['isbn'])
  })

  it('本地存了坏数据不影响页面', () => {
    localStorage.setItem('jianji.column-prefs.t1', '{不是 JSON')
    const c = useColumnPrefs('t1', COLS)
    expect(c.hidden).toEqual([])
    expect(c.visibleCount).toBe(3)
  })
})

describe('默认隐藏的列（页数 / 尺寸这类）', () => {
  const MIXED = [
    { key: 'title', label: '题名' },
    { key: 'isbn', label: 'ISBN' },
    { key: 'pages', label: '页数', defaultHidden: true },
    { key: 'size', label: '尺寸', defaultHidden: true },
  ]

  beforeEach(() => {
    localStorage.clear()
  })

  it('没有本地设置时，默认隐藏的列不显示', () => {
    const c = useColumnPrefs('t2', MIXED)
    expect(c.isVisible('title')).toBe(true)
    expect(c.isVisible('pages')).toBe(false)
    expect(c.visibleCount).toBe(2)
  })

  it('勾出来之后显示，且刷新还记得', () => {
    const c = useColumnPrefs('t2', MIXED)
    c.toggle('pages', true)
    expect(c.isVisible('pages')).toBe(true)
    expect(c.visibleCount).toBe(3)
    const again = useColumnPrefs('t2', MIXED)
    expect(again.isVisible('pages')).toBe(true)
    expect(again.isVisible('size')).toBe(false)
  })

  it('「全部显示」把默认隐藏的也打开', () => {
    const c = useColumnPrefs('t2', MIXED)
    c.showAll()
    expect(c.visibleCount).toBe(4)
    expect(c.isVisible('pages')).toBe(true)
  })

  it('「恢复默认」回到默认模板，而不是全部显示', () => {
    const c = useColumnPrefs('t2', MIXED)
    c.toggle('pages', true)
    c.toggle('title', false)
    c.reset()
    expect(c.isVisible('title')).toBe(true)
    expect(c.isVisible('pages')).toBe(false)
    expect(localStorage.getItem('jianji.column-prefs.t2')).toBeNull()
  })

  it('默认隐藏的列也受「至少留一列」保护', () => {
    const c = useColumnPrefs('t2', MIXED)
    c.showAll()
    c.toggle('isbn', false)
    c.toggle('pages', false)
    c.toggle('size', false)
    expect(c.visibleCount).toBe(1)
    c.toggle('title', false) // 最后一个，应被挡掉
    expect(c.isVisible('title')).toBe(true)
  })

  it('能读旧格式（裸数组 = 只记 hidden）', () => {
    localStorage.setItem('jianji.column-prefs.t2', JSON.stringify(['isbn']))
    const c = useColumnPrefs('t2', MIXED)
    expect(c.isVisible('isbn')).toBe(false)
    expect(c.isVisible('pages')).toBe(false)
  })
})
