<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import AppIcon from '../../components/AppIcon.vue'
import BaseModal from '../../components/BaseModal.vue'
import ConfirmDialog from '../../components/ConfirmDialog.vue'
import PagerBar from '../../components/PagerBar.vue'
import TableState from '../../components/TableState.vue'
import ThSort from '../../components/ThSort.vue'
import ReaderFormModal from '../components/ReaderFormModal.vue'
import type { ReaderRecord } from '../../lib/readerTypes'
import { can } from '../../lib/auth'
import { download, errorMessage, get, post, put } from '../../lib/api'
import { usePagedList } from '../../lib/usePagedList'
import { avatarClass, formatDate, formatNumber } from '../../lib/format'
import { readerStatusLabel } from '../../lib/displayLabels'
import { toast, toastError } from '../../lib/toast'
import { libraryState, loadLibraries } from '../../stores/libraries'

interface ReaderRow extends ReaderRecord {
  active_count: number
  overdue_count: number
}

interface ReaderList {
  total: number
  page: number
  page_size: number
  items: ReaderRow[]
}

interface ReaderFacets {
  grades: string[]
  classes: string[]
  types: Array<{ value: string; label: string; count: number }>
  statuses: Record<string, number>
  total: number
}

interface Rules {
  types: Record<string, { label: string; loan_days: number; max_items: number; max_renew: number }>
}

interface ImportResult {
  created: number
  updated: number
  failed: number
  total: number
  errors: Array<{ row: number; message: string }>
  errors_truncated: boolean
}

const route = useRoute()
const canWrite = computed(() => can('readers', 'write'))
const libraryId = computed(() => libraryState.currentId)

const facets = ref<ReaderFacets | null>(null)
const rules = ref<Rules | null>(null)
const q = ref(String(route.query.q || ''))
const readerType = ref(String(route.query.type || ''))
const statusFilter = ref(String(route.query.status || '').toUpperCase())
const grade = ref('')
const className = ref('')
const selected = ref<number[]>([])

const typeOptions = computed(() => facets.value?.types.filter((item) => item.count > 0) || [])
const statusOptions = computed(() => [
  { value: 'ACTIVE', label: '正常' },
  { value: 'SUSPENDED', label: '暂停借阅' },
  { value: 'INACTIVE', label: '已注销' },
])

const normalCount = computed(() => facets.value?.statuses.ACTIVE || 0)
const suspendedCount = computed(() => facets.value?.statuses.SUSPENDED || 0)
const inactiveCount = computed(() => facets.value?.statuses.INACTIVE || 0)

function maxItems(reader: ReaderRow): number {
  return rules.value?.types[reader.reader_type]?.max_items || 2
}

async function loadFacets() {
  try {
    facets.value = await get<ReaderFacets>('/readers/facets', {
      library_id: libraryId.value || undefined,
    })
  } catch (error) {
    facets.value = null
    toastError(errorMessage(error))
  }
}

async function loadRules() {
  try {
    rules.value = (await get<{ rules: Rules }>('/circulation/rules')).rules
  } catch {
    rules.value = null
  }
}

const { list, load: loadList, resetAndLoad: resetPageAndLoad, setPage, setSort, onSearchInput } =
  usePagedList<ReaderRow, ReaderList>({
    // 翻页 / 筛选重置都换一批行，勾选必须跟着清掉（原 resetPageAndLoad/setPage 的行为）
    onPage: () => { selected.value = [] },
    fetch: ({ page, pageSize, sort, order }) => get<ReaderList>('/readers', {
      library_id: libraryId.value || undefined,
      q: q.value.trim() || undefined,
      reader_type: readerType.value || undefined,
      status: statusFilter.value || undefined,
      grade: grade.value || undefined,
      class_name: className.value || undefined,
      sort: sort || undefined,
      order: order || undefined,
      page,
      page_size: pageSize,
    }),
  })

watch([readerType, statusFilter, grade, className], resetPageAndLoad)
watch(libraryId, async () => {
  list.page = 1
  selected.value = []
  await Promise.all([loadFacets(), loadList()])
})

/* ---------------- Selection ---------------- */
const allChecked = computed(() =>
  list.items.length > 0 && list.items.every((row) => selected.value.includes(row.id)),
)
const selectedCount = computed(() => selected.value.length)

function toggleAll() {
  if (allChecked.value) selected.value = []
  else selected.value = list.items.map((row) => row.id)
}

function toggleOne(id: number) {
  const index = selected.value.indexOf(id)
  if (index >= 0) selected.value.splice(index, 1)
  else selected.value.push(id)
}

/* ---------------- Create / edit ---------------- */
const formOpen = ref(false)
const editing = ref<ReaderRecord | null>(null)

function openCreate() {
  editing.value = null
  formOpen.value = true
}

