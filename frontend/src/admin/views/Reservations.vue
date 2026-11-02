<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import AppIcon from '../../components/AppIcon.vue'
import BookCover from '../../components/BookCover.vue'
import BaseModal from '../../components/BaseModal.vue'
import PagerBar from '../../components/PagerBar.vue'
import TableState from '../../components/TableState.vue'
import { can } from '../../lib/auth'
import { errorMessage, get, post } from '../../lib/api'
import { usePagedList } from '../../lib/usePagedList'
import { avatarClass, formatDateTime, formatMonthDay, formatNumber } from '../../lib/format'
import { toast, toastError } from '../../lib/toast'
import { libraryState, loadLibraries } from '../../stores/libraries'

type ResStatus = 'PENDING' | 'READY' | 'FULFILLED' | 'CANCELLED' | 'EXPIRED'
type ResTab = 'ready' | 'pending' | 'all'

interface ReservationRow {
  id: number
  library_id: number
  record_id: number
  reader_id: number
  status: ResStatus
  status_label: string
  reserved_at: string | null
  ready_at: string | null
  expires_at: string | null
  fulfilled_at: string | null
  cancelled_at: string | null
  cancel_reason: string | null
  note: string | null
  seconds_left: number | null
  queue_position: number | null
  available_count: number
  title: string | null
  author: string | null
  class_no: string | null
  isbn: string | null
  call_no: string | null
  reader: {
    id: number
    card_no: string | null
    name: string | null
    reader_type: string | null
    class_name: string | null
    phone: string | null
    status: string | null
  }
}

interface ReservationList {
  total: number
  page: number
  page_size: number
  items: ReservationRow[]
  summary: {
    pending: number
    ready: number
    expiring_24h: number
    fulfilled_today: number
    hold_days: number
  }
}

interface ReaderPanel {
  id: number
  card_no: string
  name: string
  reader_type_label: string
  class_name: string | null
  status: string
  status_label: string
  active_count: number
  rules: { max_items: number; loan_days: number; max_renew: number }
}

interface RecordRow {
  id: number
  title: string | null
  author: string | null
  class_no: string | null
  isbn: string | null
  call_no: string | null
  active_count: number
  total_count: number
}

interface RecordList {
  total: number
  items: RecordRow[]
}

const route = useRoute()
const canWrite = computed(() => can('circulation', 'write'))
const libraryId = computed(() => libraryState.currentId)

function normalizeTab(value: unknown): ResTab {
  const text = String(value || '')
  if (text === 'pending') return 'pending'
  if (text === 'all' || text === 'closed') return 'all'
  return 'ready'
}

const tab = ref<ResTab>(normalizeTab(route.query.tab))
const q = ref(String(route.query.q || ''))
const summary = ref<ReservationList['summary'] | null>(null)
const actionBusy = ref(false)

const { list, load: loadList, setPage, onSearchInput } = usePagedList<ReservationRow, ReservationList>({
  onLoaded: (data) => { summary.value = data.summary },
  fetch: ({ page, pageSize }) => get<ReservationList>('/reservations', {
    library_id: libraryId.value || undefined,
    status: tab.value,
    q: q.value.trim() || undefined,
    page,
    page_size: pageSize,
  }),
})

const TABS: Array<{ key: ResTab; label: string }> = [
  { key: 'ready', label: '待取书' },
  { key: 'pending', label: '排队中' },
  { key: 'all', label: '全部' },
]

function chooseTab(next: ResTab) {
  if (next === tab.value) return
  tab.value = next
  list.page = 1
  void loadList()
}

watch(libraryId, async () => {
  list.page = 1
  await loadList()
})
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
  () => route.query.q,
  (value) => {
    const next = typeof value === 'string' ? value : ''
    if (next !== q.value) {
      q.value = next
      list.page = 1
      void loadList()
    }
  },
)

/* ---------------- Mark ready ---------------- */
async function markReady(row: ReservationRow) {
  if (actionBusy.value) return
  actionBusy.value = true
  try {
    const result = await post<{ message: string }>(`/reservations/${row.id}/ready`)
    toast(result.message)
    await loadList()
  } catch (error) {
    toastError(errorMessage(error))
  } finally {
    actionBusy.value = false
  }
}

