import { onScopeDispose, reactive } from 'vue'
import { errorMessage } from './api'

/**
 * 列表页骨架的唯一实现：分页状态 + 竞态守卫 + 搜索防抖。
 *
 * 过去每个列表视图各写一遍 `reactive({items,total,page,pageSize,loading,error})`
 * + `loadList` + 280ms 防抖 `onSearchInput` + `setPage` + 卸载时清定时器
 * （Logs / Readers / Circulation / Reservations / InventorySession …），其中只有
 * Circulation 记得做**竞态守卫**——快速切筛选时旧响应晚到会把新结果覆盖掉。
 * 收到这里之后：守卫全都有，定时器自动清理（onScopeDispose），口径只此一份。
 *
 * 视图差异全部留在外面：`fetch` 自己拼查询参数（offset 制 / page_size 制 /
 * 附加筛选都随它），响应里的附加字段（罚款汇总等）走 `onLoaded` 读取。
 */
export interface PagedList<T> {
  items: T[]
  total: number
  page: number
  pageSize: number
  loading: boolean
  error: string
  /** 表头排序键（'' = 各接口的默认顺序）；sortOrder 三态 asc / desc / ''。 */
  sortKey: string
  sortOrder: '' | 'asc' | 'desc'
}

interface PagedListOptions<T, D extends { items: T[]; total: number }> {
  /**
   * 拉一页：翻页与表头排序都交给这里，查询参数由视图自己拼。
   * `sort` / `order` 直通后端 `?sort=&order=`（白名单见各接口，键外回退默认）。
   */
  fetch: (page: { page: number; pageSize: number; sort: string; order: '' | 'asc' | 'desc' }) => Promise<D>
  pageSize?: number
  /** 搜索框防抖毫秒数（默认 280 —— 与既有各视图的手写值一致） */
  debounceMs?: number
  /** 每次翻页 / 页码重置时调用（清勾选之类与页码联动的视图状态） */
  onPage?: () => void
  /** 拉取成功后的回调（读响应附加字段）；被更新请求超越时不会调用 */
  onLoaded?: (data: D) => void
}

export function usePagedList<T, D extends { items: T[]; total: number } = { items: T[]; total: number }>(
  options: PagedListOptions<T, D>,
) {
  const list = reactive<PagedList<T>>({
    items: [],
    total: 0,
    page: 1,
    pageSize: options.pageSize ?? 20,
    loading: false,
    error: '',
    sortKey: '',
    sortOrder: '',
  })

  // 竞态守卫：每次拉取自增请求号，晚到的旧响应（id 不等于最新号）整体丢弃。
  // 视图若在响应前又改了筛选并触发新请求，新请求号更大，旧的那份作废。
  let requestId = 0
  let timer = 0

  async function load() {
    const id = ++requestId
    list.loading = true
    list.error = ''
    try {
      const data = await options.fetch({ page: list.page, pageSize: list.pageSize,
                                         sort: list.sortKey, order: list.sortOrder })
      if (id !== requestId) return
      // Vue 的 reactive 会把泛型 T 解包成 UnwrapRefSimple<T>，赋值侧与声明侧在
      // 类型系统里对不上——运行时是同一个数组引用，这里断言一次即可。
      list.items = data.items as unknown as typeof list.items
      list.total = data.total
      options.onLoaded?.(data)
    } catch (error) {
      if (id !== requestId) return
      list.error = errorMessage(error)
      list.items = []
      list.total = 0
    } finally {
      if (id === requestId) list.loading = false
    }
  }

  function resetAndLoad() {
    list.page = 1
    options.onPage?.()
    void load()
  }

  /** 点表头排序：asc → desc → 默认顺序 三态循环（与图书库 el-table 一致），回第 1 页重查。 */
  function setSort(key: string) {
    if (list.sortKey !== key) {
      list.sortKey = key
      list.sortOrder = 'asc'
    } else if (list.sortOrder === 'asc') {
      list.sortOrder = 'desc'
    } else {
      list.sortKey = ''
      list.sortOrder = ''
    }
    resetAndLoad()
  }

  function setPage(page: number) {
    list.page = page
    options.onPage?.()
    void load()
  }

  function onSearchInput() {
    window.clearTimeout(timer)
    timer = window.setTimeout(resetAndLoad, options.debounceMs ?? 280)
  }

  onScopeDispose(() => window.clearTimeout(timer))

  return { list, load, resetAndLoad, setPage, setSort, onSearchInput }
}
