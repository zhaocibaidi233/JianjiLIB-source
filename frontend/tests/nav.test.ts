import { describe, expect, it } from 'vitest'
import { NAV_GROUPS, NAV_ITEMS, navForPath } from '../src/lib/nav'

describe('navigation map', () => {
  it('resolves the dashboard only for the exact /admin path', () => {
    expect(navForPath('/admin')).toMatchObject({ to: '/admin', label: '工作台' })
    expect(navForPath('/admin/circulation')).toMatchObject({ to: '/admin/circulation' })
    expect(navForPath('/admin/records/42')).toMatchObject({ to: '/admin/records' })
  })

  it('keeps destinations unique and every item addressable', () => {
    const paths = NAV_ITEMS.map((item) => item.to)
    expect(new Set(paths).size).toBe(paths.length)
    for (const item of NAV_ITEMS) {
      expect(item.to.startsWith('/admin')).toBe(true)
      expect(item.label.length).toBeGreaterThan(0)
      expect(item.icon.length).toBeGreaterThan(0)
    }
  })

  it('guards every module page while leaving the dashboard open', () => {
    for (const item of NAV_ITEMS) {
      if (item.to === '/admin') expect(item.module).toBeUndefined()
      else expect(item.module, item.to).toBeTruthy()
    }
  })

  it('does not leave an empty navigation group', () => {
    for (const group of NAV_GROUPS) {
      expect(group.items.length, group.label).toBeGreaterThan(0)
    }
  })
})
