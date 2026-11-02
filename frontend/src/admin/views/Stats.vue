<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import AppIcon from '../../components/AppIcon.vue'
import { download, errorMessage, get } from '../../lib/api'
import { formatMoneyIn, formatNumber } from '../../lib/format'
import { toastError } from '../../lib/toast'
import { libraryState } from '../../stores/libraries'

interface OverviewRow {
  id: number
  name: string
  title_count: number
  copy_count: number
  titles_with_holding: number
  amount: number
  copy_amount: number
  withdrawn_copy_count: number
}
interface OverviewTotal {
  title_count: number
  copy_count: number
  titles_with_holding: number
  amount: number
  copy_amount: number
  withdrawn_copy_count: number
}
interface Overview {
  currency: string
  libraries: OverviewRow[]
  total: OverviewTotal
}
interface BreakdownRow {
  key: string
  label: string
  title_count: number
  copy_count: number
  amount: number
  copy_amount: number
}
interface Breakdown {
  dim: string
  currency: string
  rows: BreakdownRow[]
  group_count: number
  truncated: boolean
  total: { title_count: number; copy_count: number; amount: number; copy_amount: number }
}
interface WorkloadRow {
  operator: string
  title_count: number
  copy_count: number
  amount: number
  copy_amount: number
}
interface Workload {
  from: string | null
  to: string | null
  currency: string
  rows: WorkloadRow[]
  total: { title_count: number; copy_count: number; amount: number; copy_amount: number; operator_count: number }
}

type Dim = 'classify' | 'location' | 'year' | 'source' | 'withdrawn'

const DIMS: Array<{ key: Dim; label: string; note: string }> = [
  { key: 'classify', label: '中图法大类', note: '按分类号首字母归并' },
  { key: 'location', label: '馆藏地点', note: '在架册所在馆藏地点' },
  { key: 'year', label: '出版年', note: '按出版年份分布' },
  { key: 'source', label: '来源', note: '按入藏来源归集' },
  { key: 'withdrawn', label: '注销', note: '只统计非在架册' },
]

const libraryId = computed(() => libraryState.currentId)
const overview = ref<Overview | null>(null)
const dim = ref<Dim>('classify')
const breakdowns = ref<Partial<Record<Dim, Breakdown>>>({})
const overviewLoading = ref(true)
const dimLoading = ref(false)
const error = ref('')
const exportBusy = ref(false)
const workload = ref<Workload | null>(null)
const workloadLoading = ref(true)
const workloadBusy = ref(false)
const today = new Date()
const range = reactive({
  from: `${today.getFullYear()}-01-01`,
  to: `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}-${String(today.getDate()).padStart(2, '0')}`,
})

const currency = computed(() => overview.value?.currency || 'CNY')
const dimensions = computed(() => DIMS)
const activeBreakdown = computed(() => breakdowns.value[dim.value] || null)
const chartRows = computed(() => (activeBreakdown.value?.rows || []).slice(0, 8))
const maxTitles = computed(() => Math.max(1, ...chartRows.value.map((row) => row.title_count)))
const topGroups = computed(() => activeBreakdown.value?.rows.slice(0, 4) || [])
const libraryName = computed(() => {
  if (!libraryId.value) return '全部图书库'
  return libraryState.items.find((item) => item.id === libraryId.value)?.name || '全部图书库'
})

function money(value: number, code: string | undefined = currency.value): string {
  return formatMoneyIn(code, value)
}

function percent(part: number, total: number): number {
  if (!total) return 0
  return Math.round((part / total) * 1000) / 10
}

function barWidth(value: number): number {
  return Math.max(4, Math.round((value / maxTitles.value) * 100))
}

async function loadOverview() {
  overviewLoading.value = true
  error.value = ''
  try {
    overview.value = await get<Overview>('/stats/overview', {
      library_id: libraryId.value || undefined,
    })
  } catch (e) {
    error.value = errorMessage(e)
    overview.value = null
  } finally {
    overviewLoading.value = false
  }
}

async function loadDim(key: Dim) {
  if (breakdowns.value[key]) return
  dimLoading.value = true
  try {
    breakdowns.value[key] = await get<Breakdown>('/stats/breakdown', {
      dim: key, limit: 200, library_id: libraryId.value || undefined,
    })
  } catch (e) {
    toastError(errorMessage(e))
  } finally {
    dimLoading.value = false
  }
}

