/**
 * 手工条码输入的规范化与**位数校验**。
 *
 * 「全局设置 → 条码位数」**可以设多个**（旧库常并存 7 位历史号与 8 位新号），
 * 所以这里所有函数都接受 `number | number[]` 两种形态。
 *
 * 补零规则：数字部分补到「**不小于原宽度的最小允许位数**」。
 *   - 设置 [8]    输入 "123"       → "00000123"
 *   - 设置 [8]    输入 "00020022"  → "00020022"（已够宽，不变）
 *   - 设置 [7,8]  输入 "123"       → "0000123"（补到 7，不是 8 —— 取最近的允许位数）
 *   - 设置 [7,8]  输入 "12345678"  → "12345678"（已是 8 位，落在允许集内）
 *   - 设置 [7,8]  输入 "123456789" → 原样 9 位（比所有允许位数都宽，**不截断**，
 *                                    由 `barcodeDigitsAllowed()` 拦下）
 *   - 带前缀 "ABC123"              → "ABC00000123"（前缀原样保留，位数只看数字部分）
 */

/** 归一「允许位数」入参：单个数字或数组都可以，去重升序，丢弃非法值（<=0 / 非数字）。 */
export function digitList(digits: number | number[]): number[] {
  const arr = Array.isArray(digits) ? digits : [digits]
  const out = new Set<number>()
  for (const d of arr) {
    const n = Math.trunc(Number(d))
    if (Number.isFinite(n) && n > 0) out.add(n)
  }
  return [...out].sort((a, b) => a - b)
}

/**
 * 条码的「位数」= 末尾连续数字的长度（前缀不计，与补零口径一致）。
 * `ABC00000123` → 8；`0011971` → 7；不含数字 → 0。
 */
export function barcodeWidth(code: string): number {
  const m = (code ?? '').trim().match(/(\d+)$/)
  return m ? m[1].length : 0
}

/**
 * 条码位数是否落在允许的位数里。
 * 录入与改号前用它拦下，**后端有同一套硬校验**（前端只是把错误提前，不是权威）。
 */
export function barcodeDigitsAllowed(code: string, digits: number | number[]): boolean {
  return digitList(digits).includes(barcodeWidth(code))
}

/**
 * 把手工输入补零成完整条码。返回 null 表示不是「前缀 + 数字」形态，无法识别。
 */
export function normalizeBarcodeInput(raw: string, digits: number | number[]): string | null {
  const m = (raw ?? '').trim().match(/^(\D*?)(\d+)$/)
  if (!m) return null
  const list = digitList(digits)
  const n = m[2].length
  // 补到「不小于原宽度的最小允许位数」；允许位数都比原宽度小时不补（随后位数校验会拦下）
  const width = list.find(d => d >= n) ?? n
  return `${m[1]}${String(Number(m[2])).padStart(width, '0')}`
}