/* ---------------- Cancel ---------------- */
const cancelTarget = ref<ReservationRow | null>(null)
const cancelReason = ref('')
const cancelError = ref('')

function openCancel(row: ReservationRow) {
  cancelTarget.value = row
  cancelReason.value = ''
  cancelError.value = ''
}

async function submitCancel() {
  const row = cancelTarget.value
  if (!row) return
  if (!cancelReason.value.trim()) {
    cancelError.value = '请填写取消原因'
    return
  }
  actionBusy.value = true
  try {
    await post(`/reservations/${row.id}/cancel`, { reason: cancelReason.value.trim() })
    toast('预约已取消')
    cancelTarget.value = null
    await loadList()
  } catch (error) {
    cancelError.value = errorMessage(error)
  } finally {
    actionBusy.value = false
  }
}

/* ---------------- Fulfill ---------------- */
const fulfillTarget = ref<ReservationRow | null>(null)
const fulfillBarcode = ref('')
const fulfillError = ref('')

function openFulfill(row: ReservationRow) {
  fulfillTarget.value = row
  fulfillBarcode.value = ''
  fulfillError.value = ''
}

async function submitFulfill() {
  const row = fulfillTarget.value
  if (!row) return
  if (!fulfillBarcode.value.trim()) {
    fulfillError.value = '请扫描要外借的图书条码'
    return
  }
  actionBusy.value = true
  try {
    const result = await post<{ message: string; loan: { due_on: string } | null }>(
      `/reservations/${row.id}/fulfill`,
      { barcode: fulfillBarcode.value.trim() },
    )
    toast(result.loan ? `外借成功，应还日期 ${result.loan.due_on}` : result.message)
    fulfillTarget.value = null
    await loadList()
  } catch (error) {
    fulfillError.value = errorMessage(error)
  } finally {
    actionBusy.value = false
  }
}

/* ---------------- Notify ---------------- */
const notifyTarget = ref<ReservationRow | null>(null)
const notifyText = ref('')
const notifyInput = ref<HTMLTextAreaElement | null>(null)

function notifyMessage(row: ReservationRow): string {
  const reader = row.reader.name || row.reader.card_no || '读者'
  return `【简籍图书馆】${reader}，您预约的《${row.title || '图书'}》已到馆，请于 ${formatMonthDay(row.expires_at)} 前到馆取书。`
}

async function copyNotify(row: ReservationRow) {
  const text = notifyMessage(row)
  try {
    await navigator.clipboard.writeText(text)
    toast('通知文案已复制')
  } catch {
    notifyTarget.value = row
    notifyText.value = text
  }
}

async function copyFallback() {
  if (!notifyInput.value) return
  notifyInput.value.select()
  try {
    await navigator.clipboard.writeText(notifyText.value)
    toast('通知文案已复制')
    notifyTarget.value = null
  } catch {
    toastError('浏览器不允许自动复制，请手动选择文本复制')
  }
}

/* ---------------- Create ---------------- */
const createOpen = ref(false)
const cardNo = ref('')
const readerLoading = ref(false)
const readerError = ref('')
const pickedReader = ref<ReaderPanel | null>(null)
const recordQuery = ref('')
const recordLoading = ref(false)
const records = ref<RecordRow[]>([])
const pickedRecord = ref<RecordRow | null>(null)
const createNote = ref('')
const createBusy = ref(false)
const createError = ref('')

function openCreate() {
  createOpen.value = true
  cardNo.value = ''
  pickedReader.value = null
  readerError.value = ''
  recordQuery.value = ''
  records.value = []
  pickedRecord.value = null
  createNote.value = ''
  createError.value = ''
}

async function lookupReader() {
  if (!cardNo.value.trim()) {
    readerError.value = '请输入借书证号'
    return
  }
  readerLoading.value = true
  readerError.value = ''
  try {
    pickedReader.value = await get<ReaderPanel>('/circulation/reader', {
      card_no: cardNo.value.trim(),
      library_id: libraryId.value || undefined,
    })
  } catch (error) {
    pickedReader.value = null
    readerError.value = errorMessage(error)
  } finally {
    readerLoading.value = false
  }
}

