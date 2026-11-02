<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import AppIcon from '../../components/AppIcon.vue'
import BookCover from '../../components/BookCover.vue'
import BaseModal from '../../components/BaseModal.vue'
import ConfirmDialog from '../../components/ConfirmDialog.vue'
import PagerBar from '../../components/PagerBar.vue'
import TableState from '../../components/TableState.vue'
import ThSort from '../../components/ThSort.vue'
import { can } from '../../lib/auth'
import { download, errorMessage, get, post } from '../../lib/api'
import { usePagedList } from '../../lib/usePagedList'
import { holdingLocationLabel } from '../../lib/displayLabels'
import { avatarClass, formatDate, formatDateTime, formatMoney, formatNumber } from '../../lib/format'
import { toast, toastError } from '../../lib/toast'
import { libraryState, loadLibraries } from '../../stores/libraries'

type DeskMode = 'borrow' | 'return'
type LoanTab = 'active' | 'overdue' | 'returned' | 'lost' | 'fines'
type FineFilter = 'unpaid' | 'paid' | 'waived' | 'all'

interface Loan {
  id: number
  holding_id: number
  record_id: number
  barcode: string | null
  title: string | null
  reader_id: number
  reader_card_no: string | null
  reader_name: string | null
  reader_class: string | null
  loaned_at: string | null
  due_on: string | null
  returned_at: string | null
  renew_count: number
  status: string
  status_label: string
  overdue: boolean
  days_overdue: number
  fine_amount: number
  fine_status: string | null
  fine_status_label: string | null
  compensation_amount: number
  compensation_status: string | null
  compensation_status_label: string | null
  lost_at: string | null
  lost_note: string | null
}

interface LoanList {
  total: number
  page: number
  page_size: number
  items: Loan[]
  summary?: {
    unpaid_count: number
    unpaid_amount: number
    collected_today: number
  }
}

interface ItemPanel {
  holding_id: number
  record_id: number
  barcode: string | null
  title: string | null
  isbn: string | null
  call_no: string | null
  location: string | null
  shelf_no: string | null
  status: string
  on_loan: boolean
  loan: Loan | null
}

interface ReaderPanel {
  id: number
  card_no: string
  name: string
  reader_type_label: string
  grade: string | null
  class_name: string | null
  phone: string | null
  status: string
  status_label: string
  rules: { max_items: number; loan_days: number; max_renew: number }
  active_count: number
  overdue_count: number
  active_loans: Loan[]
  fines: { enabled: boolean; unpaid: number; unpaid_count: number; overdue_estimate: number }
  compensations: { lost_count: number; unpaid: number; unpaid_count: number }
}

interface CirculationStats {
  summary: {
    today_borrowed: number
    today_returned: number
    active: number
    overdue: number
    readers_active: number
    lost: number
    fines_today: number
    fines_unpaid: number
    fines_unpaid_count: number
    compensation_unpaid: number
    compensation_unpaid_count: number
  }
}

interface Rules {
  types: Record<string, { label: string; loan_days: number; max_items: number; max_renew: number }>
  reservation_hold_days: number
  fines: { enabled: boolean; per_day: number; grace_days: number; max_amount: number }
}

interface ReturnResult {
  loan: Loan
  overdue: boolean
  message: string
  reservations_promoted: number
}

interface SettleTarget {
  loan: Loan
  kind: 'fine' | 'compensation'
}

const route = useRoute()
const TABS: Array<{ key: LoanTab; label: string }> = [
  { key: 'active', label: '外借' },
  { key: 'overdue', label: '逾期' },
  { key: 'returned', label: '已归还' },
  { key: 'lost', label: '已遗失' },
  { key: 'fines', label: '逾期罚款' },
]

function normalizeTab(value: unknown): LoanTab {
  const text = String(value || '')
  if (text === 'today') return 'active'
  return TABS.some((tab) => tab.key === text) ? (text as LoanTab) : 'active'
}

const mode = ref<DeskMode>(route.query.mode === 'return' ? 'return' : 'borrow')
const tab = ref<LoanTab>(normalizeTab(route.query.tab))
const canWrite = computed(() => can('circulation', 'write'))
const libraryId = computed(() => libraryState.currentId)

const stats = ref<CirculationStats | null>(null)
const rules = ref<Rules | null>(null)
const finesEnabled = computed(() => Boolean(rules.value?.fines.enabled))

function countsFor(key: LoanTab): number | null {
  const summary = stats.value?.summary
  if (!summary) return null
  if (key === 'active') return summary.active
  if (key === 'overdue') return summary.overdue
  if (key === 'fines' && finesEnabled.value) return fineSummary.value?.unpaid_count ?? 0
  return null
}

let statsRequest = 0
async function loadStats() {
  const request = ++statsRequest
  const library = libraryId.value
  const current = () => request === statsRequest && library === libraryId.value
  try {
    const data = await get<CirculationStats>('/circulation/stats', {
      library_id: library || undefined,
    })
    if (current()) stats.value = data
  } catch (error) {
    if (!current()) return
    stats.value = null
    toastError(errorMessage(error))
  }
}

async function loadRules() {
  try {
    const data = await get<{ rules: Rules }>('/circulation/rules')
    rules.value = data.rules
    if (!data.rules.fines.enabled && tab.value === 'fines') chooseTab('active')
  } catch {
    rules.value = null
  }
}

/* ---------------- Reader lookup & scanning ---------------- */
const readerCard = ref('')
const reader = ref<ReaderPanel | null>(null)
const readerLoading = ref(false)
const readerError = ref('')
const scanInput = ref<HTMLInputElement | null>(null)

let deskVersion = 0
let readerRequest = 0
let scanRequest = 0

function invalidateReader() {
  deskVersion += 1
  readerRequest += 1
  scanRequest += 1
  reader.value = null
  readerLoading.value = false
  readerError.value = ''
  scanning.value = false
  scanValue.value = ''
  if (pendingItems.value.length) toast('读者或办理上下文已变更，待借清单已清空', 'info')
  pendingItems.value = []
}

