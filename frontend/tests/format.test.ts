import { describe, expect, it } from 'vitest'
import {
  AVATAR_CLASSES,
  addDaysIso,
  avatarClass,
  daysBetween,
  formatDate,
  formatDateTime,
  formatIsbn,
  formatMoney,
  formatMoneyIn,
  formatMonthDay,
  formatNumber,
  initials,
  todayIso,
} from '../src/lib/format'

// 这些函数是全站列表、详情、报表的公共出口：一个格式化错了，几十个页面同时错，
// 而且**不会报错**（显示成 0 或空串）。所以哪怕逻辑简单也值得钉住。

describe('formatNumber / formatMoney', () => {
  it('renders null and undefined as zero instead of "NaN"', () => {
    expect(formatNumber(null)).toBe('0')
    expect(formatNumber(undefined)).toBe('0')
    expect(formatMoney(null)).toMatch(/^¥0\.00$/)
  })

  it('groups thousands', () => {
    expect(formatNumber(1234567)).toBe('1,234,567')
    expect(formatMoney(1234.5)).toBe('¥1,234.50')
  })

  it('formats money in an explicit currency and falls back for unknown codes', () => {
    expect(formatMoneyIn('CNY', 1234.5)).toBe('¥1,234.50')
    // zh-CN 的 USD 符号是「US$」，不是「$」—— 断言按真实输出写，不按直觉写
    expect(formatMoneyIn('USD', 10)).toBe('US$10.00')
    // 空币种按人民币；非法代码不抛异常、不吐 NaN、也不丢掉代码
    expect(formatMoneyIn(null, 8)).toBe('¥8.00')
    expect(formatMoneyIn('NOT-A-CODE', 8)).toBe('NOT-A-CODE 8.00')
    expect(formatMoneyIn('USD', null)).toBe('US$0.00')
  })
})

describe('formatDate / formatDateTime', () => {
  it('returns an empty string for missing values', () => {
    expect(formatDate(null)).toBe('')
    expect(formatDate('')).toBe('')
    expect(formatDateTime(undefined)).toBe('')
  })

  it('formats an ISO date as YYYY-MM-DD with zero padding', () => {
    expect(formatDate('2026-03-05')).toBe('2026-03-05')
    expect(formatDate('2026-03-05T10:00:00Z')).toMatch(/^2026-03-0[45]$/)
  })

  it('falls back to the raw string prefix when unparsable', () => {
    // 后端可能返回 `2026-13-45` 这类脏数据：不抛异常、也别显示 "Invalid Date"
    expect(formatDate('2026-13-45-xx')).toBe('2026-13-45')
  })

  it('keeps date and time separated by one space', () => {
    const out = formatDateTime('2026-03-05T08:07:00')
    expect(out).toBe('2026-03-05 08:07')
  })
})

describe('formatMonthDay', () => {
  it('drops the year', () => {
    expect(formatMonthDay('2026-03-05T00:00:00')).toBe('3月5日')
  })
})

describe('daysBetween', () => {
  it('counts whole days from today, negative for the past', () => {
    expect(daysBetween(todayIso())).toBe(0)
    expect(daysBetween(addDaysIso(7))).toBe(7)
    expect(daysBetween(addDaysIso(-3))).toBe(-3)
  })

  it('returns 0 for missing or unparsable input', () => {
    expect(daysBetween(null)).toBe(0)
    expect(daysBetween('not-a-date')).toBe(0)
  })
})

describe('formatIsbn', () => {
  it('splits Chinese ISBN-13 by the group-7 registrant ranges', () => {
    expect(formatIsbn('9787533183554')).toBe('978-7-5331-8355-4')
    expect(formatIsbn('9787020042494')).toBe('978-7-02-004249-4')
    expect(formatIsbn('9787208171336')).toBe('978-7-208-17133-6')
  })

  it('handles Chinese ISBN-10 and already-hyphenated input', () => {
    expect(formatIsbn('7020042494')).toBe('7-02-004249-4')
    expect(formatIsbn('978-7-5331-8355-4')).toBe('978-7-5331-8355-4')
    expect(formatIsbn('702004249X')).toBe('7-02-004249-X')
  })

  it('leaves other registration groups and junk untouched', () => {
    expect(formatIsbn('9780306406157')).toBe('9780306406157')
    expect(formatIsbn('not-an-isbn')).toBe('not-an-isbn')
    expect(formatIsbn('')).toBe('')
    expect(formatIsbn(null)).toBe('')
  })
})

describe('initials / avatarClass', () => {
  it('keeps the last two characters of a long name', () => {
    expect(initials('欧阳修文')).toBe('修文')
    expect(initials('张三')).toBe('张三')
  })

  it('falls back to a placeholder for empty names', () => {
    expect(initials('')).toBe('读')
    expect(initials(null)).toBe('读')
  })

  it('is stable for the same seed and always inside the palette', () => {
    expect(avatarClass(42)).toBe(avatarClass('42'))
    for (const seed of [1, 'a', '图书馆', 99999]) {
      expect(AVATAR_CLASSES).toContain(avatarClass(seed))
    }
  })
})