async function chooseDim(key: Dim) {
  dim.value = key
  await loadDim(key)
}

async function loadWorkload() {
  workloadLoading.value = true
  try {
    workload.value = await get<Workload>('/stats/workload', {
      from: range.from || undefined,
      to: range.to || undefined,
      library_id: libraryId.value || undefined,
    })
  } catch (e) {
    toastError(errorMessage(e))
    workload.value = null
  } finally {
    workloadLoading.value = false
  }
}

async function exportStats() {
  if (exportBusy.value) return
  exportBusy.value = true
  try {
    await download('/stats/export', {
      dim: dim.value, library_id: libraryId.value || undefined,
    }, '统计分析.xlsx')
  } catch (e) {
    toastError(errorMessage(e))
  } finally {
    exportBusy.value = false
  }
}

async function exportWorkload() {
  if (workloadBusy.value) return
  workloadBusy.value = true
  try {
    await download('/stats/workload/export', {
      from: range.from || undefined,
      to: range.to || undefined,
      library_id: libraryId.value || undefined,
    }, '编目工作量.xlsx')
  } catch (e) {
    toastError(errorMessage(e))
  } finally {
    workloadBusy.value = false
  }
}

function reloadAll() {
  breakdowns.value = {}
  void Promise.all([loadOverview(), loadDim(dim.value)])
  void loadWorkload()
}

watch(libraryId, () => reloadAll())
onMounted(() => {
  void Promise.all([loadOverview(), loadDim('classify'), loadWorkload()])
})
</script>