async function fetchReader(card: string, library: number | null, version: number) {
  const request = ++readerRequest
  const current = () => request === readerRequest && version === deskVersion
    && library === libraryId.value && card === readerCard.value.trim()
  readerLoading.value = true
  readerError.value = ''
  try {
    const data = await get<ReaderPanel>('/circulation/reader', {
      card_no: card,
      library_id: library || undefined,
    })
    if (!current()) return
    reader.value = data
    await nextTick()
    if (current()) scanInput.value?.focus()
  } catch (error) {
    if (!current()) return
    reader.value = null
    readerError.value = errorMessage(error)
  } finally {
    if (current()) readerLoading.value = false
  }
}

async function identifyReader() {
  if (borrowBusy.value) return
  const card = readerCard.value.trim()
  invalidateReader()
  if (!card) {
    readerError.value = '请输入借书证号'
    return
  }
  await fetchReader(card, libraryId.value, deskVersion)
}

function clearReader() {
  invalidateReader()
  readerCard.value = ''
}

const pendingItems = ref<ItemPanel[]>([])
const scanValue = ref('')
const scanning = ref(false)
const borrowBusy = ref(false)
const limitReached = computed(() => {
  if (!reader.value) return false
  return reader.value.active_count + pendingItems.value.length > reader.value.rules.max_items
})

watch(readerCard, () => {
  if (!borrowBusy.value) invalidateReader()
}, { flush: 'sync' })

async function addBarcode() {
  const barcode = scanValue.value.trim()
  if (!barcode || !reader.value || borrowBusy.value || readerLoading.value || scanning.value) return
  if (reader.value.status !== 'ACTIVE') {
    toastError('该读者当前状态不可借阅')
    return
  }
  if (reader.value.active_count + pendingItems.value.length >= reader.value.rules.max_items) {
    toastError(`该读者最多可借 ${reader.value.rules.max_items} 册，已达上限`)
    return
  }
  if (pendingItems.value.some((item) => item.barcode === barcode)) {
    toast('该册已在待借清单中', 'info')
    scanValue.value = ''
    return
  }
  const request = ++scanRequest
  const version = deskVersion
  const library = libraryId.value
  const readerId = reader.value.id
  const current = () => request === scanRequest && version === deskVersion
    && library === libraryId.value && readerId === reader.value?.id && !borrowBusy.value
  scanning.value = true
  try {
    const item = await get<ItemPanel>('/circulation/item', {
      barcode,
      library_id: library || undefined,
    })
    if (!current()) return
    if (item.on_loan) throw new Error('该册当前外借中，不可重复外借')
    if (item.status !== 'AVAILABLE') throw new Error('该册不在架，无法外借')
    pendingItems.value.push(item)
    scanValue.value = ''
    await nextTick()
    if (current()) scanInput.value?.focus()
  } catch (error) {
    if (current()) toastError(errorMessage(error))
  } finally {
    if (request === scanRequest) scanning.value = false
  }
}

function removePending(index: number) {
  if (borrowBusy.value) return
  pendingItems.value.splice(index, 1)
}

async function confirmBorrow() {
  if (!canWrite.value || !reader.value || reader.value.status !== 'ACTIVE'
    || !pendingItems.value.length || borrowBusy.value || readerLoading.value || scanning.value || limitReached.value) return
  // 全批写入使用同一个读者、图书库与清单；全局库切换也不能改变已提交的批次。
  const snapshot = {
    readerId: reader.value.id, card: readerCard.value.trim(), library: libraryId.value,
    version: deskVersion, mode: mode.value, items: pendingItems.value.map((item) => ({ ...item })),
  }
  const current = () => snapshot.version === deskVersion && snapshot.library === libraryId.value
    && snapshot.mode === mode.value && snapshot.card === readerCard.value.trim()
    && snapshot.readerId === reader.value?.id
  borrowBusy.value = true
  const failed: ItemPanel[] = []
  let success = 0
  let due = ''
  try {
    for (const item of snapshot.items) {
      try {
        const result = await post<{ message: string; loan: Loan }>('/circulation/borrow', {
          library_id: snapshot.library || undefined,
          reader_id: snapshot.readerId,
          barcode: item.barcode,
        })
        success += 1
        due = result.loan.due_on || ''
      } catch (error) {
        failed.push(item)
        toastError(`${item.title || item.barcode}：${errorMessage(error)}`)
      }
    }
    if (current()) pendingItems.value = failed
    if (success) {
      toast(due ? `已外借 ${success} 册，应还日期 ${due}` : `已外借 ${success} 册`)
      if (current()) await Promise.all([
        refreshAfterChange(), fetchReader(snapshot.card, snapshot.library, snapshot.version),
      ])
    }
  } finally {
    borrowBusy.value = false
  }
}

/* ---------------- Return ---------------- */
const returning = ref(false)
const returnValue = ref('')
const returnResult = ref<ReturnResult | null>(null)
const returnError = ref('')
const recentReturns = ref<Array<{ barcode: string; title: string; reader: string }>>([])
const returnInput = ref<HTMLInputElement | null>(null)

async function submitReturn() {
  const barcode = returnValue.value.trim()
  if (!barcode || returning.value) return
  returning.value = true
  returnError.value = ''
  try {
    const result = await post<ReturnResult>('/circulation/return', {
      library_id: libraryId.value || undefined,
      barcode,
    })
    returnResult.value = result
    returnValue.value = ''
    recentReturns.value.unshift({
      barcode: result.loan.barcode || barcode,
      title: result.loan.title || '',
      reader: result.loan.reader_name || result.loan.reader_card_no || '',
    })
    recentReturns.value = recentReturns.value.slice(0, 5)
    toast(result.message)
    if (reader.value) void identifyReader()
    await Promise.all([loadStats(), loadPreview(), loadList()])
  } catch (error) {
    returnError.value = errorMessage(error)
    toastError(returnError.value)
  } finally {
    returning.value = false
    await nextTick()
    returnInput.value?.focus()
  }
}

/* ---------------- Active preview ---------------- */
const preview = ref<Loan[]>([])
const previewTotal = ref(0)
const previewLoading = ref(false)
const previewOverdueOnly = ref(false)

