<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import AppIcon from '../../components/AppIcon.vue'
import PagerBar from '../../components/PagerBar.vue'
import { download, errorMessage, get } from '../../lib/api'
import { formatDate, formatMoneyIn, formatNumber } from '../../lib/format'
import { toastError } from '../../lib/toast'
import { libraryState, loadLibraries } from '../../stores/libraries'

interface BatchRow {
  batch_no: string
  titles: number
  copies: number
  amount: number
  first_at: string | null
  last_at: string | null
}
interface AccessionSummary {
  library: { id: number; name: string }
  summary: { batches: number; titles: number; copies: number; amount: number }
  batches: BatchRow[]
}
interface AccessionItem {
  id: number
  accession_no: string | null
  barcode: string | null
  record_id: number
  title: string
  call_no: string | null
  price: number | null
  acquired_at: string | null
  source: string | null
  location: string | null
  status: string
}
interface AccessionItems {
  library: { id: number; name: string }
  total: number
  items: AccessionItem[]
}

type Tab = 'summary' | 'items'
const PAGE_SIZE = 50

const libraryId = computed(() => libraryState.currentId)
const today = new Date()
const filters = reactive({
  dateFrom: `${today.getFullYear()}-01-01`,
  dateTo: `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}-${String(today.getDate()).padStart(2, '0')}`,
})
const tab = ref<Tab>('summary')
const summary = ref<AccessionSummary | null>(null)
const summaryLoading = ref(true)
const items = ref<AccessionItems | null>(null)
const itemsLoading = ref(false)
const batchNo = ref('')
const page = ref(1)
const error = ref('')
const exportBusy = ref(false)

const currency = computed(() => 'CNY')
const money = (value: number) => formatMoneyIn(currency.value, value)
const statusLabel = (status: string) => {
  const map: Record<string, string> = {
    AVAILABLE: '在架', SUSPENDED: '停流通', WITHDRAWN: '已注销', LOST: '已遗失',
  }
  return map[status] || '未知状态'
}
const statusTone = (status: string) => {
  if (status === 'AVAILABLE') return 'ok'
  if (status === 'LOST') return 'danger'
  if (status === 'SUSPENDED') return 'warn'
  return 'muted'
}

function baseParams() {
  return {
    library_id: libraryId.value || undefined,
    date_from: filters.dateFrom || undefined,
    date_to: filters.dateTo || undefined,
  }
}

async function loadSummary() {
  summaryLoading.value = true
  error.value = ''
  try {
    summary.value = await get<AccessionSummary>('/accession/summary', baseParams())
  } catch (e) {
    error.value = errorMessage(e)
    summary.value = null
  } finally {
    summaryLoading.value = false
  }
}

async function loadItems() {
  itemsLoading.value = true
  try {
    items.value = await get<AccessionItems>('/accession/items', {
      ...baseParams(),
      batch_no: batchNo.value || undefined,
      limit: PAGE_SIZE,
      offset: (page.value - 1) * PAGE_SIZE,
    })
  } catch (e) {
    toastError(errorMessage(e))
    items.value = null
  } finally {
    itemsLoading.value = false
  }
}

async function search() {
  page.value = 1
  batchNo.value = ''
  await loadSummary()
  if (tab.value === 'items') await loadItems()
}

async function openBatch(row: BatchRow) {
  batchNo.value = row.batch_no === '未分批' ? '' : row.batch_no
  page.value = 1
  tab.value = 'items'
  await loadItems()
}

async function switchTab(next: Tab) {
  tab.value = next
  if (next === 'items' && !items.value) await loadItems()
}

function onPage(next: number) {
  page.value = next
  void loadItems()
}

async function exportLedger() {
  if (exportBusy.value) return
  exportBusy.value = true
  try {
    await download('/accession/export', {
      ...baseParams(),
      batch_no: batchNo.value || undefined,
    }, '登录账.xlsx')
  } catch (e) {
    toastError(errorMessage(e))
  } finally {
    exportBusy.value = false
  }
}

