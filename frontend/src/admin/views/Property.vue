<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import AppIcon from '../../components/AppIcon.vue'
import PagerBar from '../../components/PagerBar.vue'
import { errorMessage, get, post, downloadPost } from '../../lib/api'
import { holdingStatusLabel } from '../../lib/displayLabels'
import { formatDate, formatMoneyIn, formatNumber } from '../../lib/format'
import { toastError } from '../../lib/toast'
import { libraryState, loadLibraries } from '../../stores/libraries'

interface PropertyRow {
  barcode: string | null
  call_no: string | null
  class_no: string | null
  title: string
  author: string
  publisher: string
  year: string
  pages: string
  binding: string
  price: number
  isbn: string
  volume: string
  location: string
  shelf_no: string
  source: string
  status: string
  registered_at: string
  withdraw_no: string
  withdraw_reason: string
}

interface Summary {
  copies: number
  titles: number
  amount: number
  currency: string
  withdrawn: number
}

interface Preview {
  library: { id: number; name: string } | null
  currency: string
  summary: Summary
  rows: PropertyRow[]
}

const libraryId = computed(() => libraryState.currentId)
const filters = reactive({
  barcodeFrom: '',
  barcodeTo: '',
  dateFrom: '',
  dateTo: '',
  location: '',
  classNo: '',
  status: 'active',
  q: '',
  sort: 'barcode',
})
const locations = ref<string[]>([])
const data = ref<Preview | null>(null)
const loading = ref(true)
const error = ref('')
const exportBusy = ref(false)
const currencyCode = computed(() => data.value?.currency || 'CNY')
const page = ref(1)
const pageSize = 25
const pagedRows = computed(() => {
  const rows = data.value?.rows || []
  const start = (page.value - 1) * pageSize
  return rows.slice(start, start + pageSize)
})
function priceText(value: number): string {
  return formatMoneyIn(currencyCode.value, value)
}

/**
 * 状态点配色：在架绿、遗失红、停流通橙（warn），其余（已注销 / 未知值）灰。
 * 停流通**不是**注销（契约 D14）：它仍是馆藏资产，只是不进 905、
 * 不可借，所以不能跟已注销混成同一个灰点。
 */
function statusTone(status: string): string {
  if (status === 'AVAILABLE') return 'ok'
  if (status === 'LOST') return 'danger'
  if (status === 'SUSPENDED') return 'warn'
  return 'muted'
}

function payload() {
  return {
    library_id: libraryId.value || undefined,
    barcode_from: filters.barcodeFrom.trim() || undefined,
    barcode_to: filters.barcodeTo.trim() || undefined,
    date_from: filters.dateFrom || undefined,
    date_to: filters.dateTo || undefined,
    location: filters.location || undefined,
    class_no: filters.classNo.trim() || undefined,
    status: filters.status,
    q: filters.q.trim() || undefined,
    sort: filters.sort,
  }
}

async function load() {
  if (!libraryId.value) return
  loading.value = true
  error.value = ''
  try {
    data.value = await post<Preview>('/property-register/preview', payload())
    page.value = 1
  } catch (e) {
    error.value = errorMessage(e)
    data.value = null
  } finally {
    loading.value = false
  }
}

async function loadLocations() {
  try {
    locations.value = await get<string[]>('/property-register/locations', {
      library_id: libraryId.value || undefined,
    })
  } catch {
    locations.value = []
  }
}

async function exportRegister() {
  if (exportBusy.value) return
  exportBusy.value = true
  try {
    await downloadPost('/property-register/export', payload(), '财产登记.xlsx')
  } catch (e) {
    toastError(errorMessage(e))
  } finally {
    exportBusy.value = false
  }
}

