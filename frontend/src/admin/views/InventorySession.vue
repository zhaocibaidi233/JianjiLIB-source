<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import AppIcon from '../../components/AppIcon.vue'
import BaseModal from '../../components/BaseModal.vue'
import PagerBar from '../../components/PagerBar.vue'
import { can } from '../../lib/auth'
import { download, errorMessage, get, post } from '../../lib/api'
import { usePagedList } from '../../lib/usePagedList'
import { holdingStatusLabel } from '../../lib/displayLabels'
import { holdingLocationLabel } from '../../lib/displayLabels'
import { formatDateTime, formatNumber } from '../../lib/format'
import { toast, toastError } from '../../lib/toast'

type InvTab = 'missing' | 'on_loan' | 'problems'

interface InvStats {
  expected: number
  found: number
  missing: number
  on_loan: number
  unknown: number
  other_library: number
  withdrawn: number
  on_loan_scanned: number
  duplicate: number
  problems: number
  scans: number
  progress: number
}

interface InvSession {
  id: number
  library_id: number
  name: string
  status: 'OPEN' | 'CLOSED'
  status_label: string
  expected_count: number
  started_at: string | null
  started_by: string | null
  closed_at: string | null
  closed_by: string | null
  note: string | null
  stats: InvStats
  truncated?: Record<string, number> | null
}

interface Scan {
  id: number
  barcode: string
  result: string
  result_label: string
  holding_id: number | null
  title: string | null
  call_no: string | null
  location: string | null
  reader_name: string | null
  scanned_at: string | null
  scanned_by: string | null
  message: string
}

interface SessionDetail extends InvSession {
  recent: Scan[]
}

interface MissingRow {
  holding_id: number
  record_id: number
  barcode: string | null
  title: string | null
  call_no: string | null
  location: string | null
  shelf_no: string
  status: string
}

interface OnLoanRow {
  loan_id: number
  holding_id: number
  record_id: number
  barcode: string | null
  title: string | null
  reader_id: number
  reader_name: string | null
  reader_card_no: string | null
  reader_class: string | null
  due_on: string
  days_overdue: number
}

type InvRow = MissingRow | OnLoanRow | Scan

interface ListData {
  total: number
  page: number
  page_size: number
  items: InvRow[]
}

const route = useRoute()
const sessionId = computed(() => Number(route.params.id))
const canWrite = computed(() => can('circulation', 'write'))
const session = ref<SessionDetail | null>(null)
const loading = ref(true)
const error = ref('')
const scanValue = ref('')
const scanning = ref(false)
const lastScan = ref<Scan | null>(null)
const scanInput = ref<HTMLInputElement | null>(null)
const tab = ref<InvTab>('missing')
const q = ref('')
const { list, load: loadList, setPage, onSearchInput } = usePagedList<InvRow>({
  fetch: ({ page, pageSize }) => get<ListData>(`/inventory/sessions/${sessionId.value}/${tab.value}`, {
    page,
    page_size: pageSize,
    q: q.value.trim() || undefined,
  }),
})
const actionBusy = ref(false)
const closeOpen = ref(false)
const closeNote = ref('')
const closeError = ref('')

const isOpen = computed(() => session.value?.status === 'OPEN')
const progress = computed(() => Math.max(0, Math.min(100, Number(session.value?.stats.progress || 0))))
const truncatedSummary = computed(() => {
  const truncated = session.value?.truncated
  if (!truncated) return ''
  const labels: Record<string, string> = {
    missing: '漏盘', on_loan: '外借未归', problems: '异常扫描',
  }
  const parts = Object.entries(labels)
    .filter(([key]) => Number(truncated[key] || 0) > 0)
    .map(([key, label]) => `${label} ${formatNumber(Number(truncated[key]))} 条`)
  if (!parts.length) return ''
  return `本次盘点清单超过 5000 条，页面与导出只保留前 5000 条；总数：${parts.join('、')}。请分批处理或直接查看数据库。`
})
const tabs = computed(() => [
  { key: 'missing' as InvTab, label: '漏盘', count: session.value?.stats.missing || 0 },
  { key: 'on_loan' as InvTab, label: '外借未归', count: session.value?.stats.on_loan || 0 },
  { key: 'problems' as InvTab, label: '异常扫描', count: session.value?.stats.problems || 0 },
])
const exportable = computed(() => tab.value === 'missing' || tab.value === 'on_loan')
const missingRows = computed(() => (tab.value === 'missing' ? (list.items as MissingRow[]) : []))
const onLoanRows = computed(() => (tab.value === 'on_loan' ? (list.items as OnLoanRow[]) : []))
const problemRows = computed(() => (tab.value === 'problems' ? (list.items as Scan[]) : []))

