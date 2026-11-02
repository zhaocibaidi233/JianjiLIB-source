import { reactive } from 'vue'
import { get } from '../lib/api'
import { libraryState } from './libraries'

export interface DashboardKpis {
  today_borrowed: number
  yesterday_borrowed: number
  today_returned: number
  yesterday_returned: number
  active_loans: number
  overdue: number
  readers_active: number
  reader_total: number
  collection_copies: number
  title_count: number
}

export interface TrendPoint {
  date: string
  borrowed: number
  returned: number
}

export interface CollectionRow {
  key: string
  label: string
  titles: number
  copies: number
  percent: number
}

export interface DashboardTodo {
  kind: string
  label: string
  count: number
  detail: string
  link: string
}

export interface OverdueItem {
  loan_id: number
  record_id: number
  title: string
  reader_id: number
  reader_name: string | null
  reader_card_no: string | null
  due_on: string
  days_late: number
}

export interface BookBrief {
  record_id: number
  title: string
  author: string | null
  class_no: string | null
  available_copies: number
  loans_30d?: number
  created_at?: string | null
}

export interface DashboardData {
  library: { id: number; name: string } | null
  today: string
  kpis: DashboardKpis
  trend: TrendPoint[]
  collection: { total: number; rows: CollectionRow[] }
  todos: DashboardTodo[]
  overdue_items: OverdueItem[]
  hot_books: BookBrief[]
  new_books: BookBrief[]
}

export const dashboardState = reactive({
  data: null as DashboardData | null,
  loading: false,
  error: '',
})

let inflight: Promise<void> | null = null
let lastLibraryId: number | null = null

export async function loadDashboard(force = false): Promise<void> {
  const libraryId = libraryState.currentId
  if (!force && dashboardState.data && lastLibraryId === libraryId) return
  if (inflight) {
    await inflight
    if (lastLibraryId === libraryId) return
  }
  dashboardState.loading = true
  dashboardState.error = ''
  inflight = (async () => {
    try {
      dashboardState.data = await get<DashboardData>(
        '/dashboard',
        libraryId ? { library_id: libraryId } : undefined,
      )
      lastLibraryId = libraryId
    } catch (error) {
      dashboardState.error = error instanceof Error ? error.message : '工作台数据加载失败'
    } finally {
      dashboardState.loading = false
      inflight = null
    }
  })()
  return inflight
}

export function overdueCount(): number {
  return dashboardState.data?.kpis.overdue ?? 0
}