function escapeHtml(value: string | number | null | undefined): string {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

function printRegister() {
  if (!data.value) return
  const win = window.open('', '_blank', 'width=1100,height=800')
  if (!win) {
    toastError('浏览器拦截了打印窗口，请允许弹出窗口后重试')
    return
  }
  const rows = data.value.rows.map((row, index) => `
    <tr><td>${index + 1}</td><td>${escapeHtml(row.barcode)}</td><td>${escapeHtml(row.call_no)}</td>
    <td>${escapeHtml(row.title)}</td><td>${escapeHtml(row.author)}</td><td>${escapeHtml(row.publisher)}</td>
    <td>${escapeHtml(row.year)}</td><td>${escapeHtml(priceText(row.price))}</td><td>${escapeHtml(row.location)}</td>
    <td>${escapeHtml(row.shelf_no || '—')}</td><td>${escapeHtml(row.registered_at)}</td>
    <td>${holdingStatusLabel(row.status)}</td></tr>`).join('')
  win.document.write(`<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>财产登记</title>
    <style>
      @page { size: A4 landscape; margin: 12mm; }
      body { margin: 0; color: #182420; font-family: "Microsoft YaHei", sans-serif; font-size: 10px; }
      h1 { font-size: 18px; margin: 0 0 4px; } .meta { color: #6b7a73; font-size: 10px; margin-bottom: 10px; }
      table { width: 100%; border-collapse: collapse; }
      th, td { padding: 4px 5px; border-bottom: 1px solid #e5e8e2; text-align: left; }
      th { background: #f4f6f2; font-size: 9px; color: #6b7a73; }
      tr { break-inside: avoid; } .num { text-align: right; font-variant-numeric: tabular-nums; }
      .summary { margin-top: 10px; font-weight: 600; }
    </style></head><body>
    <h1>财产登记清册</h1>
    <div class="meta">${escapeHtml(data.value.library?.name || '')} · 登记日期 ${escapeHtml(filters.dateFrom || '不限')} 至 ${escapeHtml(filters.dateTo || '不限')} · 排序：${filters.sort === 'call_no' ? '索书号' : '条码'}</div>
    <table><thead><tr><th>序号</th><th>登记号</th><th>索书号</th><th>题名</th><th>责任者</th><th>出版者</th><th>出版年</th><th class="num">单价</th><th>馆藏地点</th><th>排架号</th><th>登记日期</th><th>状态</th></tr></thead>
    <tbody>${rows}</tbody></table>
    <div class="summary">合计 ${data.value.summary.copies} 册 · ${data.value.summary.titles} 种 · ${priceText(data.value.summary.amount)}${data.value.summary.withdrawn ? ` · 注销/遗失 ${data.value.summary.withdrawn} 册` : ''}</div>
    </body></html>`)
  win.document.close()
  win.focus()
  window.setTimeout(() => win.print(), 350)
}

watch(libraryId, async () => {
  await loadLocations()
  await load()
})

/* ---------------------------------------------------------------------------
 * 表格区限高：把「多出来的行」关进表格内部的滚动条里，整页不再出滚动条。
 *
 * 为什么实测而不写死 `calc(100dvh - xxx)`：这一页上方有指标卡 + 两条工具条，
 * 它们的高度随窗口宽度换行而变（宽屏工具条 69px，窄屏两行 111px），
 * 内容区 padding 也随断点在 48px / 30px / 2px 之间跳。写死一个常数必然对不上。
 * 这里直接量「表格区顶边距视口顶还有多远」，剩下的高度就是表格能用的高度，
 * 写进 `--property-table-h`，CSS 只管照着用。
 * --------------------------------------------------------------------------- */
const tableWrap = ref<HTMLElement | null>(null)
const tableHeight = ref(0)

function syncTableHeight() {
  const el = tableWrap.value
  if (!el) return
  // 窄屏（手机）不压表格：指标卡与工具条占掉大半屏，再限高表格只剩两三行。
  // 这里直接不写 max-height，整页按正常方式滚动。
  if (window.matchMedia('(max-width: 760px)').matches) {
    tableHeight.value = 0
    return
  }
  const top = el.getBoundingClientRect().top
  // 表格下方留白 = 内容区自己的 padding-bottom（48px / 30px / 2px 随断点变），
  // 直接量出来比写死常数准；再加 4px 缓冲，避免算出 0 余量时出现 1px 抖动。
  const contentEl = el.closest('.content') as HTMLElement | null
  const padBottom = contentEl
    ? parseFloat(getComputedStyle(contentEl).paddingBottom) || 0
    : 0
  const available = window.innerHeight - top - padBottom - 4
  tableHeight.value = Math.max(220, Math.floor(available))
}

let tableObserver: ResizeObserver | null = null
function observeTable() {
  tableObserver?.disconnect()
  tableObserver = null
  const el = tableWrap.value
  if (!el || typeof ResizeObserver === 'undefined') return
  // 表格自身尺寸变了（换页、加载完成）就重新量一次顶部位置
  tableObserver = new ResizeObserver(() => syncTableHeight())
  tableObserver.observe(el)
}

watch(tableWrap, async (el) => {
  if (!el) return
  await nextTick()
  syncTableHeight()
  observeTable()
})

function onViewportChange() {
  syncTableHeight()
}

onMounted(async () => {
  await loadLibraries()
  await Promise.all([loadLocations(), load()])
  await nextTick()
  syncTableHeight()
  window.addEventListener('resize', onViewportChange)
})

onBeforeUnmount(() => {
  tableObserver?.disconnect()
  tableObserver = null
  window.removeEventListener('resize', onViewportChange)
})
</script>

<template>
  <section class="view">
    <div class="metric-grid">
      <div class="metric-card">
        <span class="metric-icon"><AppIcon name="receipt" /></span>
        <div class="metric-body">
          <div class="metric-label">登记册数</div>
          <div class="metric-value">{{ formatNumber(data?.summary.copies) }}<small>册</small></div>
          <div class="metric-meta">{{ formatNumber(data?.summary.titles) }} 种书目</div>
        </div>
      </div>
      <div class="metric-card">
        <span class="metric-icon amber"><AppIcon name="chart" /></span>
        <div class="metric-body">
          <div class="metric-label">码洋合计</div>
          <div class="metric-value">{{ priceText(data?.summary.amount || 0) }}</div>
          <div class="metric-meta">按册单价合计 · {{ currencyCode }}</div>
        </div>
      </div>
      <div class="metric-card">
        <span class="metric-icon neutral"><AppIcon name="archive" /></span>
        <div class="metric-body">
          <div class="metric-label">注销 / 遗失</div>
          <div class="metric-value">{{ formatNumber(data?.summary.withdrawn) }}<small>册</small></div>
          <div class="metric-meta">含注销号与原因</div>
        </div>
      </div>
      <div class="metric-card">
        <span class="metric-icon blue"><AppIcon name="building" /></span>
        <div class="metric-body">
          <div class="metric-label">当前图书库</div>
          <div class="metric-value" style="font-size: 16px">{{ data?.library?.name || '—' }}</div>
          <div class="metric-meta">按册出账，条码即登记号</div>
        </div>
      </div>
    </div>

    <article class="data-card property-card">
      <div class="toolbar">
        <input v-model="filters.barcodeFrom" class="input is-compact" placeholder="起始条码" aria-label="起始条码">
        <span class="faint">至</span>
        <input v-model="filters.barcodeTo" class="input is-compact" placeholder="结束条码" aria-label="结束条码">
        <span class="date-field" :class="{ 'is-empty': !filters.dateFrom }">
          <input v-model="filters.dateFrom" type="date" class="input is-compact" aria-label="登记开始日期">
          <span class="date-field-ph" aria-hidden="true">年/月/日</span>
        </span>
        <span class="faint">至</span>
        <span class="date-field" :class="{ 'is-empty': !filters.dateTo }">
          <input v-model="filters.dateTo" type="date" class="input is-compact" aria-label="登记结束日期">
          <span class="date-field-ph" aria-hidden="true">年/月/日</span>
        </span>
        <select v-model="filters.location" class="select" aria-label="馆藏地点">
          <option value="">全部馆藏地点</option>
          <option v-for="item in locations" :key="item" :value="item">{{ item }}</option>
        </select>
        <input v-model="filters.classNo" class="input is-compact" placeholder="分类号前缀" aria-label="分类号前缀">
        <select v-model="filters.status" class="select" aria-label="馆藏状态">
          <option value="active">在架</option>
          <option value="suspended">停流通</option>
          <option value="withdrawn">注销 / 遗失</option>
          <option value="all">全部状态</option>
        </select>
        <select v-model="filters.sort" class="select" aria-label="排序">
          <option value="barcode">按条码</option>
          <option value="call_no">按索书号</option>
        </select>
        <span class="toolbar-spacer" />
        <button class="btn btn-primary btn-sm" type="button" :disabled="loading" @click="load">
          {{ loading ? '查询中…' : '查询' }}
        </button>
      </div>
      <div class="toolbar">
        <div class="input-icon search-field">
          <input v-model="filters.q" class="input" placeholder="搜索条码或题名" aria-label="搜索" @keydown.enter.prevent="load">
          <AppIcon name="search" />
        </div>
        <span class="toolbar-spacer" />
        <button class="btn btn-outline btn-sm" type="button" :disabled="!data?.rows.length" @click="exportRegister">
          <AppIcon name="download" />导出 Excel
        </button>
        <button class="btn btn-ghost btn-sm" type="button" :disabled="!data?.rows.length" @click="printRegister">
          <AppIcon name="printer" />打印清册
        </button>
        <span class="toolbar-count">共 <strong>{{ formatNumber(data?.summary.copies) }}</strong> 册 · {{ formatNumber(data?.summary.titles) }} 种</span>
      </div>

      <div v-if="error" class="dash-banner" style="margin: 14px 18px" role="alert">
        <AppIcon name="alert" />
        <span>{{ error }}</span>
        <button type="button" @click="load">重试</button>
      </div>

      <div class="table-scroll-hint">左右滑动查看完整表格</div>
      <PagerBar v-if="data?.rows.length" :page="page" :page-size="pageSize" :total="data.rows.length" noun="册" @update:page="page = $event" />
      <div
        ref="tableWrap"
        class="table-wrap property-table-wrap"
        :style="tableHeight ? { maxHeight: `${tableHeight}px` } : undefined"
      >
        <table class="table property-table">
          <thead>
            <tr>
              <th class="center">登记号</th>
              <th class="center">书目</th>
              <th class="center">索书号 / 分类</th>
              <th class="center">出版</th>
              <th class="center num">单价</th>
              <th class="col-hide-md center">馆藏地点</th>
              <th class="col-hide-md center">登记日期</th>
              <th class="center">状态</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="loading"><td colspan="8" class="table-state"><span class="spinner" />正在出账…</td></tr>
            <tr v-else-if="!data?.rows.length"><td colspan="8" class="table-state">当前条件下没有可出账的册</td></tr>
            <template v-else>
              <tr v-for="row in pagedRows" :key="`${row.barcode}-${row.registered_at}`">
                <td class="center num">{{ row.barcode || '—' }}</td>
                <td>
                  <div class="cell-main">{{ row.title || '未知题名' }}</div>
                  <div class="cell-sub">{{ row.author || '佚名' }}<template v-if="row.isbn"> · {{ row.isbn }}</template></div>
                </td>
                <td class="center">
                  <div class="cell-main" style="font-size: 12.5px">{{ row.call_no || '—' }}</div>
                  <div class="cell-sub">{{ row.class_no || '未分类' }}</div>
                </td>
                <td>
                  <div class="cell-main" style="font-size: 12px">{{ row.publisher || '—' }}</div>
                  <div class="cell-sub">{{ row.year || '—' }}<template v-if="row.pages"> · {{ row.pages }}</template></div>
                </td>
                <td class="center num">{{ priceText(row.price) }}</td>
                <td class="col-hide-md">
                  <span class="property-location">
                    <span v-if="row.location" class="property-location-text">{{ row.location }}</span>
                    <span v-if="row.shelf_no" class="property-shelf">{{ row.shelf_no }}</span>
                    <span v-if="!row.location && !row.shelf_no">—</span>
                  </span>
                </td>
                <td class="col-hide-md cell-date num">{{ formatDate(row.registered_at) }}</td>
                <td>
                  <span class="status-text" :class="statusTone(row.status)">
                    <span class="dot" />{{ holdingStatusLabel(row.status) }}
                  </span>
                  <div v-if="row.withdraw_no" class="cell-sub">{{ row.withdraw_no }} · {{ row.withdraw_reason || '—' }}</div>
                </td>
              </tr>
            </template>
          </tbody>
        </table>
      </div>
    </article>
  </section>
</template>

<style scoped>
/* 卡片改成纵向 flex：工具条与提示固定在上，表格区吃掉剩余高度并内部滚动，
   多出来的行关进表格自己的滚动条里，整页不再出现纵向滚动条。
   表格区高度由 JS 实测写入 --property-table-h（见脚本里的 syncTableHeight）。 */
.property-card {
  display: flex;
  flex-direction: column;
  min-height: 0;
}
.property-table-wrap {
  /* 高度由 JS 写入 max-height；这里再兜一个视口上限，防止实测前的首帧溢出 */
  max-height: calc(100dvh - 320px);
  overflow: auto;
  border-top: 1px solid var(--line);
}
/* 表头吸在表格区顶部，向下滚动时列名始终可见 */
.property-table-wrap thead th {
  position: sticky;
  top: 0;
  z-index: 2;
  background: var(--surface);
  box-shadow: inset 0 -1px 0 var(--line);
}
/* 窄屏（手机）：指标卡与工具条占掉大半屏，再压表格就只剩两三行了。
   这里放开限高、让整页正常滚——手机上本来就要靠滚动操作。 */
@media (max-width: 760px) {
  .property-table-wrap { max-height: none; }
  .property-table-wrap thead th { position: static; }
}
.date-field {
  position: relative;
  display: inline-flex;
  flex: none;
  width: 128px;
}
.date-field .input {
  width: 100%;
  height: 38px;
  font-size: 12.5px;
}
.date-field.is-empty .input::-webkit-datetime-edit { color: transparent; }
.date-field.is-empty:focus-within .input::-webkit-datetime-edit { color: var(--ink); }
.date-field-ph {
  position: absolute;
  left: 12px;
  top: 50%;
  transform: translateY(-50%);
  font-size: 12.5px;
  color: var(--faint);
  pointer-events: none;
}
.date-field.is-empty:focus-within .date-field-ph { display: none; }
.date-field:not(.is-empty) .date-field-ph { display: none; }
.property-table th.num,
.property-table td.num { text-align: right; }
.property-table th.center,
.property-table td.center { text-align: center; }
.property-location {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 44px;
  align-items: center;
  gap: 6px;
  width: 100%;
}
.property-location-text {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.property-shelf {
  grid-column: 2;
  width: 44px;
  box-sizing: border-box;
  padding: 0;
  line-height: 22px;
  border: 1px solid var(--line);
  border-radius: 5px;
  background: var(--surface-soft);
  font-size: 11px;
  color: var(--muted);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
  text-align: center;
}
</style>