async function searchRecords() {
  const query = recordQuery.value.trim()
  if (!query) {
    records.value = []
    return
  }
  recordLoading.value = true
  try {
    const data = await get<RecordList>('/records', {
      library_id: libraryId.value || undefined,
      q: query,
      page_size: 8,
    })
    records.value = data.items
    pickedRecord.value = null
  } catch (error) {
    toastError(errorMessage(error))
    records.value = []
  } finally {
    recordLoading.value = false
  }
}

async function submitCreate() {
  if (!pickedReader.value || !pickedRecord.value) {
    createError.value = '请先选择读者与书目'
    return
  }
  createBusy.value = true
  createError.value = ''
  try {
    const result = await post<{ message: string }>('/reservations', {
      reader_id: pickedReader.value.id,
      record_id: pickedRecord.value.id,
      note: createNote.value.trim() || null,
    })
    toast(result.message)
    createOpen.value = false
    tab.value = 'pending'
    list.page = 1
    await loadList()
  } catch (error) {
    createError.value = errorMessage(error)
  } finally {
    createBusy.value = false
  }
}

/* ---------------- Display helpers ---------------- */
function remaining(seconds: number | null): string {
  if (seconds === null) return ''
  if (seconds <= 0) return '已到期'
  const hours = Math.ceil(seconds / 3600)
  if (hours < 24) return `剩余 ${hours} 小时`
  return `剩余 ${Math.ceil(hours / 24)} 天`
}

function isUrgent(row: ReservationRow): boolean {
  return row.status === 'READY' && row.seconds_left !== null && row.seconds_left < 86_400
}

function statusTone(row: ReservationRow): string {
  if (row.status === 'READY') return 'warn'
  if (row.status === 'PENDING') return 'muted'
  if (row.status === 'FULFILLED') return 'ok'
  if (row.status === 'EXPIRED') return 'danger'
  return 'muted'
}

function statusSub(row: ReservationRow): string {
  if (row.status === 'CANCELLED' && row.cancel_reason) return row.cancel_reason
  if (row.status === 'FULFILLED' && row.fulfilled_at) return `外借于 ${formatMonthDay(row.fulfilled_at)}`
  if (row.status === 'EXPIRED') return '保留期已过，队列已顺延'
  if (row.status === 'PENDING' && row.queue_position) return `排队第 ${row.queue_position} 位`
  if (row.status === 'READY') return remaining(row.seconds_left)
  return ''
}

onMounted(async () => {
  await loadLibraries()
  await loadList()
})
</script>

