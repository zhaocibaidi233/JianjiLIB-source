import { onBeforeUnmount, onMounted } from 'vue'

/**
 * 把顶部工具栏的**实测高度**写进 CSS 变量 `--topbar-h`（2026-10-25 易用性整改）。
 *
 * 为什么要实测：吸顶元素（编目页 `.editor-head`）与翻页滚动定位（`lib/scroll`）
 * 都要扣掉顶部栏的高度，写死估算值会差几像素到十几像素——顶栏只要换行、字号
 * 或 padding 变一次就会重叠。CSS 里 `--topbar-h` 仍留一个兜底值（见
 * `styles/admin.css`），JS 能跑时以实测为准。
 *
 * @param selector 顶栏选择器：馆员端 `.topbar`，读者端 `.reader-topbar`
 */
export function useTopbarHeight(selector: string) {
  let ro: ResizeObserver | null = null

  function sync() {
    const el = document.querySelector(selector)
    const h = el?.getBoundingClientRect().height
    if (h && h > 0) {
      document.documentElement.style.setProperty('--topbar-h', `${Math.round(h)}px`)
    }
  }

  onMounted(() => {
    sync()
    const el = document.querySelector(selector)
    if (el && typeof ResizeObserver !== 'undefined') {
      // 顶栏自身高度变化（窗口变窄导致换行、字号偏好等）时跟着更新
      ro = new ResizeObserver(sync)
      ro.observe(el)
    }
    window.addEventListener('resize', sync)
  })

  onBeforeUnmount(() => {
    ro?.disconnect()
    ro = null
    window.removeEventListener('resize', sync)
  })

  return { sync }
}
