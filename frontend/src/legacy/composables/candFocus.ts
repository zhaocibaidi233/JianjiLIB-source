/**
 * 候选弹窗里的焦点游走。
 *
 * 抽成独立模块而不是埋在 `useCatalogLookup` 里：那是个几百行、依赖 router/多个 ref
 * 的 composable，为了测"按一下 ↓ 该落到第几个"去把它整个搭起来不划算。这里两个
 * 函数都是纯的（一个只吃数字，一个只吃 DOM），可以直接单测。
 */

/** 焦点顺序：`-1` 表示当前焦点不在列表里。 */
export function nextFocusIndex(count: number, current: number, forward: boolean): number {
  if (count <= 0) return -1
  if (current < 0) return forward ? 0 : count - 1
  return (current + (forward ? 1 : -1) + count) % count
}

/**
 * 弹窗里"能用方向键走到"的元素，按**视觉从上到下**的顺序。
 *
 * 顺序 = DOM 顺序：扩展检索按钮 → 各候选卡片 → 页脚按钮。
 * 跳过 disabled 与被隐藏的（扩展检索那块只在"仅本库命中"时渲染）。
 */
export function collectFocusables(root: ParentNode | null): HTMLElement[] {
  if (!root) return []
  const nodes = root.querySelectorAll<HTMLElement>(
    '[data-cand-focus], .el-dialog__footer button')
  return Array.from(nodes).filter((el) => {
    if ((el as HTMLButtonElement).disabled) return false
    // offsetParent 为 null = 被 v-if / display:none 藏起来了。
    // 例外：当前已经有焦点的元素算数（某些布局下 offsetParent 会是 null）。
    return el.offsetParent !== null || el === root.ownerDocument?.activeElement
  })
}

/**
 * 按方向键移动焦点。返回是否处理了这次按键。
 *
 * ↑↓ 与 ←→ 等价——方向键习惯因人而异，两种都认成本最低。
 */
export function moveFocus(root: ParentNode | null, key: string): boolean {
  const forward = key === 'ArrowDown' || key === 'ArrowRight'
  const backward = key === 'ArrowUp' || key === 'ArrowLeft'
  if (!forward && !backward) return false
  const items = collectFocusables(root)
  if (!items.length) return false
  const active = root?.ownerDocument?.activeElement as HTMLElement | null
  const at = active ? items.indexOf(active) : -1
  items[nextFocusIndex(items.length, at, forward)]?.focus()
  return true
}