let previewRequest = 0
async function loadPreview() {
  const request = ++previewRequest
  const library = libraryId.value
  const overdue = previewOverdueOnly.value
  const current = () => request === previewRequest && library === libraryId.value
    && overdue === previewOverdueOnly.value
  previewLoading.value = true
  try {
    const data = await get<LoanList>('/circulation/loans', {
      library_id: library || undefined,
      status: overdue ? 'overdue' : 'active',
      page_size: 6,
    })
    if (!current()) return
    preview.value = data.items
    previewTotal.value = data.total
  } catch {
    if (!current()) return
    preview.value = []
    previewTotal.value = 0
  } finally {
    if (current()) previewLoading.value = false
  }
}

watch(previewOverdueOnly, () => void loadPreview())

/* ---------------- Ledger list ---------------- */
const q = ref('')
const fineFilter = ref<FineFilter>('unpaid')
const fineSummary = ref<LoanList['summary'] | null>(null)

// 竞态守卫原先在本文件手写（listRequest 计数 + listContext 快照），现在
// usePagedList 全都有；fineSummary 从响应附加字段读取，走 onLoaded。
const { list, load: loadList, setPage, setSort, onSearchInput } = usePagedList<Loan, LoanList>({
  onLoaded: (data) => { fineSummary.value = data.summary || null },
  fetch: ({ page, pageSize, sort, order }) => {
    const fines = tab.value === 'fines'
    return get<LoanList>(fines ? '/circulation/fines' : '/circulation/loans', {
      library_id: libraryId.value || undefined,
      status: fines ? fineFilter.value : tab.value,
      q: q.value.trim() || undefined,
      sort: sort || undefined,
      order: order || undefined,
      page,
      page_size: pageSize,
    })
  },
})

function chooseTab(next: LoanTab) {
  if (next === 'fines' && !finesEnabled.value) return
  if (tab.value === next) return
  tab.value = next
  list.page = 1
  void loadList()
}

watch(fineFilter, () => {
  list.page = 1
  void loadList()
})

watch(libraryId, async () => {
  clearReader()
  stats.value = null
  preview.value = []
  previewTotal.value = 0
  list.items = []
  list.total = 0
  fineSummary.value = null
  previewOverdueOnly.value = false
  list.page = 1
  returnResult.value = null
  recentReturns.value = []
  await Promise.all([loadStats(), loadPreview(), loadList()])
}, { flush: 'sync' })

/* ---------------- Row operations ---------------- */
const actionBusy = ref(false)
const returnTarget = ref<Loan | null>(null)
const lostTarget = ref<Loan | null>(null)
const lostForm = reactive({ amount: '0', paid: false, note: '' })
const settleTarget = ref<SettleTarget | null>(null)
const settleStatus = ref<'PAID' | 'WAIVED' | 'UNPAID'>('PAID')

async function renew(loan: Loan) {
  if (actionBusy.value) return
  actionBusy.value = true
  try {
    const result = await post<{ message: string }>('/circulation/renew', { loan_id: loan.id })
    toast(result.message)
    await Promise.all([loadList(), loadPreview(), loadStats()])
  } catch (error) {
    toastError(errorMessage(error))
  } finally {
    actionBusy.value = false
  }
}

async function doReturn(loan: Loan) {
  const target = returnTarget.value
  returnTarget.value = null
  if (!loan.barcode) return
  actionBusy.value = true
  try {
    const result = await post<ReturnResult>('/circulation/return', {
      library_id: libraryId.value || undefined,
      barcode: loan.barcode,
    })
    toast(result.message)
    if (target) returnResult.value = result
    await Promise.all([loadList(), loadPreview(), loadStats()])
  } catch (error) {
    toastError(errorMessage(error))
  } finally {
    actionBusy.value = false
  }
}

function openLost(loan: Loan) {
  lostTarget.value = loan
  lostForm.amount = '0.00'
  lostForm.paid = false
  lostForm.note = ''
}

async function submitLost() {
  const loan = lostTarget.value
  if (!loan) return
  const amount = Number(lostForm.amount)
  if (!Number.isFinite(amount) || amount < 0) {
    toastError('赔偿金额应为不小于 0 的数字')
    return
  }
  actionBusy.value = true
  try {
    const result = await post<{ message: string }>(`/circulation/loans/${loan.id}/lost`, {
      amount,
      paid: lostForm.paid,
      note: lostForm.note.trim() || null,
    })
    toast(result.message)
    lostTarget.value = null
    if (reader.value && reader.value.id === loan.reader_id) void identifyReader()
    await Promise.all([loadList(), loadPreview(), loadStats()])
  } catch (error) {
    toastError(errorMessage(error))
  } finally {
    actionBusy.value = false
  }
}

function openSettle(loan: Loan, kind: 'fine' | 'compensation') {
  settleTarget.value = { loan, kind }
  settleStatus.value = 'PAID'
}

async function submitSettle() {
  const target = settleTarget.value
  if (!target) return
  actionBusy.value = true
  try {
    const url =
      target.kind === 'fine'
        ? `/circulation/fines/${target.loan.id}/settle`
        : `/circulation/loans/${target.loan.id}/compensation/settle`
    const result = await post<{ message: string }>(url, { status: settleStatus.value })
    toast(result.message)
    settleTarget.value = null
    await Promise.all([loadList(), loadStats()])
  } catch (error) {
    toastError(errorMessage(error))
  } finally {
    actionBusy.value = false
  }
}

/* ---------------- Export & print ---------------- */
async function exportOverdue() {
  try {
    await download('/circulation/overdue/export', {
      library_id: libraryId.value || undefined,
    }, '逾期清单.xlsx')
  } catch (error) {
    toastError(errorMessage(error))
  }
}