function openEdit(reader: ReaderRecord) {
  editing.value = reader
  formOpen.value = true
}

async function onSaved() {
  await Promise.all([loadFacets(), loadList()])
}

/* ---------------- Quick status ---------------- */
interface StatusTarget {
  reader: ReaderRow
  status: string
}
const statusTarget = ref<StatusTarget | null>(null)
const actionBusy = ref(false)

function askStatus(reader: ReaderRow, status: string) {
  statusTarget.value = { reader, status }
}

function statusLabel(status: string): string {
  return readerStatusLabel(status)
}

async function submitStatus() {
  const target = statusTarget.value
  if (!target) return
  actionBusy.value = true
  try {
    await put(`/readers/${target.reader.id}`, { status: target.status })
    toast(`读者状态已改为「${statusLabel(target.status)}」`)
    statusTarget.value = null
    await Promise.all([loadFacets(), loadList()])
  } catch (error) {
    toastError(errorMessage(error))
  } finally {
    actionBusy.value = false
  }
}

/* ---------------- Bulk status ---------------- */
const bulkTarget = ref('')
const bulkConfirmOpen = ref(false)

function askBulkStatus(status: string) {
  if (!selectedCount.value) return
  bulkTarget.value = status
  bulkConfirmOpen.value = true
}

async function submitBulkStatus() {
  if (!bulkTarget.value) return
  actionBusy.value = true
  try {
    const result = await post<{ updated: number }>('/readers/batch-status', {
      ids: selected.value,
      status: bulkTarget.value,
    })
    toast(`已更新 ${result.updated} 位读者`)
    selected.value = []
    bulkConfirmOpen.value = false
    await Promise.all([loadFacets(), loadList()])
  } catch (error) {
    toastError(errorMessage(error))
  } finally {
    actionBusy.value = false
  }
}

/* ---------------- Import ---------------- */
const importOpen = ref(false)
const importBusy = ref(false)
const importResult = ref<ImportResult | null>(null)
const importError = ref('')
const pickedName = ref('')

function openImport() {
  importOpen.value = true
  importResult.value = null
  importError.value = ''
  pickedName.value = ''
}

async function downloadTemplate() {
  try {
    await download('/readers/import/template', undefined, 'reader_template.xlsx')
  } catch (error) {
    toastError(errorMessage(error))
  }
}

async function onFilePicked(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  if (!file.name.toLowerCase().endsWith('.xlsx')) {
    importError.value = '仅支持 .xlsx 文件'
    input.value = ''
    return
  }
  pickedName.value = file.name
  importBusy.value = true
  importError.value = ''
  importResult.value = null
  try {
    const data = new FormData()
    data.append('file', file)
    if (libraryId.value) data.append('library_id', String(libraryId.value))
    importResult.value = await post<ImportResult>('/readers/import', data)
    toast(`导入完成：新增 ${importResult.value.created}，更新 ${importResult.value.updated}`)
    await Promise.all([loadFacets(), loadList()])
  } catch (error) {
    importError.value = errorMessage(error)
  } finally {
    importBusy.value = false
    input.value = ''
  }
}

/* ---------------- init ---------------- */
onMounted(async () => {
  await loadLibraries()
  await Promise.all([loadRules(), loadFacets(), loadList()])
})
</script>

