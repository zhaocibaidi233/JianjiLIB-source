const nf = new Intl.NumberFormat('zh-CN')
const money = new Intl.NumberFormat('zh-CN', {
  style: 'currency',
  currency: 'CNY',
  minimumFractionDigits: 2,
})

export function formatNumber(value: number | null | undefined): string {
  return nf.format(Number(value || 0))
}

export function formatMoney(value: number | null | undefined): string {
  return money.format(Number(value || 0))
}

/**
 * 指定币种的金额（财产登记、统计分析会混入 USD 等外币）。
 *
 * 为什么单独一个函数：这两个页面原来各自 `new Intl.NumberFormat(...)` 再包
 * try/catch 兜底 —— 同一套逻辑抄两份，币种代码非法时的兜底格式还各写各的。
 * 这里收成一处：合法币种走 Intl（¥1,234.00 / US$1,234.00），非法或空币种
 * 退回「代码 + 两位小数」，不抛异常、不出现 NaN、也不把内部代码吞掉。
 */
export function formatMoneyIn(
  currency: string | null | undefined,
  value: number | null | undefined,
): string {
  const code = (currency ?? '').trim().toUpperCase() || 'CNY'
  const amount = Number(value || 0)
  try {
    return new Intl.NumberFormat('zh-CN', {
      style: 'currency',
      currency: code,
      minimumFractionDigits: 2,
    }).format(amount)
  } catch {
    return `${code} ${amount.toFixed(2)}`
  }
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value.slice(0, 10)
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return `${formatDate(value)} ${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`
}

export function formatMonthDay(value: string | null | undefined): string {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return `${date.getMonth() + 1}月${date.getDate()}日`
}

export function daysBetween(value: string | null | undefined): number {
  if (!value) return 0
  const target = new Date(value)
  if (Number.isNaN(target.getTime())) return 0
  const today = new Date()
  const start = new Date(today.getFullYear(), today.getMonth(), today.getDate())
  const end = new Date(target.getFullYear(), target.getMonth(), target.getDate())
  return Math.round((end.getTime() - start.getTime()) / 86_400_000)
}

export function initials(name: string | null | undefined): string {
  const text = (name || '').trim()
  return text.length > 2 ? text.slice(-2) : text || '读'
}

/*
 * ISBN 中国组（7）的出版者号长度规则，取自 ISBN 国际 RangeMessage.xml：
 * 组区号后取 7 位数字落区间，区间决定出版者号位数。只格式化 978-7 / 7 开头的
 * 书号；其它组区（978-0、979-…）没有内置范围表，原样返回，避免切错。
 */
const GROUP7_RULES: ReadonlyArray<readonly [number, number, number]> = [
  [0, 999_999, 2],
  [1_000_000, 4_999_999, 3],
  [5_000_000, 7_999_999, 4],
  [8_000_000, 8_999_999, 5],
  [9_000_000, 9_999_999, 6],
]

export function formatIsbn(value: string | null | undefined): string {
  const raw = (value || '').trim()
  if (!raw) return ''
  const digits = raw.replace(/[^0-9Xx]/g, '').toUpperCase()
  let rest: string
  let head: string
  if (digits.length === 13 && digits.startsWith('9787')) {
    rest = digits.slice(4, 12)
    head = '978-7-'
  } else if (digits.length === 10 && digits.startsWith('7')) {
    rest = digits.slice(1, 9)
    head = '7-'
  } else {
    return raw
  }
  const key = Number(rest.slice(0, 7))
  const rule = GROUP7_RULES.find(([lo, hi]) => key >= lo && key <= hi)
  if (!rule) return raw
  const registrant = rest.slice(0, rule[2])
  const publication = rest.slice(rule[2])
  return `${head}${registrant}-${publication}-${digits[digits.length - 1]}`
}

export function todayIso(): string {
  return formatDate(new Date().toISOString())
}

export function addDaysIso(days: number): string {
  const date = new Date()
  date.setDate(date.getDate() + days)
  return formatDate(date.toISOString())
}

export const AVATAR_CLASSES = ['av-1', 'av-2', 'av-3', 'av-4', 'av-5', 'av-6']

export function avatarClass(seed: string | number): string {
  const text = String(seed)
  let hash = 0
  for (let i = 0; i < text.length; i += 1) hash = (hash * 31 + text.charCodeAt(i)) >>> 0
  return AVATAR_CLASSES[hash % AVATAR_CLASSES.length]
}
