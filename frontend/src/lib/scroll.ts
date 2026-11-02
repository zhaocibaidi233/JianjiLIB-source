/**
 * 列表翻页 / 加载后的滚动定位（2026-10-25 易用性整改）。
 *
 * 痛点：列表页翻页时只「改页码 + 重新请求」，没人管滚动位置。用户看完一页、
 * 在底部点「下一页」，新内容渲染到**上方**，他必须往上滚才能看到——实际体验
 * 是"翻了页却什么都没变"。工具化之后各页面调用一行即可，不必各写一遍。
 */

/**
 * 顶部 sticky 工具栏的遮挡高度：目标要扣掉，否则标题被压住。
 * 读 CSS 变量 `--topbar-h`（见 styles/admin.css，矮屏档会调小），读不到用 62。
 * 多留 10px 视觉余量，免得标题紧贴栏底。
 */
function stickyHeadOffset(): number {
  const raw = getComputedStyle(document.documentElement).getPropertyValue('--topbar-h')
  const px = parseFloat(raw)
  return (Number.isFinite(px) ? px : 62) + 10
}

/** 把元素滚到视口顶部（自动扣掉 sticky 顶栏）。 */
export function scrollElementToTop(el: Element | null | undefined): void {
  if (!el) return
  const top = el.getBoundingClientRect().top + window.scrollY - stickyHeadOffset()
  window.scrollTo({ top: Math.max(0, top), behavior: 'smooth' })
}

/**
 * 从参考元素（通常是分页栏）往上找**最近的有高度的兄弟节点**，把它滚到顶部。
 *
 * 列表通常就是分页栏的前一个兄弟（表格容器 / 书卡网格 / 分组列表），比让每个
 * 页面传 ref 省事，也不依赖具体类名；中间的空态/骨架节点高度为 0，跳过。
 */
export function scrollPreviousSiblingToTop(from: Element | null | undefined): void {
  let el = (from?.previousElementSibling as HTMLElement | null) ?? null
  while (el && el.getBoundingClientRect().height < 8) {
    el = (el.previousElementSibling as HTMLElement | null) ?? null
  }
  scrollElementToTop(el ?? from ?? null)
}