watch(libraryId, async () => {
  page.value = 1
  batchNo.value = ''
  items.value = null
  await loadSummary()
  if (tab.value === 'items') await loadItems()
})

onMounted(async () => {
  await loadLibraries()
  await loadSummary()
})
</script>

<template>
  <section class="view">
    <div class="metric-grid">
      <div class="metric-card">
        <span class="metric-icon"><AppIcon name="receipt" /></span>
        <div class="metric-body">
          <div class="metric-label">入藏批次</div>
          <div class="metric-value">{{ formatNumber(summary?.summary.batches) }}<small>批</small></div>
          <div class="metric-meta">总括登录按批次汇总</div>
        </div>
      </div>
      <div class="metric-card">
        <span class="metric-icon blue"><AppIcon name="books" /></span>
        <div class="metric-body">
          <div class="metric-label">入藏种数</div>
          <div class="metric-value">{{ formatNumber(summary?.summary.titles) }}<small>种</small></div>
          <div class="metric-meta">按书目去重</div>
        </div>
      </div>
      <div class="metric-card">
        <span class="metric-icon amber"><AppIcon name="archive" /></span>
        <div class="metric-body">
          <div class="metric-label">入藏册数</div>
          <div class="metric-value">{{ formatNumber(summary?.summary.copies) }}<small>册</small></div>
          <div class="metric-meta">含已注销 / 遗失 / 停流通</div>
        </div>
      </div>
      <div class="metric-card">
        <span class="metric-icon neutral"><AppIcon name="chart" /></span>
        <div class="metric-body">
          <div class="metric-label">入藏金额</div>
          <div class="metric-value">{{ money(summary?.summary.amount || 0) }}</div>
          <div class="metric-meta">按册单价合计</div>
        </div>
      </div>
    </div>

    <article class="data-card">
      <div class="toolbar">
        <span class="date-field">
          <input v-model="filters.dateFrom" type="date" class="input is-compact" aria-label="入藏开始日期">
        </span>
        <span class="faint">至</span>
        <span class="date-field">
          <input v-model="filters.dateTo" type="date" class="input is-compact" aria-label="入藏结束日期">
        </span>
        <button class="btn btn-primary btn-sm" type="button" :disabled="summaryLoading" @click="search">
          {{ summaryLoading ? '查询中…' : '查询' }}
        </button>
        <span class="toolbar-spacer" />
        <button class="btn btn-outline btn-sm" type="button" :disabled="exportBusy" @click="exportLedger">
          <AppIcon name="download" />{{ exportBusy ? '导出中…' : '导出登录账' }}
        </button>
      </div>

      <div class="tabs card-tabs" role="tablist">
        <button class="tab" type="button" :class="{ 'is-active': tab === 'summary' }" @click="switchTab('summary')">
          总括登录 <span class="count">{{ summary?.batches.length || 0 }}</span>
        </button>
        <button class="tab" type="button" :class="{ 'is-active': tab === 'items' }" @click="switchTab('items')">
          个别登录 <span v-if="items" class="count">{{ formatNumber(items.total) }}</span>
        </button>
      </div>

      <div v-if="error" class="dash-banner" style="margin: 14px 18px" role="alert">
        <AppIcon name="alert" />
        <span>{{ error }}</span>
        <button type="button" @click="loadSummary">重试</button>
      </div>

      <div v-if="tab === 'summary'" class="table-wrap">
        <table class="table">
          <thead>
            <tr>
              <th>批次号</th>
              <th class="num">种数</th>
              <th class="num">册数</th>
              <th class="num">金额</th>
              <th>首次入藏</th>
              <th>最后入藏</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="summaryLoading"><td colspan="6" class="table-state"><span class="spinner" />正在汇总…</td></tr>
            <tr v-else-if="!summary?.batches.length"><td colspan="6" class="table-state">当前条件下没有入藏记录</td></tr>
            <template v-else>
              <tr v-for="row in summary.batches" :key="row.batch_no" class="row-clickable" @click="openBatch(row)">
                <td><span class="cell-main">{{ row.batch_no }}</span></td>
                <td class="num">{{ formatNumber(row.titles) }}</td>
                <td class="num">{{ formatNumber(row.copies) }}</td>
                <td class="num">{{ money(row.amount) }}</td>
                <td class="cell-date">{{ formatDate(row.first_at) || '—' }}</td>
                <td class="cell-date">{{ formatDate(row.last_at) || '—' }}</td>
              </tr>
            </template>
          </tbody>
          <tfoot v-if="summary?.batches.length">
            <tr>
              <td>合计</td>
              <td class="num">{{ formatNumber(summary.summary.titles) }}</td>
              <td class="num">{{ formatNumber(summary.summary.copies) }}</td>
              <td class="num">{{ money(summary.summary.amount) }}</td>
              <td colspan="2" />
            </tr>
          </tfoot>
        </table>
      </div>

      <template v-else>
        <div class="toolbar">
          <span class="toolbar-count">
            {{ batchNo ? `批次 ${batchNo}` : '全部批次' }} · 共
            <strong>{{ formatNumber(items?.total) }}</strong> 册
          </span>
          <span class="toolbar-spacer" />
          <button v-if="batchNo" class="btn btn-ghost btn-sm" type="button" @click="batchNo = ''; page = 1; loadItems()">
            清除批次筛选
          </button>
        </div>
        <PagerBar
          v-if="items?.total"
          :page="page"
          :page-size="PAGE_SIZE"
          :total="items.total"
          noun="册"
          @update:page="onPage"
        />
        <div class="table-wrap">
          <table class="table">
            <thead>
              <tr>
                <th>登录号</th>
                <th>书目</th>
                <th>索书号</th>
                <th class="num">价格</th>
                <th>入藏日期</th>
                <th>来源 / 馆藏地</th>
                <th>状态</th>
              </tr>
            </thead>
            <tbody>
              <tr v-if="itemsLoading"><td colspan="7" class="table-state"><span class="spinner" />正在读取…</td></tr>
              <tr v-else-if="!items?.items.length"><td colspan="7" class="table-state">当前条件下没有个别登录记录</td></tr>
              <template v-else>
                <tr v-for="row in items.items" :key="row.id">
                  <td class="num">{{ row.accession_no || row.barcode || '—' }}</td>
                  <td>
                    <RouterLink class="cell-main" :to="`/admin/records/${row.record_id}`">{{ row.title || '未知题名' }}</RouterLink>
                    <div class="cell-sub">{{ row.barcode || '—' }}</div>
                  </td>
                  <td class="cell-date">{{ row.call_no || '—' }}</td>
                  <td class="num">{{ row.price == null ? '—' : money(row.price) }}</td>
                  <td class="cell-date">{{ formatDate(row.acquired_at) || '—' }}</td>
                  <td>
                    <div class="cell-main" style="font-size: 12.5px">{{ row.source || '—' }}</div>
                    <div class="cell-sub">{{ row.location || '—' }}</div>
                  </td>
                  <td>
                    <span class="status-text" :class="statusTone(row.status)">
                      <span class="dot" />{{ statusLabel(row.status) }}
                    </span>
                  </td>
                </tr>
              </template>
            </tbody>
          </table>
        </div>
      </template>

      <p class="field-note" style="margin: 12px 18px 16px">
        登录账是资产台账：包含已注销、遗失与停流通的册；905 只含可流通册，两者口径不同。
      </p>
    </article>
  </section>
</template>

<style scoped>
.date-field { display: inline-flex; flex: none; width: 128px; }
.date-field .input { width: 100%; height: 38px; font-size: 12.5px; }
.row-clickable { cursor: pointer; }
.row-clickable:hover { background: var(--surface-soft); }
.table th.num, .table td.num { text-align: right; }
</style>
