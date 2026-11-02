import { computed } from 'vue'
import type { Ref } from 'vue'
import type { SimpleNavDir } from '../simpleForm'
import { nextSimpleKey, simpleFormRows } from '../simpleForm'

/**
 * 简编表单键盘导航：回车跳下一个框；方向键按"类表格"移动 —— ↑/↓ 到同一列的相邻行，
 * ←/→ 同行左右。行结构见 `simpleForm.ts::simpleFormRows()`（与模板的 24 栅格**同源**，
 * 不另抄一份数字）。
 *
 * ⚠️ 三条"不抢"的守卫，否则会破坏原生编辑体验：
 *   ① **el-select 不抢 Enter / ↑/↓** —— Element 用它们打开下拉、上下选选项；
 *   ② **textarea 不抢 Enter**（那是换行）；
 *   ③ **文本输入框的 ←/→ 只在光标贴边时接管** —— 否则框内没法移光标。
 *      （录入栏那边是**完全不借 ←/→**；这里字段成行成列，多一个"贴边"条件才两全。）
 */
export function useSimpleKeyNav(opts: { shelfNoEnabled: Ref<boolean> }) {
  const simpleNavRows = computed(() => simpleFormRows(opts.shelfNoEnabled.value))

  /** 光标是否贴在值的开头 / 结尾（有选区时不算 —— 先让用户把选区处理完）。 */
  function atCaretBoundary(el: HTMLElement, which: 'start' | 'end'): boolean {
    const inp = el as HTMLInputElement | HTMLTextAreaElement
    if (typeof inp.selectionStart !== 'number' || typeof inp.selectionEnd !== 'number') return true
    if (inp.selectionStart !== inp.selectionEnd) return false
    return which === 'start'
      ? inp.selectionStart === 0
      : inp.selectionEnd === (inp.value ?? '').length
  }

  /** 把焦点送到简编表单的某个字段：`data-simple-key` 锚点 → 它内部的原生控件。 */
  function focusSimpleKey(scope: ParentNode, key: string) {
    const host = scope.querySelector<HTMLElement>(`[data-simple-key="${key}"]`)
    const el = host?.matches('input, textarea')
      ? host
      : host?.querySelector<HTMLElement>('input, textarea') ?? null
    el?.focus()
  }

  function onSimpleKeydown(e: KeyboardEvent) {
    const t = e.target as HTMLElement | null
    if (!t) return
    const host = t.closest?.('[data-simple-key]') as HTMLElement | null
    const cur = host?.getAttribute('data-simple-key')
    if (!cur || !host) return
    const scope = (t.closest('.simple-form') ?? host) as ParentNode
    const isSelect = !!t.closest('.el-select')
    const isTextarea = t.tagName === 'TEXTAREA'
    const move = (dir: SimpleNavDir) => {
      const nxt = nextSimpleKey(simpleNavRows.value, cur, dir)
      if (!nxt) return                    // 到头了：不拦，交给默认行为
      e.preventDefault()
      focusSimpleKey(scope, nxt)
    }

    if (e.key === 'Enter') {
      if (isSelect || isTextarea) return  // 下拉要回车确认 / 文本域要回车换行
      move('next')
      return
    }
    if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
      if (isSelect) return                // 下拉用 ↑/↓ 选选项
      const which = e.key === 'ArrowUp' ? 'start' : 'end'
      if (isTextarea && !atCaretBoundary(t, which)) return
      move(e.key === 'ArrowUp' ? 'up' : 'down')
      return
    }
    if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
      const which = e.key === 'ArrowLeft' ? 'start' : 'end'
      if (!isSelect && !atCaretBoundary(t, which)) return
      move(e.key === 'ArrowLeft' ? 'left' : 'right')
    }
  }

  return { simpleNavRows, onSimpleKeydown }
}
