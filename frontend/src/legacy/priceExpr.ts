/**
 * 价格栏的**简单计算器**：把「整套总价 ÷ 册数 = 单本价」这类算式直接在价格框里算出来。
 *
 * 抽成纯逻辑模块（不写进组件）的理由和其它 `*Route.ts` / `isbd.ts` 一样：求值逻辑一旦出错
 * 是**静默算错钱**——用户看到 010$d 里落了个错数还以为对了。必须能脱离组件单测。
 *
 * ⚠️ **安全红线**：求值**只用自写的 tokenizer + 递归下降**，**绝不**用
 * `eval` / `new Function` / `setTimeout('...')` 这类能把输入当代码执行的手段。
 * 输入来自输入框，任何"顺手 eval 一下"的写法都是注入面。
 *
 * 三个出口（外加一个给组件做守卫用的 `isPlainNumber`）：
 *   - `isPriceExpression(s)` —— 是不是"算式"（决定要不要触发计算 / 显示结果浮层）
 *   - `evalPriceExpression(s)` —— 求值（非法 / 除零 / 非有限 → `null`）
 *   - `formatPrice(n)` —— 金额四舍五入到 2 位小数
 */

/** 允许出现的字符白名单：数字、小数点、四则运算符、括号、空格。出现其它字符一律判非法。 */
const LEGAL_CHARS = /^[0-9.+\-*/() ]+$/

/** 输入长度上限：挡住超长串（既防滥用，也避免极端输入下的性能问题）。 */
const MAX_LEN = 64

/** 纯数字（可带正负号与小数）：`45` / `45.00` / `-3.5` / `.5`。 */
const PLAIN_NUMBER = /^[+-]?(\d+\.?\d*|\.\d+)$/

/**
 * 是否是"纯数字"（含空串）。**供组件做写回守卫**：只有纯数字/空串才允许同步进 `simple['010d']`，
 * 算式（如 `135/5`，用户还没按回车/失焦的那一刻）不能落进 MARC。
 *
 * 空串视为合法（`''` 是合法的空定价，不是算式）。
 */
export function isPlainNumber(s: string): boolean {
  const v = (s ?? '').trim()
  return v === '' || PLAIN_NUMBER.test(v)
}

/** 是否"算式"：含 `+ - * /` 之一、字符合法、且**不是纯数字**（纯数字原样保留，不重格式化）。 */
export function isPriceExpression(s: string): boolean {
  const v = (s ?? '').trim()
  if (!v || v.length > MAX_LEN) return false
  if (!LEGAL_CHARS.test(v)) return false
  if (!/[+\-*/]/.test(v)) return false
  // 纯数字（含正负号）不算算式：`45.00` 要原样保留，不能被当成"45"再格式化。
  if (isPlainNumber(v)) return false
  return true
}

type Token =
  | { kind: 'num'; value: number }
  | { kind: 'op'; value: '+' | '-' | '*' | '/' | '(' | ')' }

/**
 * 词法分析。非法字符、多个小数点、非有限数字 → `null`。
 * 只处理白名单内的字符（调用前已用 `LEGAL_CHARS` 过滤）。
 */
function tokenize(s: string): Token[] | null {
  const tokens: Token[] = []
  let i = 0
  while (i < s.length) {
    const c = s[i]
    if (c === ' ') { i += 1; continue }
    if (c === '+' || c === '-' || c === '*' || c === '/' || c === '(' || c === ')') {
      tokens.push({ kind: 'op', value: c })
      i += 1
      continue
    }
    if (c >= '0' && c <= '9' || c === '.') {
      let j = i
      let dots = 0
      while (j < s.length && ((s[j] >= '0' && s[j] <= '9') || s[j] === '.')) {
        if (s[j] === '.') dots += 1
        j += 1
      }
      const text = s.slice(i, j)
      if (dots > 1 || text === '.') return null      // `1.2.3` / 孤立小数点
      const value = Number(text)
      if (!Number.isFinite(value)) return null
      tokens.push({ kind: 'num', value })
      i = j
      continue
    }
    return null
  }
  return tokens
}

/**
 * 递归下降求值（含一元正负号、括号嵌套）。
 *
 * 文法：
 *   expr    := term (('+' | '-') term)*
 *   term    := factor (('*' | '/') factor)*
 *   factor  := ('+' | '-') factor | primary
 *   primary := number | '(' expr ')'
 *
 * 任何语法错误、除零、非有限结果 → 抛出（由 `evalPriceExpression` 统一兜成 `null`）。
 */
function evaluate(tokens: Token[]): number {
  let pos = 0
  const peek = (): Token | undefined => tokens[pos]
  const next = (): Token | undefined => tokens[pos++]

  function parseExpr(): number {
    let v = parseTerm()
    for (;;) {
      const t = peek()
      if (t && t.kind === 'op' && (t.value === '+' || t.value === '-')) {
        next()
        const rhs = parseTerm()
        v = t.value === '+' ? v + rhs : v - rhs
      } else {
        break
      }
    }
    return v
  }

  function parseTerm(): number {
    let v = parseFactor()
    for (;;) {
      const t = peek()
      if (t && t.kind === 'op' && (t.value === '*' || t.value === '/')) {
        next()
        const rhs = parseFactor()
        v = t.value === '*' ? v * rhs : v / rhs
      } else {
        break
      }
    }
    return v
  }

  function parseFactor(): number {
    const t = peek()
    if (t && t.kind === 'op' && (t.value === '+' || t.value === '-')) {
      next()
      const v = parseFactor()
      return t.value === '-' ? -v : v
    }
    return parsePrimary()
  }

  function parsePrimary(): number {
    const t = next()
    if (!t) throw new Error('unexpected end of expression')
    if (t.kind === 'num') return t.value
    if (t.value === '(') {
      const v = parseExpr()
      const close = next()
      if (!close || close.kind !== 'op' || close.value !== ')') {
        throw new Error('missing closing parenthesis')
      }
      return v
    }
    throw new Error('unexpected token')
  }

  const result = parseExpr()
  if (pos !== tokens.length) throw new Error('trailing tokens')   // 如 `1 2` / `(1)(2)`
  return result
}

/** 求值；非法 / 除零 / 非有限 → `null`。 */
export function evalPriceExpression(s: string): number | null {
  const v = (s ?? '').trim()
  if (!v || v.length > MAX_LEN) return null
  if (!LEGAL_CHARS.test(v)) return null
  const tokens = tokenize(v)
  if (!tokens || tokens.length === 0) return null
  try {
    const n = evaluate(tokens)
    return Number.isFinite(n) ? n : null      // NaN / ±Infinity（含除零）→ null
  } catch {
    return null
  }
}

/**
 * 金额格式：四舍五入保留 2 位小数，返回字符串（不含货币码）。
 *
 * 直接 `Math.round(n * 100)` 会被二进制浮点坑到：`27.005` 实际存成 `27.0049999…`，
 * `Math.round(2700.4999999…) = 2700` → 得到错误的 `27.00`。这里用一个**与数值同阶**的
 * 极小偏移（相对 epsilon）补偿表示误差，等价于十进制"四舍五入到分"，且不会在大数上误进位。
 */
export function formatPrice(n: number): string {
  if (!Number.isFinite(n)) return ''
  const scaled = n * 100
  const eps = Math.abs(scaled) * 1e-9
  const rounded = Math.round(scaled + (scaled >= 0 ? eps : -eps))
  return (rounded / 100).toFixed(2)
}