<template>
  <section class="view">
    <div class="metric-grid">
      <div class="metric-card">
        <span class="metric-icon"><AppIcon name="users" /></span>
        <div class="metric-body">
          <div class="metric-label">读者总数</div>
          <div class="metric-value">{{ formatNumber(facets?.total) }}<small>位</small></div>
          <div class="metric-meta">当前图书库登记读者</div>
        </div>
      </div>
      <div class="metric-card">
        <span class="metric-icon"><AppIcon name="check-circle" /></span>
        <div class="metric-body">
          <div class="metric-label">正常</div>
          <div class="metric-value">{{ formatNumber(normalCount) }}<small>位</small></div>
          <div class="metric-meta">可正常借阅</div>
        </div>
      </div>
      <div class="metric-card">
        <span class="metric-icon amber"><AppIcon name="clock" /></span>
        <div class="metric-body">
          <div class="metric-label">暂停借阅</div>
          <div class="metric-value">{{ formatNumber(suspendedCount) }}<small>位</small></div>
          <div class="metric-meta">仍可归还图书</div>
        </div>
      </div>
      <div class="metric-card">
        <span class="metric-icon neutral"><AppIcon name="archive" /></span>
        <div class="metric-body">
          <div class="metric-label">已注销</div>
          <div class="metric-value">{{ formatNumber(inactiveCount) }}<small>位</small></div>
          <div class="metric-meta">档案与借阅历史保留</div>
        </div>
      </div>
    </div>

    <div class="page-tools">
      <div class="filter-row">
        <div class="input-icon search-field">
          <input
            v-model="q"
            class="input"
            placeholder="搜索姓名、借书证号或班级"
            autocomplete="off"
            @input="onSearchInput"
            @keydown.enter.prevent="resetPageAndLoad"
          >
          <AppIcon name="search" />
        </div>
        <select v-model="readerType" class="select" aria-label="读者类型">
          <option value="">全部类型</option>
          <option v-for="item in typeOptions" :key="item.value" :value="item.value">{{ item.label }}</option>
        </select>
        <select v-model="statusFilter" class="select" aria-label="读者状态">
          <option value="">全部状态</option>
          <option v-for="item in statusOptions" :key="item.value" :value="item.value">{{ item.label }}</option>
        </select>
        <select v-model="grade" class="select" aria-label="年级">
          <option value="">全部年级</option>
          <option v-for="item in facets?.grades || []" :key="item" :value="item">{{ item }}</option>
        </select>
        <select v-model="className" class="select" aria-label="班级">
          <option value="">全部班级</option>
          <option v-for="item in facets?.classes || []" :key="item" :value="item">{{ item }}</option>
        </select>
      </div>
      <span class="spacer" />
      <button v-if="canWrite" class="btn btn-outline btn-sm" type="button" @click="openImport">
        <AppIcon name="upload" />导入
      </button>
      <button v-if="canWrite" class="btn btn-primary btn-sm" type="button" @click="openCreate">
        <AppIcon name="user-plus" />新增读者
      </button>
    </div>

    <article class="data-card">
      <div v-if="selectedCount" class="bulk-bar">
        <span>已选 <strong>{{ selectedCount }}</strong> 位读者</span>
        <span class="spacer" />
        <button class="btn btn-outline btn-sm" type="button" @click="askBulkStatus('ACTIVE')">设为正常</button>
        <button class="btn btn-outline btn-sm" type="button" @click="askBulkStatus('SUSPENDED')">暂停借阅</button>
        <button class="btn btn-ghost btn-sm" type="button" @click="selected = []">取消选择</button>
      </div>

      <div class="table-scroll-hint">左右滑动查看完整表格与操作</div>
      <PagerBar :page="list.page" :page-size="list.pageSize" :total="list.total" noun="位读者" @update:page="setPage" />
      <div class="table-wrap">
        <table class="table is-striped">
          <thead>
            <tr>
              <th style="width: 38px">
                <input
                  class="row-check"
                  type="checkbox"
                  :checked="allChecked"
                  aria-label="全选本页读者"
                  @change="toggleAll"
                >
              </th>
              <ThSort label="读者" sort-key="name" :current="list.sortKey" :order="list.sortOrder" @sort="setSort" />
              <ThSort label="类型 / 班级" sort-key="class_name" :current="list.sortKey" :order="list.sortOrder" @sort="setSort" />
              <th class="col-hide-md">联系电话</th>
              <th>外借 / 上限</th>
              <ThSort label="状态" sort-key="status" :current="list.sortKey" :order="list.sortOrder" @sort="setSort" />
              <ThSort class="col-hide-md" label="注册日期" sort-key="created_at" :current="list.sortKey" :order="list.sortOrder" @sort="setSort" />
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <TableState v-if="list.loading || list.error || !list.items.length"
                        :colspan="8" :list="list" loading-text="正在读取读者…"
                        empty="当前没有符合条件的读者" :retry="loadList" />
            <template v-else>
              <tr v-for="reader in list.items" :key="reader.id">
                <td>
                  <input
                    class="row-check"
                    type="checkbox"
                    :checked="selected.includes(reader.id)"
                    :aria-label="`选择 ${reader.name}`"
                    @change="toggleOne(reader.id)"
                  >
                </td>
                <td>
                  <div class="reader-name-cell">
                    <span class="avatar" :class="avatarClass(reader.id)">{{ reader.name.slice(-2) }}</span>
                    <div class="reader-name-text">
                      <RouterLink class="cell-main" :to="`/admin/readers/${reader.id}`">{{ reader.name }}</RouterLink>
                      <div class="cell-sub">{{ reader.card_no }}</div>
                    </div>
                  </div>
                </td>
                <td>
                  <div class="cell-main" style="font-size: 12.5px">{{ reader.reader_type_label }}</div>
                  <div class="cell-sub">{{ reader.class_name || reader.grade || '未分班' }}</div>
                </td>
                <td class="col-hide-md cell-date">{{ reader.phone || '—' }}</td>
                <td>
                  <span class="stock-cell" :class="{ 'is-empty': reader.active_count >= maxItems(reader) }">
                    <strong>{{ reader.active_count }}</strong><span class="slash">/</span><span>{{ maxItems(reader) }}</span>
                  </span>
                  <span v-if="reader.overdue_count" class="overdue-pill">逾期 {{ reader.overdue_count }}</span>
                </td>
                <td>
                  <span
                    class="status-text"
                    :class="reader.status === 'ACTIVE' ? 'ok' : reader.status === 'SUSPENDED' ? 'warn' : 'muted'"
                  ><span class="dot" />{{ reader.status_label }}</span>
                </td>
                <td class="col-hide-md cell-date">{{ formatDate(reader.created_at) || '—' }}</td>
                <td>
                  <div class="row-actions">
                    <RouterLink class="text-link" :to="`/admin/readers/${reader.id}`">详情</RouterLink>
                    <template v-if="canWrite">
                      <button class="text-link" type="button" @click="openEdit(reader)">编辑</button>
                      <button
                        v-if="reader.status === 'ACTIVE'"
                        class="text-link"
                        type="button"
                        @click="askStatus(reader, 'SUSPENDED')"
                      >暂停</button>
                      <button
                        v-else-if="reader.status === 'SUSPENDED'"
                        class="text-link"
                        type="button"
                        @click="askStatus(reader, 'ACTIVE')"
                      >恢复</button>
                    </template>
                  </div>
                </td>
              </tr>
            </template>
          </tbody>
        </table>
      </div>
    </article>

    <ReaderFormModal
      :open="formOpen"
      :reader="editing"
      :library-id="libraryId"
      @close="formOpen = false"
      @saved="onSaved"
    />

    <ConfirmDialog
      :open="Boolean(statusTarget)"
      :title="statusTarget?.status === 'ACTIVE' ? '恢复读者' : statusTarget?.status === 'SUSPENDED' ? '暂停借阅' : '注销读者'"
      :message="statusTarget
        ? statusTarget.status === 'ACTIVE'
          ? `确认将 ${statusTarget.reader.name} 恢复为正常状态？`
          : statusTarget.status === 'SUSPENDED'
            ? `确认暂停 ${statusTarget.reader.name} 的借阅资格？其进行中的预约将同时取消。`
            : `确认注销 ${statusTarget.reader.name}？档案与借阅历史将保留，不会硬删除。`
        : ''"
      :confirm-text="statusTarget?.status === 'ACTIVE' ? '恢复' : statusTarget?.status === 'SUSPENDED' ? '暂停借阅' : '注销'"
      :danger="statusTarget?.status !== 'ACTIVE'"
      :busy="actionBusy"
      @cancel="statusTarget = null"
      @confirm="submitStatus"
    />

    <ConfirmDialog
      :open="bulkConfirmOpen"
      :title="`批量设为${statusLabel(bulkTarget)}`"
      :message="`将选中的 ${selectedCount} 位读者状态改为「${statusLabel(bulkTarget)}」。暂停或注销会取消其进行中的预约。`"
      :confirm-text="`确认改为${statusLabel(bulkTarget)}`"
      :danger="bulkTarget !== 'ACTIVE'"
      :busy="actionBusy"
      @cancel="bulkConfirmOpen = false"
      @confirm="submitBulkStatus"
    />

    <BaseModal
      :open="importOpen"
      title="导入读者"
      subtitle="按借书证号新增或更新，单次最多 5000 行"
      @close="importOpen = false"
    >
      <label class="import-drop" :class="{ 'is-busy': importBusy }">
        <input type="file" accept=".xlsx" :disabled="importBusy" aria-label="选择读者 Excel 文件" @change="onFilePicked">
        <AppIcon name="upload" />
        <strong>{{ importBusy ? '正在导入…' : pickedName || '选择 Excel 文件' }}</strong>
        <span>仅支持 .xlsx，第一行须为表头</span>
      </label>
      <div class="page-tools" style="margin: 12px 0 0">
        <button class="btn btn-outline btn-sm" type="button" @click="downloadTemplate">
          <AppIcon name="download" />下载导入模板
        </button>
        <span class="spacer" />
        <span class="page-tools-note">重复借书证号会更新资料，不会重复建立读者</span>
      </div>
      <p v-if="importError" class="field-note is-error" style="margin-top: 10px" role="alert">{{ importError }}</p>
      <div v-if="importResult" class="import-result">
        共读取 {{ importResult.total }} 行：新增 <strong>{{ importResult.created }}</strong>，
        更新 <strong>{{ importResult.updated }}</strong>，失败 <strong>{{ importResult.failed }}</strong>。
        <ul v-if="importResult.errors.length" class="import-errors">
          <li v-for="error in importResult.errors" :key="`${error.row}-${error.message}`">
            第 {{ error.row }} 行：{{ error.message }}
          </li>
        </ul>
        <p v-if="importResult.errors_truncated" class="field-note">仅显示前 200 条错误。</p>
      </div>
      <template #footer>
        <button class="btn btn-ghost" type="button" @click="importOpen = false">关闭</button>
      </template>
    </BaseModal>
  </section>
</template>