async function loadSession() {
  const data = await get<SessionDetail>(`/inventory/sessions/${sessionId.value}`)
  session.value = data
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    await loadSession()
    await loadList()
    if (isOpen.value) await nextTick(() => scanInput.value?.focus())
  } catch (e) {
    error.value = errorMessage(e)
  } finally {
    loading.value = false
  }
}

async function doScan() {
  const barcode = scanValue.value.trim()
  if (!barcode || scanning.value || !isOpen.value) return
  scanning.value = true
  try {
    const data = await post<{ scan: Scan; session: InvSession; message: string }>(
      `/inventory/sessions/${sessionId.value}/scan`,
      { barcode },
    )
    if (session.value) {
      Object.assign(session.value, data.session)
      session.value.recent = [data.scan, ...(session.value.recent || [])].slice(0, 20)
    }
    lastScan.value = data.scan
    scanValue.value = ''
    toast(data.message, data.scan.result === 'OK' ? 'success' : 'info')
    await loadList()
  } catch (e) {
    toastError(errorMessage(e))
  } finally {
    scanning.value = false
    await nextTick()
    scanInput.value?.focus()
  }
}

function resultTone(result: string): string {
  if (result === 'OK') return 'ok'
  if (result === 'DUPLICATE') return 'muted'
  if (result === 'ON_LOAN') return 'warn'
  return 'danger'
}

function chooseTab(next: InvTab) {
  if (tab.value === next) return
  tab.value = next
  q.value = ''
  list.page = 1
  void loadList()
}

function openClose() {
  closeNote.value = session.value?.note || ''
  closeError.value = ''
  closeOpen.value = true
}

async function submitClose() {
  if (actionBusy.value) return
  actionBusy.value = true
  closeError.value = ''
  try {
    const data = await post<{ session: InvSession; message: string }>(
      `/inventory/sessions/${sessionId.value}/close`,
      { note: closeNote.value.trim() || undefined },
    )
    if (session.value) Object.assign(session.value, data.session)
    closeOpen.value = false
    toast(data.message)
    await load()
  } catch (e) {
    closeError.value = errorMessage(e)
  } finally {
    actionBusy.value = false
  }
}

async function exportList() {
  if (!exportable.value) return
  try {
    await download(`/inventory/sessions/${sessionId.value}/export`, { kind: tab.value },
      tab.value === 'missing' ? '盘点漏盘清单.xlsx' : '盘点外借未归清单.xlsx')
  } catch (e) {
    toastError(errorMessage(e))
  }
}

watch(
  () => route.params.id,
  () => void load(),
)

onMounted(load)
</script>