function escapeHtml(value: string | null | undefined): string {
  return String(value || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

interface OverdueNotices {
  library_name: string
  today: string
  generated_at: string
  total_readers: number
  total_loans: number
  readers: Array<{
    card_no: string | null
    name: string | null
    class_name: string | null
    phone: string | null
    count: number
    max_days_overdue: number
    items: Array<{ barcode: string | null; title: string | null; due_on: string; days_overdue: number }>
  }>
}

async function printOverdue() {
  const win = window.open('', '_blank', 'width=920,height=800')
  if (!win) {
    toastError('浏览器拦截了打印窗口，请允许弹出窗口后重试')
    return
  }
  try {
    const data = await get<OverdueNotices>('/circulation/overdue/notices', {
      library_id: libraryId.value || undefined,
    })
    if (!data.readers.length) {
      win.close()
      toast('当前没有逾期记录', 'info')
      return
    }
    const groups = data.readers.map((reader) => `
      <section class="group">
        <header>
          <strong>${escapeHtml(reader.name)}</strong>
          <span>${escapeHtml(reader.card_no)} · ${escapeHtml(reader.class_name || '')}</span>
          <em>${reader.count} 册 · 最长逾期 ${reader.max_days_overdue} 天</em>
        </header>
        <table>
          <thead><tr><th>条码</th><th>题名</th><th>应还日期</th><th>逾期天数</th></tr></thead>
          <tbody>${reader.items.map((item) => `
            <tr><td>${escapeHtml(item.barcode)}</td><td>${escapeHtml(item.title)}</td>
            <td>${escapeHtml(item.due_on)}</td><td>${item.days_overdue}</td></tr>`).join('')}
          </tbody>
        </table>
      </section>`).join('')
    win.document.write(`<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
      <title>逾期催还单</title><style>
        *{box-sizing:border-box} body{margin:0;padding:18mm;font-family:"Microsoft YaHei",sans-serif;color:#182420;font-size:12px;line-height:1.6}
        h1{font-size:20px;margin:0 0 4px} .meta{color:#6b7a73;font-size:11px;margin-bottom:18px}
        .group{border:1px solid #d7dcd4;border-radius:8px;padding:12px 14px;margin-bottom:12px;break-inside:avoid}
        header{display:flex;align-items:baseline;gap:10px;border-bottom:1px solid #e5e8e2;padding-bottom:7px;margin-bottom:7px}
        header strong{font-size:14px} header span{color:#6b7a73} header em{margin-left:auto;color:#bf5540;font-style:normal;font-weight:600}
        table{width:100%;border-collapse:collapse} th,td{text-align:left;padding:4px 6px;border-bottom:1px solid #eef0eb}
        th{color:#96a29c;font-weight:600;font-size:10.5px} td:last-child{color:#bf5540;font-weight:600}
        footer{margin-top:16px;color:#96a29c;font-size:10px}
        @media print{body{padding:0}.group{page-break-inside:avoid}}
      </style></head><body>
      <h1>逾期催还单</h1>
      <div class="meta">${escapeHtml(data.library_name)} · 生成于 ${escapeHtml(data.generated_at.replace('T', ' ').slice(0, 16))} ·
        共 ${data.total_readers} 位读者 / ${data.total_loans} 册</div>
      ${groups}
      <footer>请读者尽快到馆归还。实际逾期罚款以归还时的系统结算为准。</footer>
      </body></html>`)
    win.document.close()
    win.focus()
    window.setTimeout(() => win.print(), 350)
  } catch (error) {
    win.close()
    toastError(errorMessage(error))
  }
}

/* ---------------- Refresh & keyboard ---------------- */
async function refreshAfterChange() {
  await Promise.all([loadStats(), loadPreview(), loadList()])
}

function switchMode(next: DeskMode) {
  if (borrowBusy.value) return
  if (next !== mode.value) invalidateReader()
  mode.value = next
  nextTick(() => {
    if (next === 'borrow') (reader.value ? scanInput.value : document.querySelector<HTMLInputElement>('#reader-card'))?.focus()
    else returnInput.value?.focus()
  })
}

function onKeydown(event: KeyboardEvent) {
  const target = event.target as HTMLElement | null
  const tag = target?.tagName?.toLowerCase()
  if (tag === 'input' || tag === 'textarea' || tag === 'select' || target?.isContentEditable) return
  if (event.key === 'b' || event.key === 'B') switchMode('borrow')
  if (event.key === 'r' || event.key === 'R') switchMode('return')
}

watch(
  () => route.query.tab,
  (value) => {
    const next = normalizeTab(value)
    if (next !== tab.value) {
      tab.value = next
      list.page = 1
      void loadList()
    }
  },
)
watch(
  () => route.query.mode,
  (value) => {
    const next: DeskMode = value === 'return' ? 'return' : 'borrow'
    if (next !== mode.value) switchMode(next)
  },
)
watch(
  () => route.query.reader,
  (value) => {
    if (!borrowBusy.value && typeof value === 'string' && value.trim() && value !== readerCard.value) {
      readerCard.value = value.trim()
      void identifyReader()
    }
  },
)

onMounted(async () => {
  await loadLibraries()
  window.addEventListener('keydown', onKeydown)
  await Promise.all([loadStats(), loadRules(), loadPreview(), loadList()])
  if (typeof route.query.reader === 'string' && route.query.reader.trim()) {
    readerCard.value = route.query.reader.trim()
    await identifyReader()
  } else if (mode.value === 'return') {
    nextTick(() => returnInput.value?.focus())
  }
})

onBeforeUnmount(() => {
  deskVersion += 1
  readerRequest += 1
  scanRequest += 1
  statsRequest += 1
  previewRequest += 1
  window.removeEventListener('keydown', onKeydown)
})

const tabDefinitions = computed(() =>
  TABS.filter((item) => item.key !== 'fines' || finesEnabled.value),
)

watch(finesEnabled, (enabled) => {
  if (!enabled && tab.value === 'fines') chooseTab('active')
})

const listEmptyText = computed(() => {
  if (list.error) return list.error
  if (tab.value === 'fines') return '当前没有符合条件的逾期罚款记录'
  if (tab.value === 'overdue') return '当前没有逾期记录'
  if (tab.value === 'returned') return '当前没有已归还记录'
  if (tab.value === 'lost') return '当前没有遗失记录'
  return '当前没有外借记录'
})
</script>

<template>
  <section class="view">
    <div class="metric-grid">
      <div class="metric-card">
        <span class="metric-icon"><AppIcon name="books" /></span>
        <div class="metric-body">
          <div class="metric-label">今日外借</div>
          <div class="metric-value">{{ formatNumber(stats?.summary.today_borrowed) }}<small>册</small></div>
          <div class="metric-meta">外借 {{ formatNumber(stats?.summary.active) }} 册 · {{ formatNumber(stats?.summary.readers_active) }} 位读者</div>
        </div>
      </div>
      <div class="metric-card">
        <span class="metric-icon blue"><AppIcon name="rotate" /></span>
        <div class="metric-body">
          <div class="metric-label">今日归还</div>
          <div class="metric-value">{{ formatNumber(stats?.summary.today_returned) }}<small>册</small></div>
          <div class="metric-meta">遗失 {{ formatNumber(stats?.summary.lost) }} 册</div>
        </div>
      </div>
      <div class="metric-card">
        <span class="metric-icon amber"><AppIcon name="layers" /></span>
        <div class="metric-body">
          <div class="metric-label">外借册数</div>
          <div class="metric-value">{{ formatNumber(stats?.summary.active) }}<small>册</small></div>
          <div class="metric-meta">{{ formatNumber(stats?.summary.readers_active) }} 位读者外借</div>
        </div>
      </div>
      <div class="metric-card">
        <span class="metric-icon rose"><AppIcon name="alert" /></span>
        <div class="metric-body">
          <div class="metric-label">逾期未还</div>
          <div class="metric-value" :class="{ 'is-danger': (stats?.summary.overdue || 0) > 0 }">{{ formatNumber(stats?.summary.overdue) }}<small>册</small></div>
          <div class="metric-meta" :class="{ 'is-danger': (stats?.summary.overdue || 0) > 0 }">
            {{ (stats?.summary.overdue || 0) > 0 ? '需要跟进处理' : '当前无逾期' }}
          </div>
        </div>
      </div>
    </div>

    <div class="circ-work">
      <article class="card desk-card">
        <div class="card-head">
          <div>
            <div class="card-title">借还办理</div>
            <div class="card-sub">识别读者后扫描条码，支持扫码枪连续录入</div>
          </div>
          <div class="mode-switch" role="group" aria-label="借还模式">
            <button type="button" :disabled="borrowBusy" :class="{ 'is-active': mode === 'borrow' }" @click="switchMode('borrow')">
              <AppIcon name="books" />外借<kbd>B</kbd>
            </button>
            <button type="button" :disabled="borrowBusy" :class="{ 'is-active': mode === 'return' }" @click="switchMode('return')">
              <AppIcon name="rotate" />归还<kbd>R</kbd>
            </button>
          </div>
        </div>

        <div class="desk-body">
          <template v-if="mode === 'borrow'">
            <div class="desk-step">
              <div class="step-head">
                <span class="step-index">1</span>
                <strong>识别读者</strong>
                <span>输入借书证号后回车，或直接扫描读者证</span>
              </div>
              <div class="scan-field">
                <div class="input-icon">
                  <input
                    id="reader-card"
                    v-model="readerCard"
                    class="input"
                    placeholder="借书证号"
                    autocomplete="off"
                    :disabled="readerLoading || borrowBusy"
                    @keydown.enter.prevent="identifyReader"
                  >
                  <AppIcon name="scan" />
                </div>
                <button class="btn btn-outline" type="button" :disabled="readerLoading || borrowBusy" @click="identifyReader">
                  {{ readerLoading ? '查询中…' : '识别' }}
                </button>
              </div>

              <div v-if="readerError" class="lookup-alert" role="alert">
                <AppIcon name="alert" /><span>{{ readerError }}</span>
              </div>

              <div v-if="reader" class="lookup-panel">
                <span class="avatar" :class="avatarClass(reader.id)">{{ reader.name.slice(-2) }}</span>
                <div class="lookup-main">
                  <strong>{{ reader.name }} <span class="badge badge-neutral no-dot">{{ reader.reader_type_label }}</span></strong>
                  <span>{{ reader.card_no }} · {{ reader.class_name || reader.grade || '未分班' }}</span>
                  <div class="lookup-alert" v-if="reader.status !== 'ACTIVE'">
                    <AppIcon name="alert" /><span>{{ reader.status_label }}，不能借书</span>
                  </div>
                  <div class="lookup-alert" v-else-if="reader.overdue_count">
                    <AppIcon name="alert" /><span>有 {{ reader.overdue_count }} 册逾期，须先归还</span>
                  </div>
                  <div class="lookup-alert" v-else-if="reader.fines.enabled && reader.fines.unpaid_count">
                    <AppIcon name="receipt" /><span>未缴逾期罚款 {{ formatMoney(reader.fines.unpaid) }}</span>
                  </div>
                </div>
                <div class="lookup-side">
                  <strong>{{ reader.active_count }} / {{ reader.rules.max_items }}</strong>
                  <span>外借 / 上限</span>
                  <div class="quota-track">
                    <div class="quota-fill" :class="{ 'is-full': reader.active_count >= reader.rules.max_items }" :style="{ width: `${Math.min(100, (reader.active_count / Math.max(1, reader.rules.max_items)) * 100)}%` }" />
                  </div>
                </div>
              </div>
            </div>

            <div class="desk-step">
              <div class="step-head">
                <span class="step-index">2</span>
                <strong>扫描图书</strong>
                <span>扫描条码即加入待借清单，可连续操作</span>
              </div>
              <div class="scan-field">
                <div class="input-icon">
                  <input
                    ref="scanInput"
                    v-model="scanValue"
                    class="input"
                    placeholder="图书条码"
                    autocomplete="off"
                    :disabled="!reader || scanning || readerLoading || borrowBusy"
                    @keydown.enter.prevent="addBarcode"
                  >
                  <AppIcon name="barcode" />
                </div>
                <button class="btn btn-outline" type="button" :disabled="!reader || scanning || readerLoading || borrowBusy || !scanValue.trim()" @click="addBarcode">
                  <AppIcon name="plus" />添加
                </button>
              </div>

              <div v-if="pendingItems.length" class="borrow-list">
                <div v-for="(item, index) in pendingItems" :key="item.holding_id" class="borrow-item">
                  <BookCover :title="item.title || item.barcode || '未知题名'" />
                  <div class="borrow-info">
                    <strong>{{ item.title || '未知题名' }}</strong>
                    <span>{{ item.barcode }} · {{ item.call_no || '未编索书号' }} · {{ item.location || item.shelf_no ? holdingLocationLabel(item.location, item.shelf_no) : '未设地点' }}</span>
                  </div>
                  <button class="icon-btn remove" type="button" title="移出待借清单" aria-label="移出待借清单" :disabled="borrowBusy" @click="removePending(index)">
                    <AppIcon name="x" />
                  </button>
                </div>
              </div>
              <div v-else class="desk-empty">
                <AppIcon name="barcode" />
                当前没有待借图书。识别读者后扫描图书条码。
              </div>
            </div>

            <div class="desk-submit">
              <div>
                <strong>待借 {{ pendingItems.length }} 册<template v-if="reader"> · {{ reader.name }}</template></strong>
                <p v-if="limitReached" class="field-note is-error">待借册数超出该读者的借阅上限，请调整后再办理。</p>
                <p v-else-if="reader">借期 {{ reader.rules.loan_days }} 天，最多续借 {{ reader.rules.max_renew }} 次。应还日期以办理结果为准。</p>
                <p v-else>请先识别读者。</p>
              </div>
              <button
                class="btn btn-primary"
                type="button"
                :disabled="!canWrite || !reader || reader.status !== 'ACTIVE' || !pendingItems.length || borrowBusy || readerLoading || scanning || limitReached"
                @click="confirmBorrow"
              >
                <AppIcon name="check" />{{ borrowBusy ? '外借中…' : '确认外借' }}
              </button>
            </div>
          </template>

          <template v-else>
            <div class="desk-step">
              <div class="step-head">
                <span class="step-index">1</span>
                <strong>扫描归还</strong>
                <span>扫描条码后回车，系统自动结算逾期罚款与预约队列</span>
              </div>
              <div class="scan-field">
                <div class="input-icon">
                  <input
                    ref="returnInput"
                    v-model="returnValue"
                    class="input"
                    placeholder="图书条码"
                    autocomplete="off"
                    :disabled="returning"
                    @keydown.enter.prevent="submitReturn"
                  >
                  <AppIcon name="barcode" />
                </div>
                <button class="btn btn-primary" type="button" :disabled="!canWrite || returning || !returnValue.trim()" @click="submitReturn">
                  <AppIcon name="rotate" />{{ returning ? '处理中…' : '确认归还' }}
                </button>
              </div>
              <div v-if="returnError" class="lookup-alert" role="alert">
                <AppIcon name="alert" /><span>{{ returnError }}</span>
              </div>
            </div>

            <div v-if="returnResult" class="return-result" :class="returnResult.overdue ? 'is-overdue' : 'is-ok'">
              <div class="return-result-head">
                <AppIcon :name="returnResult.overdue ? 'alert' : 'check-circle'" />
                {{ returnResult.message }}
              </div>
              <div class="rr-meta">
                <span>读者：{{ returnResult.loan.reader_name || returnResult.loan.reader_card_no || '—' }}</span>
                <span>条码：{{ returnResult.loan.barcode || '—' }}</span>
                <span v-if="returnResult.loan.fine_amount > 0">罚款：{{ formatMoney(returnResult.loan.fine_amount) }}（{{ returnResult.loan.fine_status_label || '未缴' }}）</span>
              </div>
            </div>
            <div v-else class="desk-empty">
              <AppIcon name="rotate" />
              扫描待归还的图书。归还无需先识别读者。
            </div>

            <div v-if="recentReturns.length" class="desk-step">
              <div class="step-head">
                <strong>本次归还</strong>
                <span>最近 {{ recentReturns.length }} 册</span>
              </div>
              <div class="borrow-list">
                <div v-for="(row, index) in recentReturns" :key="`${row.barcode}-${index}`" class="borrow-item">
                  <span class="metric-icon"><AppIcon name="check" /></span>
                  <div class="borrow-info">
                    <strong>{{ row.title || '未知题名' }}</strong>
                    <span>{{ row.barcode }} · {{ row.reader || '—' }}</span>
                  </div>
                </div>
              </div>
            </div>
          </template>
        </div>
      </article>

      <article class="card">
        <div class="card-head">
          <div>
            <div class="card-title">当前外借</div>
            <div class="card-sub">按应还日期排序 · 可续借、归还或按遗失处理</div>
          </div>
          <div class="loan-filter" role="group" aria-label="外借筛选">
            <button type="button" :class="{ 'is-active': !previewOverdueOnly }" @click="previewOverdueOnly = false">全部</button>
            <button type="button" :class="{ 'is-active': previewOverdueOnly }" @click="previewOverdueOnly = true">逾期</button>
          </div>
        </div>
        <div class="active-loan-list">
          <div v-if="previewLoading" class="desk-empty"><span class="spinner" />正在读取外借记录…</div>
          <template v-else-if="preview.length">
            <div v-for="loan in preview" :key="loan.id" class="active-loan">
              <BookCover :title="loan.title || '未知题名'" />
              <div class="active-loan-info">
                <strong>{{ loan.title || '未知题名' }}</strong>
                <span>{{ loan.reader_name || loan.reader_card_no }} · {{ loan.reader_class || '未分班' }}</span>
                <span>{{ loan.barcode }} · 续借 {{ loan.renew_count }} 次</span>
              </div>
              <div class="active-loan-due" :class="{ 'is-overdue': loan.overdue }">
                <strong>{{ loan.due_on }}</strong>
                <span>{{ loan.overdue ? `逾期 ${loan.days_overdue} 天` : '应还日期' }}</span>
              </div>
              <div v-if="canWrite" class="active-loan-actions">
                <button class="icon-btn" type="button" title="续借" aria-label="续借" :disabled="actionBusy || loan.overdue" @click="renew(loan)">
                  <AppIcon name="refresh" />
                </button>
                <button class="icon-btn" type="button" title="归还" aria-label="归还" :disabled="actionBusy" @click="returnTarget = loan">
                  <AppIcon name="rotate" />
                </button>
              </div>
            </div>
          </template>
          <div v-else class="desk-empty">
            <AppIcon name="check-circle" />
            {{ previewOverdueOnly ? '当前没有逾期记录。' : '当前没有外借图书。' }}
          </div>
        </div>
        <div class="loans-foot">
          <span>共 <strong>{{ previewTotal }}</strong> 册{{ previewOverdueOnly ? '逾期' : '外借' }}</span>
          <RouterLink class="text-link" :to="{ path: '/admin/circulation', query: { tab: previewOverdueOnly ? 'overdue' : 'active' } }">
            查看全部 <AppIcon name="chevron-right" />
          </RouterLink>
        </div>
      </article>
    </div>

    <article class="data-card">
      <div class="tabs card-tabs" role="tablist" aria-label="借阅记录分类">
        <button
          v-for="item in tabDefinitions"
          :key="item.key"
          class="tab"
          type="button"
          role="tab"
          :aria-selected="tab === item.key"
          :class="{ 'is-active': tab === item.key }"
          @click="chooseTab(item.key)"
        >
          {{ item.label }}
          <span v-if="countsFor(item.key) !== null" class="count">{{ formatNumber(countsFor(item.key)) }}</span>
        </button>
      </div>

      <div class="toolbar">
        <div class="input-icon search-field">
          <input
            v-model="q"
            class="input"
            placeholder="搜索题名、读者、借书证号或条码"            autocomplete="off"
            @input="onSearchInput"
            @keydown.enter.prevent="list.page = 1; loadList()"
          >
          <AppIcon name="search" />
        </div>
        <select v-if="tab === 'fines'" v-model="fineFilter" class="select" aria-label="逾期罚款状态">
          <option value="unpaid">未缴</option>
          <option value="paid">已收</option>
          <option value="waived">已免除</option>
          <option value="all">全部状态</option>
        </select>
        <span class="toolbar-spacer" />
        <template v-if="tab === 'overdue'">
          <button class="btn btn-outline btn-sm" type="button" @click="printOverdue">
            <AppIcon name="printer" />打印催还单
          </button>
          <button class="btn btn-outline btn-sm" type="button" @click="exportOverdue">
            <AppIcon name="download" />导出 Excel
          </button>
        </template>
        <span class="toolbar-count">
          <template v-if="tab === 'fines' && fineSummary">
            未缴 <strong>{{ fineSummary.unpaid_count }}</strong> 笔 · {{ formatMoney(fineSummary.unpaid_amount) }}
          </template>
          <template v-else>共 <strong>{{ formatNumber(list.total) }}</strong> 条</template>
        </span>
      </div>

      <div class="table-scroll-hint">左右滑动查看完整表格与操作</div>
      <PagerBar :page="list.page" :page-size="list.pageSize" :total="list.total" noun="条记录" @update:page="setPage" />
      <div class="table-wrap">
        <table class="table is-striped">
          <thead>
            <tr>
              <ThSort label="图书" sort-key="title" :current="list.sortKey" :order="list.sortOrder" @sort="setSort" />
              <ThSort label="读者" sort-key="reader_name" :current="list.sortKey" :order="list.sortOrder" @sort="setSort" />
              <th v-if="tab === 'fines'">外借 / 归还</th>
              <ThSort v-else class="col-hide-md" label="外借日期" sort-key="loaned_at"
                      :current="list.sortKey" :order="list.sortOrder" @sort="setSort" />
              <ThSort :label="tab === 'returned' ? '归还日期' : tab === 'fines' ? '罚款' : '应还日期'"
                      :sort-key="tab === 'returned' ? 'returned_at' : tab === 'fines' ? 'fine_amount' : 'due_on'"
                      :current="list.sortKey" :order="list.sortOrder" @sort="setSort" />
              <th>状态</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <TableState v-if="list.loading || list.error || !list.items.length"
                        :colspan="6" :list="list" loading-text="正在读取记录…"
                        :empty="listEmptyText" :retry="loadList" />
            <template v-else>
              <tr v-for="loan in list.items" :key="loan.id">
              <td>
                <div class="record-book-cell">
                  <BookCover :title="loan.title || '未知题名'" />
                  <div class="record-book-text">
                    <div class="cell-main">{{ loan.title || '未知题名' }}</div>
                    <div class="cell-sub">{{ loan.barcode || '无条码' }}</div>
                  </div>
                </div>
              </td>
              <td>
                <div class="reader-inline">
                  <span class="avatar" :class="avatarClass(loan.reader_id)">{{ (loan.reader_name || '读').slice(-2) }}</span>
                  <div class="reader-inline-text">
                    <strong>{{ loan.reader_name || '未知读者' }}</strong>
                    <span>{{ loan.reader_card_no }}<template v-if="loan.reader_class"> · {{ loan.reader_class }}</template></span>
                  </div>
                </div>
              </td>
              <td v-if="tab === 'fines'" class="cell-date">{{ formatDate(loan.loaned_at) }}<br>{{ formatDate(loan.returned_at) || '—' }}</td>
              <td v-else class="cell-date col-hide-md">{{ formatDate(loan.loaned_at) || '—' }}</td>
              <td class="cell-date">
                <template v-if="tab === 'returned'">{{ loan.returned_at ? formatDateTime(loan.returned_at) : '—' }}</template>
                <template v-else-if="tab === 'fines'">{{ formatMoney(loan.fine_amount) }}</template>
                <template v-else>{{ loan.due_on }}</template>
              </td>
              <td>
                <template v-if="tab === 'fines'">
                  <span class="status-text" :class="loan.fine_status === 'UNPAID' ? 'danger' : loan.fine_status === 'PAID' ? 'ok' : 'muted'">
                    <span class="dot" />{{ loan.fine_status_label || '—' }}
                  </span>
                </template>
                <template v-else-if="loan.overdue">
                  <span class="status-text danger"><span class="dot" />逾期 {{ loan.days_overdue }} 天</span>
                </template>
                <template v-else-if="loan.status === 'LOST'">
                  <span class="status-text danger"><span class="dot" />已遗失</span>
                  <div v-if="loan.compensation_amount > 0" class="cell-sub is-danger">赔偿 {{ formatMoney(loan.compensation_amount) }} · {{ loan.compensation_status_label || '未缴' }}</div>
                </template>
                <template v-else-if="loan.status === 'RETURNED'">
                  <span class="status-text ok"><span class="dot" />已归还</span>
                  <div v-if="loan.fine_amount > 0" class="cell-sub" :class="loan.fine_status === 'UNPAID' ? 'is-danger' : ''">
                    罚款 {{ formatMoney(loan.fine_amount) }} · {{ loan.fine_status_label || '未缴' }}
                  </div>
                </template>
                <template v-else>
                  <span class="status-text ok"><span class="dot" />外借</span>
                </template>
              </td>
              <td>
                <div class="row-actions">
                  <template v-if="tab === 'fines'">
                    <button v-if="canWrite && loan.fine_status === 'UNPAID'" class="text-link" type="button" :disabled="actionBusy" @click="openSettle(loan, 'fine')">收款</button>
                    <RouterLink class="text-link" :to="`/admin/readers/${loan.reader_id}`">读者</RouterLink>
                  </template>
                  <template v-else-if="canWrite && (loan.status === 'ACTIVE')">
                    <button class="text-link" type="button" :disabled="actionBusy || loan.overdue" @click="renew(loan)">续借</button>
                    <button class="text-link" type="button" :disabled="actionBusy" @click="returnTarget = loan">归还</button>
                    <button class="text-link" type="button" :disabled="actionBusy" @click="openLost(loan)">遗失</button>
                  </template>
                  <template v-else-if="canWrite && loan.status === 'RETURNED' && loan.fine_amount > 0">
                    <button class="text-link" type="button" :disabled="actionBusy" @click="openSettle(loan, 'fine')">罚款处理</button>
                  </template>
                  <template v-else-if="canWrite && loan.status === 'LOST' && loan.compensation_amount > 0">
                    <button class="text-link" type="button" :disabled="actionBusy" @click="openSettle(loan, 'compensation')">赔偿处理</button>
                  </template>
                  <RouterLink class="text-link" :to="`/admin/readers/${loan.reader_id}`">读者</RouterLink>
                </div>
              </td>
            </tr>
            </template>
          </tbody>
        </table>
      </div>
    </article>

    <ConfirmDialog
      :open="Boolean(returnTarget)"
      title="确认归还"
      :message="returnTarget ? `确认归还《${returnTarget.title || returnTarget.barcode || '未知题名'}》？\n条码：${returnTarget.barcode || '—'}` : ''"
      confirm-text="确认归还"
      :busy="actionBusy"
      @cancel="returnTarget = null"
      @confirm="returnTarget && doReturn(returnTarget)"
    />

    <BaseModal
      :open="Boolean(lostTarget)"
      :busy="actionBusy"
      title="按遗失处理"
      :subtitle="lostTarget ? `《${lostTarget.title || lostTarget.barcode || '未知题名'}》 · ${lostTarget.reader_name || '读者'}` : ''"
      size="sm"
      @close="lostTarget = null"
    >
      <div class="field">
        <label for="lost-amount">赔偿金额</label>
        <input id="lost-amount" v-model="lostForm.amount" class="input" inputmode="decimal" placeholder="0.00">
        <span class="hint">0 表示仅登记遗失，不产生赔偿</span>
      </div>
      <div class="field">
        <label for="lost-note">备注</label>
        <input id="lost-note" v-model="lostForm.note" class="input" maxlength="256" placeholder="遗失说明（可选）">
      </div>
      <label class="check-line" style="margin-top: 14px">
        <input v-model="lostForm.paid" type="checkbox">
        赔偿已当场收取
      </label>
      <template #footer>
        <button class="btn btn-ghost" type="button" :disabled="actionBusy" @click="lostTarget = null">取消</button>
        <button class="btn btn-danger" type="button" :disabled="actionBusy" @click="submitLost">
          {{ actionBusy ? '处理中…' : '确认遗失' }}
        </button>
      </template>
    </BaseModal>

    <BaseModal
      :open="Boolean(settleTarget)"
      :busy="actionBusy"
      :title="settleTarget?.kind === 'fine' ? '罚款处理' : '赔偿处理'"
      size="sm"
      @close="settleTarget = null"
    >
      <template v-if="settleTarget">
        <div class="notice-strip" style="margin-bottom: 14px">
          <AppIcon name="receipt" />
          <span>
            <strong>{{ settleTarget.loan.title || '未知题名' }}</strong><br>
            {{ settleTarget.loan.reader_name || '读者' }} ·
            {{ settleTarget.kind === 'fine' ? `罚款 ${formatMoney(settleTarget.loan.fine_amount)}` : `赔偿 ${formatMoney(settleTarget.loan.compensation_amount)}` }}
          </span>
        </div>
        <div class="field">
          <label>处理结果</label>
          <div class="segmented" role="group" aria-label="处理结果">
            <button type="button" :class="{ 'is-active': settleStatus === 'PAID' }" @click="settleStatus = 'PAID'">
              {{ settleTarget.kind === 'fine' ? '已收款' : '已收赔偿' }}
            </button>
            <button type="button" :class="{ 'is-active': settleStatus === 'WAIVED' }" @click="settleStatus = 'WAIVED'">免除</button>
            <button type="button" :class="{ 'is-active': settleStatus === 'UNPAID' }" @click="settleStatus = 'UNPAID'">撤销为未缴</button>
          </div>
          <span class="hint">处理结果记录当前操作人，可在借阅记录中追溯</span>
        </div>
      </template>
      <template #footer>
        <button class="btn btn-ghost" type="button" :disabled="actionBusy" @click="settleTarget = null">取消</button>
        <button class="btn btn-primary" type="button" :disabled="actionBusy" @click="submitSettle">
          {{ actionBusy ? '处理中…' : '确认' }}
        </button>
      </template>
    </BaseModal>
  </section>
</template>