<template>
  <section class="view stats-view">
    <div v-if="error && !overview" class="card dash-error">
      <span class="error-icon"><AppIcon name="alert" /></span>
      <strong>无法读取统计数据</strong>
      <p>{{ error }}</p>
      <button class="btn btn-primary btn-sm" type="button" @click="reloadAll">重新加载</button>
    </div>

    <template v-else>
      <section class="stats-banner">
        <div class="stats-banner-head">
          <div>
            <span class="stats-eyebrow">COLLECTION REPORT · {{ libraryName }}</span>
            <h2>在架馆藏与编目概览</h2>
            <p>册数与码洋按在架口径统计，注销册单列。非默认币种不计入金额。</p>
          </div>
          <div class="stats-banner-actions">
            <button class="btn btn-outline btn-sm" type="button" :disabled="overviewLoading" @click="reloadAll">
              <AppIcon name="refresh" />刷新
            </button>
            <button class="btn btn-primary btn-sm" type="button" :disabled="exportBusy" @click="exportStats">
              <AppIcon name="download" />{{ exportBusy ? '导出中…' : '导出 Excel' }}
            </button>
          </div>
        </div>
        <div class="stats-kpis">
          <div class="stats-kpi">
            <span>书目种数</span>
            <strong>{{ formatNumber(overview?.total.title_count) }}<small>种</small></strong>
            <em>有馆藏 {{ formatNumber(overview?.total.titles_with_holding) }} 种</em>
          </div>
          <div class="stats-kpi">
            <span>在架册数</span>
            <strong>{{ formatNumber(overview?.total.copy_count) }}<small>册</small></strong>
            <em>不含注销与遗失</em>
          </div>
          <div class="stats-kpi">
            <span>在架册码洋</span>
            <strong class="is-money">{{ money(overview?.total.copy_amount || 0) }}</strong>
            <em>按册累加定价</em>
          </div>
          <div class="stats-kpi">
            <span>种码洋</span>
            <strong class="is-money">{{ money(overview?.total.amount || 0) }}</strong>
            <em>按书目去重</em>
          </div>
          <div class="stats-kpi">
            <span>注销册数</span>
            <strong>{{ formatNumber(overview?.total.withdrawn_copy_count) }}<small>册</small></strong>
            <em>已注销或遗失</em>
          </div>
        </div>
      </section>

      <article class="card stats-overview-card">
        <div class="card-head">
          <div>
            <div class="card-title">分馆总览</div>
            <div class="card-sub">种数、在架册数与码洋。合计随当前筛选变化。</div>
          </div>
          <span class="badge badge-neutral no-dot">{{ overview?.currency || 'CNY' }}</span>
        </div>
        <div v-if="overviewLoading && !overview" class="stats-skeleton">
          <span class="skeleton" style="height: 160px" />
        </div>
        <div v-else-if="overview" class="table-wrap">
          <table class="table stats-table">
            <thead>
              <tr>
                <th>图书库</th>
                <th class="num">种数</th>
                <th class="num">有馆藏种数</th>
                <th class="num">在架册数</th>
                <th class="num">种码洋</th>
                <th class="num">册码洋</th>
                <th class="num">注销册数</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in overview.libraries" :key="row.id">
                <td>
                  <div class="stats-lib-cell">
                    <span class="stats-lib-mark"><AppIcon name="building" :size="15" /></span>
                    <strong>{{ row.name }}</strong>
                  </div>
                </td>
                <td class="num">{{ formatNumber(row.title_count) }}</td>
                <td class="num">{{ formatNumber(row.titles_with_holding) }}</td>
                <td class="num">{{ formatNumber(row.copy_count) }}</td>
                <td class="num">{{ money(row.amount) }}</td>
                <td class="num">{{ money(row.copy_amount) }}</td>
                <td class="num" :class="{ 'is-muted': !row.withdrawn_copy_count }">{{ formatNumber(row.withdrawn_copy_count) }}</td>
              </tr>
              <tr v-if="!overview.libraries.length">
                <td colspan="7" class="table-state">当前筛选下没有馆藏数据</td>
              </tr>
              <tr v-else class="stats-total-row">
                <td>合计</td>
                <td class="num">{{ formatNumber(overview.total.title_count) }}</td>
                <td class="num">{{ formatNumber(overview.total.titles_with_holding) }}</td>
                <td class="num">{{ formatNumber(overview.total.copy_count) }}</td>
                <td class="num">{{ money(overview.total.amount) }}</td>
                <td class="num">{{ money(overview.total.copy_amount) }}</td>
                <td class="num">{{ formatNumber(overview.total.withdrawn_copy_count) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </article>

      <section class="stats-dim-section">
        <div class="stats-section-head">
          <div>
            <h2>馆藏结构</h2>
            <p>选择维度查看分组构成，明细可随统计一并导出。</p>
          </div>
          <div class="stats-dim-tabs" role="tablist" aria-label="统计维度">
            <button
              v-for="item in dimensions"
              :key="item.key"
              class="stats-dim-tab"
              :class="{ 'is-active': dim === item.key }"
              type="button"
              role="tab"
              :aria-selected="dim === item.key"
              @click="chooseDim(item.key)"
            >{{ item.label }}</button>
          </div>
        </div>

        <div class="stats-dim-layout">
          <article class="card stats-chart-card">
            <div class="card-head">
              <div>
                <div class="card-title">{{ dimensions.find((item) => item.key === dim)?.label }}</div>
                <div class="card-sub">{{ dimensions.find((item) => item.key === dim)?.note }} · 按种数排序</div>
              </div>
              <span class="badge badge-neutral no-dot">{{ activeBreakdown?.group_count || 0 }} 组</span>
            </div>
            <div v-if="dimLoading && !activeBreakdown" class="stats-skeleton">
              <span class="skeleton" style="height: 300px" />
            </div>
            <div v-else-if="!chartRows.length" class="empty-card" style="margin: 16px">当前维度没有数据</div>
            <div v-else class="stats-bars">
              <div v-for="(row, index) in chartRows" :key="row.key" class="stats-bar-row">
                <span class="stats-bar-rank">{{ String(index + 1).padStart(2, '0') }}</span>
                <span class="stats-bar-label" :title="row.label">{{ row.label }}</span>
                <div class="stats-bar-track">
                  <span class="stats-bar-fill" :style="{ width: `${barWidth(row.title_count)}%` }" />
                </div>
                <span class="stats-bar-value">{{ formatNumber(row.title_count) }}<small>种</small></span>
              </div>
            </div>
            <div class="stats-chart-foot">
              <span>合计 {{ formatNumber(activeBreakdown?.total.title_count) }} 种 · {{ formatNumber(activeBreakdown?.total.copy_count) }} 册</span>
              <span v-if="activeBreakdown?.truncated">图表显示前 8 组，完整明细见右侧</span>
            </div>
          </article>

          <article class="card stats-detail-card">
            <div class="card-head">
              <div>
                <div class="card-title">分组明细</div>
                <div class="card-sub">{{ overview?.currency || 'CNY' }} · 金额按默认币种累计</div>
              </div>
            </div>
            <div v-if="dimLoading && !activeBreakdown" class="stats-skeleton">
              <span class="skeleton" style="height: 300px" />
            </div>
            <div v-else class="table-wrap stats-detail-wrap">
              <table class="table">
                <thead>
                  <tr><th>分组</th><th class="num">种数</th><th class="num">册数</th><th class="num">册码洋</th><th class="num">册数占比</th></tr>
                </thead>
                <tbody>
                  <tr v-for="row in activeBreakdown?.rows || []" :key="row.key">
                    <td><span class="stats-group-label" :title="row.label">{{ row.label }}</span></td>
                    <td class="num">{{ formatNumber(row.title_count) }}</td>
                    <td class="num">{{ formatNumber(row.copy_count) }}</td>
                    <td class="num">{{ money(row.copy_amount) }}</td>
                    <td class="num">{{ percent(row.copy_count, activeBreakdown?.total.copy_count || 0) }}%</td>
                  </tr>
                  <tr v-if="!activeBreakdown?.rows.length"><td colspan="5" class="table-state">当前维度没有数据</td></tr>
                </tbody>
              </table>
            </div>
            <div v-if="topGroups.length" class="stats-dim-summary">
              <span>占册数最高：<strong>{{ topGroups[0].label }}</strong> {{ percent(topGroups[0].copy_count, activeBreakdown?.total.copy_count || 0) }}%</span>
            </div>
          </article>
        </div>
      </section>

      <article class="card stats-workload-card">
        <div class="card-head">
          <div>
            <div class="card-title">编目工作量</div>
            <div class="card-sub">按编目员聚合，时间以书目创建日期为准。册数只统计在架册。</div>
          </div>
          <div class="stats-workload-tools">
            <label class="stats-date-field">
              <span>从</span>
              <input v-model="range.from" class="input" type="date" />
            </label>
            <label class="stats-date-field">
              <span>至</span>
              <input v-model="range.to" class="input" type="date" />
            </label>
            <button class="btn btn-outline btn-sm" type="button" :disabled="workloadLoading" @click="loadWorkload">
              <AppIcon name="search" />查询
            </button>
            <button class="btn btn-soft btn-sm" type="button" :disabled="workloadBusy" @click="exportWorkload">
              <AppIcon name="download" />{{ workloadBusy ? '导出中…' : '导出' }}
            </button>
          </div>
        </div>
        <div v-if="workloadLoading && !workload" class="stats-skeleton">
          <span class="skeleton" style="height: 160px" />
        </div>
        <div v-else class="table-wrap">
          <table class="table">
            <thead>
              <tr><th>编目员</th><th class="num">种数</th><th class="num">在架册数</th><th class="num">种码洋</th><th class="num">册码洋</th><th class="num">种数占比</th></tr>
            </thead>
            <tbody>
              <tr v-for="row in workload?.rows || []" :key="row.operator">
                <td>
                  <div class="stats-operator">
                    <span class="stats-operator-mark">{{ row.operator.slice(0, 1) }}</span>
                    <strong>{{ row.operator }}</strong>
                  </div>
                </td>
                <td class="num">{{ formatNumber(row.title_count) }}</td>
                <td class="num">{{ formatNumber(row.copy_count) }}</td>
                <td class="num">{{ money(row.amount, workload?.currency) }}</td>
                <td class="num">{{ money(row.copy_amount, workload?.currency) }}</td>
                <td class="num">{{ percent(row.title_count, workload?.total.title_count || 0) }}%</td>
              </tr>
              <tr v-if="!workload?.rows.length"><td colspan="6" class="table-state">所选区间内没有编目记录</td></tr>
              <tr v-else class="stats-total-row">
                <td>合计 · {{ workload?.total.operator_count }} 位编目员</td>
                <td class="num">{{ formatNumber(workload?.total.title_count) }}</td>
                <td class="num">{{ formatNumber(workload?.total.copy_count) }}</td>
                <td class="num">{{ money(workload?.total.amount || 0, workload?.currency) }}</td>
                <td class="num">{{ money(workload?.total.copy_amount || 0, workload?.currency) }}</td>
                <td class="num">100%</td>
              </tr>
            </tbody>
          </table>
        </div>
      </article>

      <p class="stats-footnote">
        统计口径：种数为书目记录数，册数为在架馆藏数，码洋只累计默认币种。注销册与遗失册不计入在架册数，单独列示。
      </p>
    </template>
  </section>
</template>