<template>
  <section class="view">
    <div class="page-tools">
      <RouterLink class="text-link" to="/admin/inventory">
        <AppIcon name="arrow-left" />盘点记录
      </RouterLink>
      <span class="spacer" />
      <button v-if="isOpen && canWrite" class="btn btn-outline" type="button" :disabled="actionBusy" @click="openClose">
        结束盘点
      </button>
      <button v-if="session && exportable" class="btn btn-outline" type="button" @click="exportList">
        <AppIcon name="download" />导出当前清单
      </button>
    </div>

    <div v-if="loading" class="card" aria-busy="true">
      <div class="skeleton" style="height: 120px; margin: 16px" />
      <div class="skeleton" style="height: 320px; margin: 16px" />
    </div>

    <div v-else-if="error" class="card dash-error">
      <span class="error-icon"><AppIcon name="alert" /></span>
      <strong>无法读取盘点</strong>
      <p>{{ error }}</p>
      <button class="btn btn-primary btn-sm" type="button" @click="load">重新加载</button>
    </div>

    <template v-else-if="session">
      <article class="card inv-head">
        <div class="inv-head-main">
          <div class="inv-title">
            <h2>{{ session.name }}</h2>
            <span class="status-text" :class="isOpen ? 'warn' : 'ok'"><span class="dot" />{{ session.status_label }}</span>
          </div>
          <div class="inv-meta">
            <span>开始 {{ formatDateTime(session.started_at) }} · {{ session.started_by || '馆员' }}</span>
            <span v-if="session.closed_at">结束 {{ formatDateTime(session.closed_at) }} · {{ session.closed_by || '馆员' }}</span>
            <span v-if="session.note">{{ session.note }}</span>
          </div>
        </div>
        <div class="inv-progress">
          <div class="inv-progress-head"><span>扫描进度</span><strong>{{ progress.toFixed(1) }}%</strong></div>
          <div class="inv-track"><span :style="{ width: `${progress}%` }" /></div>
          <div class="inv-progress-foot">已找到 {{ formatNumber(session.stats.found) }} / 应盘 {{ formatNumber(session.stats.expected) }} 册</div>
        </div>
      </article>

      <div v-if="truncatedSummary" class="inv-truncated" role="status">
        <AppIcon name="alert" />
        <span>{{ truncatedSummary }}</span>
      </div>

      <div class="metric-grid">
        <div class="metric-card">
          <span class="metric-icon"><AppIcon name="layers" /></span>
          <div class="metric-body">
            <div class="metric-label">应盘册数</div>
            <div class="metric-value">{{ formatNumber(session.stats.expected) }}<small>册</small></div>
            <div class="metric-meta">建盘时在架馆藏快照</div>
          </div>
        </div>
        <div class="metric-card">
          <span class="metric-icon"><AppIcon name="check-circle" /></span>
          <div class="metric-body">
            <div class="metric-label">已找到</div>
            <div class="metric-value">{{ formatNumber(session.stats.found) }}<small>册</small></div>
            <div class="metric-meta">含系统显示为外借的册</div>
          </div>
        </div>
        <div class="metric-card">
          <span class="metric-icon amber"><AppIcon name="alert" /></span>
          <div class="metric-body">
            <div class="metric-label">漏盘</div>
            <div class="metric-value">{{ formatNumber(session.stats.missing) }}<small>册</small></div>
            <div class="metric-meta">需人工核对是否乱架或遗失</div>
          </div>
        </div>
        <div class="metric-card">
          <span class="metric-icon blue"><AppIcon name="clock" /></span>
          <div class="metric-body">
            <div class="metric-label">外借未归</div>
            <div class="metric-value">{{ formatNumber(session.stats.on_loan) }}<small>册</small></div>
            <div class="metric-meta">仍在读者手中，不计入漏盘</div>
          </div>
        </div>
      </div>

      <div class="inv-work">
        <article class="card inv-scan-card">
          <div class="card-head">
            <div>
              <div class="card-title">{{ isOpen ? '扫描条码' : '盘点已结束' }}</div>
              <div class="card-sub">{{ isOpen ? '扫码枪连续扫描，回车即提交' : '盘点已结束，清单可继续查看与导出' }}</div>
            </div>
            <span v-if="isOpen" class="badge badge-primary no-dot">进行中</span>
          </div>
          <div class="card-body">
            <template v-if="isOpen">
              <div class="scan-field">
                <div class="input-icon">
                  <input
                    ref="scanInput"
                    v-model="scanValue"
                    class="input"
                    placeholder="扫描或输入图书条码"
                    autocomplete="off"
                    :disabled="!canWrite || scanning"
                    @keydown.enter.prevent="doScan"
                  >
                  <AppIcon name="barcode" />
                </div>
                <button class="btn btn-primary" type="button" :disabled="!canWrite || scanning || !scanValue.trim()" @click="doScan">
                  {{ scanning ? '提交中…' : '提交扫描' }}
                </button>
              </div>
              <div v-if="lastScan" class="scan-result" :class="resultTone(lastScan.result)">
                <div class="scan-result-head">
                  <span class="status-text" :class="resultTone(lastScan.result)">
                    <span class="dot" />{{ lastScan.result_label }}
                  </span>
                  <strong>{{ lastScan.barcode }}</strong>
                  <span v-if="lastScan.title">· {{ lastScan.title }}</span>
                </div>
                <p>{{ lastScan.message }}</p>
                <div v-if="lastScan.reader_name" class="rr-meta">借阅人：{{ lastScan.reader_name }}</div>
              </div>
              <div v-else class="desk-empty">
                <AppIcon name="scan" />扫描第一册图书后，此处显示判定结果。
              </div>
            </template>
            <div v-else class="empty-card">
              <AppIcon name="check-circle" />
              本次盘点已结束，统计与清单已保存为快照。
            </div>

            <div v-if="session.recent.length" class="recent-scans">
              <div class="drawer-section-title" style="margin-bottom: 8px">最近扫描</div>
              <div v-for="scan in session.recent.slice(0, 10)" :key="scan.id" class="recent-scan-row">
                <span class="status-text" :class="resultTone(scan.result)"><span class="dot" />{{ scan.result_label }}</span>
                <strong>{{ scan.barcode }}</strong>
                <span class="faint">{{ scan.title || '—' }}</span>
                <span class="recent-scan-time">{{ formatDateTime(scan.scanned_at) }}</span>
              </div>
            </div>
          </div>
        </article>

        <article class="card">
          <div class="card-head">
            <div>
              <div class="card-title">扫描统计</div>
              <div class="card-sub">异常结果需要逐册核对</div>
            </div>
          </div>
          <div class="card-body">
            <div class="inv-stat-list">
              <div class="inv-stat-row"><span>正常扫描</span><strong>{{ formatNumber(session.stats.found) }}</strong></div>
              <div class="inv-stat-row"><span>重复扫描</span><strong>{{ formatNumber(session.stats.duplicate) }}</strong></div>
              <div class="inv-stat-row"><span>系统显示为外借</span><strong>{{ formatNumber(session.stats.on_loan_scanned) }}</strong></div>
              <div class="inv-stat-row"><span>已注销 / 遗失</span><strong>{{ formatNumber(session.stats.withdrawn) }}</strong></div>
              <div class="inv-stat-row"><span>其他图书库</span><strong>{{ formatNumber(session.stats.other_library) }}</strong></div>
              <div class="inv-stat-row"><span>无此条码</span><strong>{{ formatNumber(session.stats.unknown) }}</strong></div>
              <div class="inv-stat-row"><span>扫描总次数</span><strong>{{ formatNumber(session.stats.scans) }}</strong></div>
            </div>
            <p class="field-note" style="margin-top: 12px">结束盘点不注销馆藏，漏盘与外借未归均以清单保留。</p>
          </div>
        </article>
      </div>

      <article class="data-card">
        <div class="tabs card-tabs" role="tablist" aria-label="盘点清单">
          <button
            v-for="item in tabs"
            :key="item.key"
            class="tab"
            type="button"
            :class="{ 'is-active': tab === item.key }"
            :aria-selected="tab === item.key"
            @click="chooseTab(item.key)"
          >
            {{ item.label }} <span class="count">{{ formatNumber(item.count) }}</span>
          </button>
        </div>
        <div class="toolbar">
          <div class="input-icon search-field">
            <input
              v-model="q"
              class="input"
              placeholder="搜索条码、题名或读者"
              autocomplete="off"
              @input="onSearchInput"
              @keydown.enter.prevent="list.page = 1; loadList()"
            >
            <AppIcon name="search" />
          </div>
          <span class="toolbar-spacer" />
          <span class="toolbar-count">共 <strong>{{ formatNumber(list.total) }}</strong> 条</span>
        </div>
        <div class="table-scroll-hint">左右滑动查看完整表格与操作</div>
        <PagerBar :page="list.page" :page-size="list.pageSize" :total="list.total" noun="条" @update:page="setPage" />
        <div class="table-wrap">
          <table class="table is-striped">
            <thead v-if="tab === 'missing'">
              <tr><th>条码</th><th>题名</th><th>索书号</th><th>馆藏地点</th><th>状态</th></tr>
            </thead>
            <thead v-else-if="tab === 'on_loan'">
              <tr><th>条码</th><th>题名</th><th>读者</th><th>应还日期</th><th>逾期</th></tr>
            </thead>
            <thead v-else>
              <tr><th>时间</th><th>结果</th><th>条码</th><th>题名</th><th>说明</th></tr>
            </thead>
            <tbody>
              <tr v-if="list.loading">
                <td colspan="5" class="table-state"><span class="spinner" />正在读取清单…</td>
              </tr>
              <tr v-else-if="list.error">
                <td colspan="5" class="table-state">{{ list.error }}</td>
              </tr>
              <tr v-else-if="!list.items.length">
                <td colspan="5" class="table-state">
                  {{ tab === 'missing' ? '当前没有漏盘的馆藏' : tab === 'on_loan' ? '当前没有外借未归的馆藏' : '当前没有异常扫描记录' }}
                </td>
              </tr>
              <template v-else-if="tab === 'missing'">
                <tr v-for="row in missingRows" :key="row.holding_id">
                  <td class="num">{{ row.barcode || '—' }}</td>
                  <td><div class="cell-main">{{ row.title || '未知题名' }}</div></td>
                  <td>{{ row.call_no || '—' }}</td>
                  <td>{{ holdingLocationLabel(row.location, row.shelf_no) }}</td>
                  <td><span class="status-text muted"><span class="dot" />{{ holdingStatusLabel(row.status) }}</span></td>
                </tr>
              </template>
              <template v-else-if="tab === 'on_loan'">
                <tr v-for="row in onLoanRows" :key="row.loan_id">
                  <td class="num">{{ row.barcode || '—' }}</td>
                  <td>
                    <div class="cell-main">{{ row.title || '未知题名' }}</div>
                  </td>
                  <td>
                    <div class="cell-main" style="font-size: 12.5px">{{ row.reader_name || '未知读者' }}</div>
                    <div class="cell-sub">{{ row.reader_card_no }}<template v-if="row.reader_class"> · {{ row.reader_class }}</template></div>
                  </td>
                  <td class="cell-date">{{ row.due_on }}</td>
                  <td>
                    <span v-if="row.days_overdue" class="status-text danger"><span class="dot" />逾期 {{ row.days_overdue }} 天</span>
                    <span v-else class="status-text ok"><span class="dot" />未逾期</span>
                  </td>
                </tr>
              </template>
              <template v-else>
                <tr v-for="row in problemRows" :key="row.id">
                  <td class="cell-date">{{ formatDateTime(row.scanned_at) }}</td>
                  <td>
                    <span class="status-text" :class="resultTone(row.result)">
                      <span class="dot" />{{ row.result_label }}
                    </span>
                  </td>
                  <td class="num">{{ row.barcode }}</td>
                  <td>{{ row.title || '—' }}</td>
                  <td class="cell-date">{{ row.message }}</td>
                </tr>
              </template>
            </tbody>
          </table>
        </div>
      </article>

      <BaseModal :open="closeOpen" title="结束盘点" subtitle="结束后清单固定，不能继续扫描" size="sm" @close="closeOpen = false">
        <div class="notice-strip">
          <AppIcon name="alert" />
          <span>请确认已扫描完毕。结束盘点不自动注销馆藏，漏盘目录需人工核对。</span>
        </div>
        <div class="field">
          <label for="close-note">结束备注</label>
          <textarea id="close-note" v-model="closeNote" class="textarea" rows="3" maxlength="256" placeholder="盘点情况说明（可选）" />
        </div>
        <p v-if="closeError" class="field-note is-error" style="margin-top: 10px" role="alert">{{ closeError }}</p>
        <template #footer>
          <button class="btn btn-ghost" type="button" :disabled="actionBusy" @click="closeOpen = false">继续扫描</button>
          <button class="btn btn-danger" type="button" :disabled="actionBusy" @click="submitClose">
            {{ actionBusy ? '处理中…' : '确认结束盘点' }}
          </button>
        </template>
      </BaseModal>
    </template>
  </section>
</template>
