import type { Ref } from 'vue'

/**
 * 录入栏 ↑/↓ 方向键导航 + 回车跳格链（RecordEdit 录入栏）。
 *
 * 为什么用 ↑/↓ 而不是 ←/→：单行输入框里 ↑/↓ 本来就没有光标语义（只会在页面滚动），
 * 借来做"上下移动一行"很自然；←/→ 是光标键，抢了就没法在字段里移光标。
 *
 * 分类号 / 书次号 / 卷册号在视觉上同一行，算**一站**（A 行三站合一）：
 * 从这一行往上/下都是整行跳，不用先横穿到书次号或卷册号（不然"↓ 却横向跑了"很反直觉）。
 * 排架号刻意**不在**这张表里——它是随开关显隐的，且不该打断"条码回车即入库"的主链路。
 */
const PANEL_ROWS: string[][] = [
  ['isbn'], ['classNo', 'bookNo', 'volume'], ['price'], ['barcode'],
]

/** 录入栏各输入框的组件 ref（el-input / el-select，调 .focus() 用）。 */
export interface PanelInputRefs {
  isbnInput: Ref<any>
  classNoInput: Ref<any>
  bookNoInput: Ref<any>
  volumeInput: Ref<any>
  priceInput: Ref<any>
  barcodeInput: Ref<any>
}

export function usePanelNav(inputs: PanelInputRefs) {
  // PANEL_ROWS / focusNext 的 target 用的是**字段名**（data-field），这里建
  // 字段名 → ref 的映射——别把两套名字混用（行表里是 'classNo'，不是 'classNoInput'）。
  const byField: Record<string, Ref<any>> = {
    isbn: inputs.isbnInput, classNo: inputs.classNoInput, bookNo: inputs.bookNoInput,
    volume: inputs.volumeInput, price: inputs.priceInput, barcode: inputs.barcodeInput,
  }

  function onPanelArrow(e: KeyboardEvent) {
    if (e.key !== 'ArrowUp' && e.key !== 'ArrowDown') return
    // 事件委托挂在录入表单上，靠 data-field 认字段。
    // 没有 data-field 的（如馆藏地点的下拉）一律不接管——那里 ↑/↓ 要留给下拉选选项。
    const host = (e.target as HTMLElement)?.closest?.('[data-field]')
    const cur = host?.getAttribute('data-field')
    if (!cur) return
    const row = PANEL_ROWS.findIndex(r => r.includes(cur))
    if (row < 0) return
    const next = PANEL_ROWS[row + (e.key === 'ArrowDown' ? 1 : -1)]
    if (!next) return          // 已经到头：不拦，让默认行为（页面滚动）继续
    e.preventDefault()
    byField[next[0]]?.value?.focus?.()
  }

  // ===== 回车跳格链 =====
  // 馆藏地点 → ISBN → 分类号 → 书次号 → 卷册号 → 价格 → 条码（回车即入库）
  // 分类号/书次号/卷册号现在同处一行（A 行三站合一）；排架号不在链里（只在它自己框上回车跳价格）。
  function focusNext(target: 'isbn' | 'bookNo' | 'volume' | 'price' | 'barcode') {
    byField[target]?.value?.focus?.()
  }

  return { onPanelArrow, focusNext }
}