<template>
  <section class="view">
    <div class="metric-grid">
      <div class="metric-card">
        <span class="metric-icon amber"><AppIcon name="bookmark" /></span>
        <div class="metric-body">
          <div class="metric-label">待取书</div>
          <div class="metric-value">{{ formatNumber(summary?.ready) }}<small>位</small></div>
          <div class="metric-meta">{{ summary?.expiring_24h ? `${summary.expiring_24h} 位将在 24 小时内过期` : '暂无即将过期' }}</div>
        </div>
      </div>
      <div class="metric-card">
        <span class="metric-icon"><AppIcon name="clock" /></span>
        <div class="metric-body">
          <div class="metric-label">排队中</div>
          <div class="metric-value">{{ formatNumber(summary?.pending) }}<small>位</small></div>
          <div class="metric-meta">归还后按顺位自动提升</div>
        </div>
      </div>
      <div class="metric-card">
        <span class="metric-icon blue"><AppIcon name="check-circle" /></span>
        <div class="metric-body">
          <div class="metric-label">今日已外借</div>
          <div class="metric-value">{{ formatNumber(summary?.fulfilled_today) }}<small>笔</small></div>
          <div class="metric-meta">预约履行数量</div>
        </div>
      </div>
      <div class="metric-card">
        <span class="metric-icon neutral"><AppIcon name="info" /></span>
        <div class="metric-body">
          <div class="metric-label">保留期</div>
          <div class="metric-value">{{ summary?.hold_days ?? 3 }}<small>天</small></div>
          <div class="metric-meta">到书后逾期自动释放</div>
        </div>
      </div>
    </div>

    <div class="notice-strip">
      <AppIcon name="info" />
      <span>预约按登记时间先到先得。待取书超过保留期未取，系统自动释放并顺延下一位读者。</span>
    </div>

    <article class="data-card">
      <div class="tabs card-tabs" role="tablist" aria-label="预约状态">
        <button
          v-for="item in TABS"
          :key="item.key"
          class="tab"
          type="button"
          :class="{ 'is-active': tab === item.key }"
          :aria-selected="tab === item.key"
          @click="chooseTab(item.key)"
        >
          {{ item.label }}
          <span v-if="item.key !== 'all' && summary" class="count">{{ formatNumber(item.key === 'ready' ? summary.ready : summary.pending) }}</span>
        </button>
      </div>

      <div class="toolbar">
        <div class="input-icon search-field">
          <input
            v-model="q"
            class="input"
            placeholder="搜索题名、读者或借书证号"
            autocomplete="off"
            @input="onSearchInput"
            @keydown.enter.prevent="list.page = 1; loadList()"
          >
          <AppIcon name="search" />
        </div>
        <span class="toolbar-spacer" />
        <span class="toolbar-count">共 <strong>{{ formatNumber(list.total) }}</strong> 条预约</span>
        <button v-if="canWrite" class="btn btn-primary btn-sm" type="button" @click="openCreate">
          <AppIcon name="plus" />登记预约
        </button>
      </div>

      <div class="table-scroll-hint">左右滑动查看完整表格与操作</div>
      <PagerBar :page="list.page" :page-size="list.pageSize" :total="list.total" noun="条预约" @update:page="setPage" />
      <div class="table-wrap">
        <table class="table is-striped">
          <thead>
            <tr>
              <th>书目</th>
              <th>读者</th>
              <th class="col-hide-md">预约时间</th>
              <th>到书 / 保留</th>
              <th>状态</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <TableState v-if="list.loading || list.error || !list.items.length"
                        :colspan="6" :list="list" loading-text="正在读取预约…"
                        :empty="tab === 'ready' ? '当前没有待取书的预约' : tab === 'pending' ? '当前没有排队中的预约' : '当前没有预约记录'"
                        :retry="loadList" />
            <template v-else>
              <tr v-for="row in list.items" :key="row.id">
                <td>
                  <div class="reservation-book">
                    <BookCover :title="row.title || '未知题名'" />
                    <div class="reservation-book-info">
                      <RouterLink class="cell-main" :to="`/admin/records/${row.record_id}`">{{ row.title || '未知题名' }}</RouterLink>
                      <div class="cell-sub">{{ row.author || '佚名' }} · {{ row.call_no || row.class_no || '未分类' }}</div>
                    </div>
                  </div>
                </td>
                <td>
                  <div class="reader-inline">
                    <span class="avatar" :class="avatarClass(row.reader_id)">{{ (row.reader.name || '读').slice(-2) }}</span>
                    <div class="reader-inline-text">
                      <RouterLink class="cell-main" :to="`/admin/readers/${row.reader_id}`">{{ row.reader.name || '未知读者' }}</RouterLink>
                      <span>{{ row.reader.card_no }}<template v-if="row.reader.class_name"> · {{ row.reader.class_name }}</template></span>
                    </div>
                  </div>
                </td>
                <td class="col-hide-md cell-date">{{ formatDateTime(row.reserved_at) }}</td>
                <td class="cell-date">
                  <template v-if="row.status === 'READY'">
                    <span class="hold-time" :class="{ 'is-urgent': isUrgent(row) }">
                      {{ formatMonthDay(row.expires_at) }} 前取书
                    </span>
                    <div class="cell-sub">{{ statusSub(row) }}</div>
                  </template>
                  <template v-else-if="row.status === 'PENDING'">
                    <span class="queue-badge" :class="{ 'is-first': row.queue_position === 1 }">
                      {{ row.queue_position || '—' }}
                    </span>
                    <span class="cell-sub" style="margin-left: 6px">顺位</span>
                  </template>
                  <template v-else>—</template>
                </td>
                <td>
                  <span class="status-text" :class="statusTone(row)">
                    <span class="dot" />{{ row.status_label }}
                  </span>
                  <div v-if="row.status !== 'READY' && row.status !== 'PENDING'" class="cell-sub">{{ statusSub(row) }}</div>
                </td>
                <td>
                  <div class="reservation-action-group">
                    <template v-if="canWrite && row.status === 'PENDING'">
                      <button class="text-link" type="button" :disabled="actionBusy" @click="markReady(row)">标记到书</button>
                      <button class="text-link" type="button" :disabled="actionBusy" @click="openCancel(row)">取消</button>
                    </template>
                    <template v-else-if="canWrite && row.status === 'READY'">
                      <button class="text-link" type="button" :disabled="actionBusy" @click="openFulfill(row)">外借</button>
                      <button class="text-link" type="button" @click="copyNotify(row)">通知</button>
                      <button class="text-link" type="button" :disabled="actionBusy" @click="openCancel(row)">取消</button>
                    </template>
                    <RouterLink v-else-if="row.reader.id" class="text-link" :to="`/admin/readers/${row.reader.id}`">读者</RouterLink>
                  </div>
                </td>
              </tr>
            </template>
          </tbody>
        </table>
      </div>
    </article>

    <BaseModal
      :open="Boolean(cancelTarget)"
      title="取消预约"
      :subtitle="cancelTarget ? `《${cancelTarget.title || '未知题名'}》 · ${cancelTarget.reader.name || ''}` : ''"
      size="sm"
      @close="cancelTarget = null"
    >
      <div class="field">
        <label for="cancel-reason">取消原因 <span class="req">*</span></label>
        <textarea id="cancel-reason" v-model="cancelReason" class="textarea" rows="3" maxlength="128" placeholder="如：读者不再需要、信息登记有误" />
      </div>
      <p v-if="cancelError" class="field-note is-error" style="margin-top: 10px" role="alert">{{ cancelError }}</p>
      <template #footer>
        <button class="btn btn-ghost" type="button" :disabled="actionBusy" @click="cancelTarget = null">返回</button>
        <button class="btn btn-danger" type="button" :disabled="actionBusy" @click="submitCancel">
          {{ actionBusy ? '处理中…' : '确认取消' }}
        </button>
      </template>
    </BaseModal>

    <BaseModal
      :open="Boolean(fulfillTarget)"
      title="外借预约图书"
      :subtitle="fulfillTarget ? `《${fulfillTarget.title || '未知题名'}》 · ${fulfillTarget.reader.name || ''}` : ''"
      size="sm"
      @close="fulfillTarget = null"
    >
      <div class="notice-strip" style="margin-bottom: 14px">
        <AppIcon name="barcode" />
        <span>扫描该读者要借走的图书条码，系统同时生成借阅记录并结清本次预约。</span>
      </div>
      <div class="field">
        <label for="fulfill-barcode">图书条码</label>
        <input
          id="fulfill-barcode"
          v-model="fulfillBarcode"
          class="input"
          placeholder="扫描条码"
          autocomplete="off"
          @keydown.enter.prevent="submitFulfill"
        >
      </div>
      <p v-if="fulfillError" class="field-note is-error" style="margin-top: 10px" role="alert">{{ fulfillError }}</p>
      <template #footer>
        <button class="btn btn-ghost" type="button" :disabled="actionBusy" @click="fulfillTarget = null">取消</button>
        <button class="btn btn-primary" type="button" :disabled="actionBusy" @click="submitFulfill">
          {{ actionBusy ? '处理中…' : '确认外借' }}
        </button>
      </template>
    </BaseModal>

    <BaseModal
      :open="Boolean(notifyTarget)"
      title="通知读者"
      subtitle="复制以下文案，通过短信或即时通讯发送"
      size="sm"
      @close="notifyTarget = null"
    >
      <div class="field">
        <label for="notify-text">通知文案</label>
        <textarea id="notify-text" ref="notifyInput" v-model="notifyText" class="textarea" rows="4" readonly />
      </div>
      <template #footer>
        <button class="btn btn-ghost" type="button" @click="notifyTarget = null">关闭</button>
        <button class="btn btn-primary" type="button" @click="copyFallback">
          <AppIcon name="file" />复制文案
        </button>
      </template>
    </BaseModal>

    <BaseModal
      :open="createOpen"
      title="登记预约"
      subtitle="仅可预约当前无可借馆藏的书目"
      size="lg"
      @close="createOpen = false"
    >
      <div class="notice-strip">
        <AppIcon name="info" />
        <span>有可借馆藏时请直接办理借阅。预约登记后，归还会自动按顺位提升为待取书。</span>
      </div>

      <div class="form-grid">
        <div class="field">
          <label for="res-card">借书证号 <span class="req">*</span></label>
          <div class="scan-field">
            <input
              id="res-card"
              v-model="cardNo"
              class="input"
              placeholder="输入借书证号后识别"
              autocomplete="off"
              @keydown.enter.prevent="lookupReader"
            >
            <button class="btn btn-outline" type="button" :disabled="readerLoading" @click="lookupReader">
              {{ readerLoading ? '查询中…' : '识别' }}
            </button>
          </div>
          <p v-if="readerError" class="field-note is-error" role="alert">{{ readerError }}</p>
          <div v-if="pickedReader" class="lookup-panel">
            <span class="avatar" :class="avatarClass(pickedReader.id)">{{ pickedReader.name.slice(-2) }}</span>
            <div class="lookup-main">
              <strong>{{ pickedReader.name }}</strong>
              <span>{{ pickedReader.card_no }} · {{ pickedReader.class_name || pickedReader.reader_type_label }} · {{ pickedReader.status_label }}</span>
            </div>
            <div class="lookup-side">
              <strong>{{ pickedReader.active_count }} / {{ pickedReader.rules.max_items }}</strong>
              <span>外借 / 上限</span>
            </div>
          </div>
        </div>

        <div class="field">
          <label for="res-book">书目 <span class="req">*</span></label>
          <div class="scan-field">
            <input
              id="res-book"
              v-model="recordQuery"
              class="input"
              placeholder="输入题名、ISBN 或条码"
              autocomplete="off"
              @keydown.enter.prevent="searchRecords"
            >
            <button class="btn btn-outline" type="button" :disabled="recordLoading" @click="searchRecords">
              {{ recordLoading ? '检索中…' : '检索' }}
            </button>
          </div>
          <div v-if="records.length" class="rec-picker">
            <button
              v-for="row in records"
              :key="row.id"
              class="picker-row"
              :class="{ 'is-selected': pickedRecord?.id === row.id }"
              type="button"
              @click="pickedRecord = row"
            >
              <BookCover :title="row.title || '未知题名'" />
              <div class="picker-info">
                <strong>{{ row.title || '未知题名' }}</strong>
                <span>{{ row.author || '佚名' }} · {{ row.call_no || row.class_no || '未分类' }} · 馆藏 {{ row.total_count }} 册</span>
              </div>
              <AppIcon v-if="pickedRecord?.id === row.id" name="check-circle" :size="17" />
            </button>
          </div>
          <p v-else-if="recordQuery && !recordLoading" class="field-note">没有检索到书目，请调整关键词。</p>
        </div>
      </div>

      <div class="field" style="margin-top: 14px">
        <label for="res-note">备注</label>
        <input id="res-note" v-model="createNote" class="input" maxlength="256" placeholder="馆员备注（可选）">
      </div>
      <p v-if="createError" class="field-note is-error" style="margin-top: 10px" role="alert">{{ createError }}</p>

      <template #footer>
        <button class="btn btn-ghost" type="button" :disabled="createBusy" @click="createOpen = false">取消</button>
        <button class="btn btn-primary" type="button" :disabled="createBusy || !pickedReader || !pickedRecord" @click="submitCreate">
          {{ createBusy ? '提交中…' : '登记预约' }}
        </button>
      </template>
    </BaseModal>
  </section>
</template>
