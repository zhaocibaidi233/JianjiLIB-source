import { nextTick, onBeforeUnmount, onMounted, ref, type Ref } from 'vue'

/**
 * 让 el-table 自适应一屏（2026-10-25 易用性整改）。
 *
 * 痛点：legacy 列表页把 20~50 行全部铺开，整页高度 1300~2600px。1080P（视口约
 * 945px）下分页栏被推到视口外——用户每翻一次页都要先滚到底、点完再滚回去看
 * 新内容。这里按「表格顶部到视口底」实时算出 max-height，交给 Element Plus 做
 * **表体内部滚动 + 表头固定**，分页栏就自然留在视口内。
 *
 * 为什么不用固定 CSS（如 `calc(100vh - 400px)`）：每个页面的页头/筛选条高度不同
 * （图书库 ~323px、条码 ~230px），写死必然一边不准。这里实测表格位置再算，
 * 筛选条展开/收起、窗口缩放都会跟着重算。
 *
 * @param anchor      表格的 ref（`ref="tableRef"` 指向 el-table 实例）
 * @param reservePx   表格下方要为分页栏 + 页底留白预留的高度
 */
export function useTableFill(anchor: Ref<any>, reservePx = 96) {
  const maxHeight = ref<number | undefined>(undefined)

  function tableEl(): HTMLElement | null {
    const v = anchor.value
    // el-table 实例 → 根 DOM；也接受直接传入 DOM 元素
    return (v?.$el instanceof HTMLElement ? v.$el : (v instanceof HTMLElement ? v : null))
  }

  function recompute() {
    const el = tableEl()
    if (!el) return
    const top = el.getBoundingClientRect().top + window.scrollY
    maxHeight.value = Math.max(240, Math.round(window.innerHeight - top - reservePx))
  }

  let ro: ResizeObserver | null = null

  onMounted(async () => {
    await nextTick()
    recompute()
    // 页头/筛选条自身高度变化（例如点了「更多筛选」）时也要重算
    const el = tableEl()
    if (el?.parentElement && typeof ResizeObserver !== 'undefined') {
      ro = new ResizeObserver(() => recompute())
      ro.observe(el.parentElement)
    }
    window.addEventListener('resize', recompute)
  })

  onBeforeUnmount(() => {
    ro?.disconnect()
    ro = null
    window.removeEventListener('resize', recompute)
  })

  return { maxHeight, recompute }
}

/**
 * 翻页后把 el-table 的**表体**滚动条归零。
 *
 * 表格改用内部滚动（`max-height`）后多出一个坑：Element Plus 换数据不会重置
 * 表体滚动位置，翻到第 2 页时用户看到的仍是"第 5 行开始"——比整页滚动更容易
 * 被误认为"没翻页"。整页滚动由 `lib/scroll` 负责，表体这一层必须单独归零。
 */
export function resetTableScroll(tableRef: Ref<any>) {
  const el: HTMLElement | undefined = tableRef.value?.$el
  const wrap = el?.querySelector('.el-scrollbar__wrap') as HTMLElement | null
  if (wrap) wrap.scrollTop = 0
}
