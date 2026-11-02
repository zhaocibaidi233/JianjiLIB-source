/**
 * 列显示偏好：哪些列要显示、哪些收起来，按表记忆在浏览器本地。
 *
 * 设计要点（都是踩过的坑）：
 *
 * 1. **每列各有默认值，改动只记"偏离默认"的部分**。
 *    默认显示的列（题名、条码…）记在 `hidden` 里；默认隐藏的列（页数、尺寸…）
 *    记在 `shown` 里。以后版本给表格加新列，只要声明自己的默认值，老用户的
 *    偏好自然生效——存"当前可见列快照"的话，新列会被静默藏掉。
 *
 * 2. **localStorage 读写一律 try/catch**。无痕窗口、被策略禁用的浏览器里
 *    `localStorage` 直接抛异常；一个列偏好不值得让整页白屏。
 *
 * 3. **至少留一列**。全勾掉会得到一张只剩表头的空表，看着像坏了。最后一个
 *    可见列不允许取消，`toggle` 里挡掉。
 *
 * 4. **每次改动同步落盘**，不靠 `watch`：watch 默认在下一渲染前才 flush，
 *    点完复选框立刻关标签页就丢了。
 *
 * 5. **键带表名**。同一个浏览器里好几个表格，各记各的，互不干扰。
 */
import { computed, reactive, ref } from 'vue'

export interface ColumnDef {
  /** 稳定的列标识（不要用表头文案，改文案会让老偏好失效） */
  key: string
  label: string
  /** 默认不显示（用户可以在「列」里勾出来）。缺省 = 默认显示 */
  defaultHidden?: boolean
}

const PREFIX = 'jianji.column-prefs.'

interface Prefs {
  /** 默认显示的列里，被用户关掉的 */
  hidden: string[]
  /** 默认隐藏的列里，被用户打开的 */
  shown: string[]
}

function read(key: string): Prefs {
  try {
    const raw = localStorage.getItem(PREFIX + key)
    if (!raw) return { hidden: [], shown: [] }
    const parsed = JSON.parse(raw)
    // 旧格式是裸数组（只记 hidden），升级时按 hidden 读，shown 视为空
    if (Array.isArray(parsed)) {
      return { hidden: parsed.filter((x) => typeof x === 'string'), shown: [] }
    }
    const clean = (v: unknown): string[] =>
      Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : []
    return { hidden: clean(parsed?.hidden), shown: clean(parsed?.shown) }
  } catch {
    return { hidden: [], shown: [] }
  }
}

function write(key: string, prefs: Prefs): void {
  try {
    if (prefs.hidden.length || prefs.shown.length) {
      localStorage.setItem(PREFIX + key, JSON.stringify(prefs))
    } else {
      localStorage.removeItem(PREFIX + key)
    }
  } catch {
    /* 存不了就算了，本次会话内仍然有效 */
  }
}

export function useColumnPrefs(storageKey: string, columns: ColumnDef[]) {
  const allKeys = columns.map((c) => c.key)
  const byKey = new Map(columns.map((c) => [c.key, c]))
  const defaultsHidden = columns.filter((c) => c.defaultHidden).map((c) => c.key)

  const stored = read(storageKey)
  // 只保留仍然存在的列：删掉的列留在偏好里没有意义，还会越攒越多
  const hidden = ref<string[]>(stored.hidden.filter((k) => allKeys.includes(k)))
  const shown = ref<string[]>(stored.shown.filter((k) => allKeys.includes(k)))

  function persist(): void {
    write(storageKey, { hidden: hidden.value, shown: shown.value })
  }

  const isVisible = (key: string): boolean => {
    const def = byKey.get(key)
    if (!def) return false
    return def.defaultHidden ? shown.value.includes(key) : !hidden.value.includes(key)
  }

  const visibleCount = computed(() => allKeys.filter((k) => isVisible(k)).length)

  function toggle(key: string, on?: boolean): void {
    const def = byKey.get(key)
    if (!def) return
    const want = on ?? !isVisible(key)
    // 最后一个可见列不许取消：否则表格只剩表头，看起来像坏了
    if (!want && visibleCount.value <= 1) return
    if (def.defaultHidden) {
      const has = shown.value.includes(key)
      if (want && !has) shown.value = [...shown.value, key]
      if (!want && has) shown.value = shown.value.filter((k) => k !== key)
    } else {
      const has = hidden.value.includes(key)
      if (!want && !has) hidden.value = [...hidden.value, key]
      if (want && has) hidden.value = hidden.value.filter((k) => k !== key)
    }
    persist()
  }

  /** 全部显示（包括默认隐藏的列） */
  function showAll(): void {
    hidden.value = []
    shown.value = [...defaultsHidden]
    persist()
  }

  /** 回到默认模板：默认显示的就是显示，默认隐藏的就是隐藏 */
  function reset(): void {
    hidden.value = []
    shown.value = []
    persist()
  }

  /**
   * 返回 **reactive** 而不是普通对象。
   *
   * 模板只自动解包"顶层" ref：普通对象里裹着的 ref（`cols.hidden`）在模板里
   * 拿到的仍是 Ref 对象本身，传给 `:hidden="cols.hidden"` 会报
   * `Ref<string[]> 不能赋给 string[]`。包一层 reactive 后属性访问自动解包，
   * 调用方（尤其是模板）不用记得加 `.value`。
   */
  return reactive({
    columns, hidden, shown, visibleCount, isVisible, toggle, showAll, reset,
  })
}
