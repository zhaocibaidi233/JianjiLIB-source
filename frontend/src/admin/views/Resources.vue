<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import AppIcon from '../../components/AppIcon.vue'
import BaseModal from '../../components/BaseModal.vue'
import ConfirmDialog from '../../components/ConfirmDialog.vue'
import PagerBar from '../../components/PagerBar.vue'
import TableState from '../../components/TableState.vue'
import ThSort from '../../components/ThSort.vue'
import { can } from '../../lib/auth'
import { del, downloadPost, errorMessage, get, post } from '../../lib/api'
import { sourceLabel } from '../../lib/displayLabels'
import { formatDate, formatIsbn, formatNumber } from '../../lib/format'
import { toast, toastError } from '../../lib/toast'
import { loadLibraries as loadLibraryStore } from '../../stores/libraries'

interface ResourceLibrary {
  id: number
  name: string
  origin: string | null
  record_count: number
  enabled: boolean
}

interface ResourceRecord {
  id: number
  title: string | null
  isbn: string | null
  author: string | null
  year: number | null
  class_no: string | null
  source_tag: string | null
  updated_at: string | null
}

interface RecordList {
  total: number
  page: number
  page_size: number
  items: ResourceRecord[]
}

interface Facets {
  source_tags: Array<{ value: string; count: number }>
  years: number[]
  total: number
}

interface MarcField {
  tag: string
  i1?: string
  i2?: string
  data?: string
  sf?: Array<[string, string]>
}

const router = useRouter()
const canWrite = computed(() => can('resources', 'write'))
const canIo = computed(() => can('io', 'write'))
const libraries = ref<ResourceLibrary[]>([])
const selectedId = ref<number | null>(null)
const selectedLibrary = computed(() => libraries.value.find((item) => item.id === selectedId.value) || null)
const records = reactive({ items: [] as ResourceRecord[], total: 0, page: 1, pageSize: 10, loading: false, error: '' })
const facets = ref<Facets>({ source_tags: [], years: [], total: 0 })
const yearRange = computed(() => {
  const years = facets.value.years.filter((year) => year > 0)
  if (!years.length) return '—'
  const min = Math.min(...years)
  const max = Math.max(...years)
  return min === max ? String(min) : `${min}–${max}`
})
const PAGE_SIZE_PRESETS = [10, 20, 50, 100]
const pageSizeChoice = ref('10')
const pageSizeOpen = ref(false)
const pageSizeDraft = ref(10)
const pageSizeIsCustom = computed(() => !PAGE_SIZE_PRESETS.includes(records.pageSize))
const pageSizeValid = computed(() => {
  const size = Number(pageSizeDraft.value)
  return Number.isInteger(size) && size >= 1 && size <= 500
})
const filters = reactive({ q: '', sourceTag: '', hasIsbn: '', classNo: '', year: '' })
const selected = ref<number[]>([])
const loading = ref(true)
const error = ref('')
let searchTimer = 0

const createOpen = ref(false)
const createForm = reactive({ name: '', origin: '' })
const createBusy = ref(false)
const createError = ref('')

const importOpen = ref(false)
const importFile = ref<File | null>(null)
const importBusy = ref(false)
const importResult = ref<{ added: number; updated: number; parse_errors: Array<{ index: number; error: string }> } | null>(null)
const importError = ref('')
const importInput = ref<HTMLInputElement | null>(null)

const deleteTarget = ref<ResourceLibrary | null>(null)
const clearTarget = ref<ResourceLibrary | null>(null)
const actionBusy = ref(false)
const deleteSelectedOpen = ref(false)

const preview = ref<{ id: number; record: { leader: string; fields: MarcField[] } } | null>(null)
const previewBusy = ref(false)
const exportMenu = ref<HTMLDetailsElement | null>(null)

async function loadLibraries() {
  try {
    libraries.value = await get<ResourceLibrary[]>('/resource-libraries')
    if (!selectedId.value && libraries.value.length) {
      selectedId.value = libraries.value[0].id
    } else if (selectedId.value && !libraries.value.some((item) => item.id === selectedId.value)) {
      selectedId.value = libraries.value[0]?.id || null
    }
  } catch (e) {
    error.value = errorMessage(e)
  }
}

async function loadFacets() {
  if (!selectedId.value) {
    facets.value = { source_tags: [], years: [], total: 0 }
    return
  }
  try {
    facets.value = await get<Facets>(`/resource-libraries/${selectedId.value}/facets`)
  } catch {
    facets.value = { source_tags: [], years: [], total: 0 }
  }
}

// 表头排序（后端 RESOURCE_SORTS 白名单；三态循环口径与 usePagedList.setSort 一致。
// 这个表是资源库详情的嵌套列表，状态本就自有一份 reactive，排序也留在这里）。
const sortKey = ref('')
const sortOrder = ref<'' | 'asc' | 'desc'>('')
function setSort(key: string) {
  if (sortKey.value !== key) {
    sortKey.value = key
    sortOrder.value = 'asc'
  } else if (sortOrder.value === 'asc') {
    sortOrder.value = 'desc'
  } else {
    sortKey.value = ''
    sortOrder.value = ''
  }
  records.page = 1
  void loadRecords()
}

async function loadRecords() {
  if (!selectedId.value) {
    records.items = []
    records.total = 0
    return
  }
  records.loading = true
  records.error = ''
  try {
    const data = await get<RecordList>(`/resource-libraries/${selectedId.value}/records`, {
      q: filters.q.trim() || undefined,
      source_tag: filters.sourceTag || undefined,
      has_isbn: filters.hasIsbn === '' ? undefined : filters.hasIsbn === 'yes',
      class_no: filters.classNo.trim() || undefined,
      year: filters.year || undefined,
      sort: sortKey.value || undefined,
      order: sortOrder.value || undefined,
      page: records.page,
      page_size: records.pageSize,
    })
    records.items = data.items
    records.total = data.total
    selected.value = selected.value.filter((id) => data.items.some((row) => row.id === id))
  } catch (e) {
    records.error = errorMessage(e)
    records.items = []
    records.total = 0
  } finally {
    records.loading = false
  }
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    await loadLibraries()
    await Promise.all([loadFacets(), loadRecords()])
  } finally {
    loading.value = false
  }
}

function resetPageAndLoad() {
  records.page = 1
  void loadRecords()
}

function onSearchInput() {
  window.clearTimeout(searchTimer)
  searchTimer = window.setTimeout(resetPageAndLoad, 280)
}

function setPage(page: number) {
  records.page = page
  void loadRecords()
}

function applyPageSize(size: number) {
  records.pageSize = size
  records.page = 1
  void loadRecords()
}

function onPageSizeChange() {
  if (pageSizeChoice.value === 'custom') {
    pageSizeDraft.value = records.pageSize
    pageSizeOpen.value = true
    return
  }
  applyPageSize(Number(pageSizeChoice.value))
}

function confirmPageSize() {
  if (!pageSizeValid.value) return
  pageSizeOpen.value = false
  applyPageSize(Number(pageSizeDraft.value))
  pageSizeChoice.value = 'custom'
}

function cancelPageSize() {
  pageSizeOpen.value = false
  pageSizeChoice.value = PAGE_SIZE_PRESETS.includes(records.pageSize)
    ? String(records.pageSize)
    : 'custom'
}

function chooseLibrary(id: number) {
  if (selectedId.value === id) return
  selectedId.value = id
  records.page = 1
  selected.value = []
  filters.sourceTag = ''
  filters.year = ''
  void Promise.all([loadFacets(), loadRecords()])
}

watch(selectedId, () => {
  if (selectedId.value) {
    void loadFacets()
    records.page = 1
    void loadRecords()
  }
})

const allChecked = computed(() =>
  records.items.length > 0 && records.items.every((row) => selected.value.includes(row.id)),
)
const selectedCount = computed(() => selected.value.length)

function toggleAll() {
  selected.value = allChecked.value ? [] : records.items.map((row) => row.id)
}

function toggleOne(id: number) {
  const index = selected.value.indexOf(id)
  if (index >= 0) selected.value.splice(index, 1)
  else selected.value.push(id)
}

/* ---------------- create ---------------- */
function openCreate() {
  createForm.name = ''
  createForm.origin = ''
  createError.value = ''
  createOpen.value = true
}

async function submitCreate() {
  if (!createForm.name.trim()) {
    createError.value = '请填写资源库名称'
    return
  }
  createBusy.value = true
  try {
    const data = await post<{ id: number; name: string }>('/resource-libraries', undefined, {
      params: { name: createForm.name.trim(), origin: createForm.origin.trim() },
    })
    toast('资源库已创建')
    createOpen.value = false
    await loadLibraries()
    selectedId.value = data.id
    await Promise.all([loadFacets(), loadRecords()])
  } catch (e) {
    createError.value = errorMessage(e)
  } finally {
    createBusy.value = false
  }
}

/* ---------------- import ISO ---------------- */
function openImport() {
  importFile.value = null
  importResult.value = null
  importError.value = ''
  importOpen.value = true
}

function pickImportFile(event: Event) {
  const input = event.target as HTMLInputElement
  importFile.value = input.files?.[0] || null
}

async function submitImport() {
  if (!selectedId.value || !importFile.value) {
    importError.value = '请选择资源库与书目文件'
    return
  }
  importBusy.value = true
  importError.value = ''
  try {
    const form = new FormData()
    form.append('file', importFile.value)
    importResult.value = await post(`/resource-libraries/${selectedId.value}/import-iso`, form)
    toast(`资源库导入完成：新增 ${importResult.value?.added ?? 0}，更新 ${importResult.value?.updated ?? 0}`)
    await Promise.all([loadLibraries(), loadFacets(), loadRecords()])
  } catch (e) {
    importError.value = errorMessage(e)
  } finally {
    importBusy.value = false
  }
}

/* ---------------- remove / clear ---------------- */
async function submitDeleteLibrary() {
  const target = deleteTarget.value
  if (!target) return
  actionBusy.value = true
  try {
    await del(`/resource-libraries/${target.id}`)
    toast('资源库已删除')
    deleteTarget.value = null
    selectedId.value = null
    await load()
  } catch (e) {
    toastError(errorMessage(e))
  } finally {
    actionBusy.value = false
  }
}

async function submitClear() {
  const target = clearTarget.value
  if (!target) return
  actionBusy.value = true
  try {
    const data = await post<{ cleared: number }>(`/resource-libraries/${target.id}/clear`)
    toast(`已清空 ${data.cleared} 条记录`)
    clearTarget.value = null
    await Promise.all([loadLibraries(), loadFacets(), loadRecords()])
  } catch (e) {
    toastError(errorMessage(e))
  } finally {
    actionBusy.value = false
  }
}

async function deleteRecords() {
  if (!selected.value.length) return
  actionBusy.value = true
  try {
    await post('/resource-records/bulk-delete', { ids: selected.value })
    toast(`已删除 ${selected.value.length} 条资源记录`)
    selected.value = []
    deleteSelectedOpen.value = false
    await Promise.all([loadLibraries(), loadFacets(), loadRecords()])
  } catch (e) {
    toastError(errorMessage(e))
  } finally {
    actionBusy.value = false
  }
}

/* ---------------- preview / export ---------------- */
const loadBusy = ref<number | null>(null)

async function loadToEditor(row: ResourceRecord) {
  if (loadBusy.value) return
  loadBusy.value = row.id
  try {
    const full = await get<{ id: number; record: { leader: string; fields: MarcField[] } }>(
      `/resource-records/${row.id}`,
    )
    const prep = await post<{ record: unknown }>('/cataloging/prepare', {
      record: full.record,
      agency: 'LOCAL',
    })
    sessionStorage.setItem('cataloging_temp', JSON.stringify(prep.record))
    await router.push('/admin/records/temp?from=resource')
  } catch (e) {
    toastError(errorMessage(e))
  } finally {
    loadBusy.value = null
  }
}

async function openPreview(row: ResourceRecord) {
  previewBusy.value = true
  try {
    preview.value = await get<{ id: number; record: { leader: string; fields: MarcField[] } }>(
      `/resource-records/${row.id}`,
    )
  } catch (e) {
    toastError(errorMessage(e))
  } finally {
    previewBusy.value = false
  }
}

function fieldText(field: MarcField): string {
  if (field.data) return field.data
  return (field.sf || []).map((pair) => `$${pair[0]} ${pair[1]}`).join('  ')
}

async function exportResource(format: 'excel' | 'iso') {
  if (!selectedId.value) return
  try {
    const body = {
      ids: selected.value.length ? selected.value : [],
      library_id: selected.value.length ? undefined : selectedId.value,
      encoding: 'utf-8',
    }
    await downloadPost(`/export/resource-${format}`, body, `资源库.${format === 'excel' ? 'xlsx' : 'iso'}`)
  } catch (e) {
    toastError(errorMessage(e))
  }
}

function pickExport(format: 'excel' | 'iso') {
  exportMenu.value?.removeAttribute('open')
  void exportResource(format)
}

function pickFile() {
  importInput.value?.click()
}

onMounted(async () => {
  await loadLibraryStore()
  await loadLibraries()
  await Promise.all([loadFacets(), loadRecords()])
  loading.value = false
})

onBeforeUnmount(() => window.clearTimeout(searchTimer))
</script>

<template>
  <section class="view">
    <div v-if="loading" class="card" aria-busy="true"><div class="skeleton" style="height: 400px; margin: 16px" /></div>
    <div v-else-if="error" class="card dash-error">
      <span class="error-icon"><AppIcon name="alert" /></span>
      <strong>无法读取资源库</strong>
      <p>{{ error }}</p>
      <button class="btn btn-primary btn-sm" type="button" @click="load">重新加载</button>
    </div>

    <div v-else class="resource-layout">
      <aside class="card resource-libs">
        <div class="card-head">
          <div>
            <div class="card-title">数据来源</div>
            <div class="card-sub">{{ libraries.length }} 个来源 · 不参与馆藏统计</div>
          </div>
          <button v-if="canWrite" class="btn btn-primary btn-sm" type="button" @click="openCreate">
            <AppIcon name="plus" />新建
          </button>
        </div>
        <div class="resource-lib-list">
          <button
            v-for="lib in libraries"
            :key="lib.id"
            class="resource-lib"
            :class="{ 'is-active': selectedId === lib.id }"
            type="button"
            @click="chooseLibrary(lib.id)"
          >
            <span class="metric-icon"><AppIcon name="database" /></span>
            <span class="resource-lib-info">
              <strong>{{ lib.name }}</strong>
              <small>{{ lib.origin || '本地资源库' }}<template v-if="!lib.enabled"> · 已停用</template></small>
            </span>
            <span class="resource-lib-count">{{ formatNumber(lib.record_count) }}</span>
          </button>
          <div v-if="!libraries.length" class="empty-card">当前没有资源库</div>
        </div>
        <div v-if="selectedLibrary" class="resource-summary">
          <div class="resource-summary-row">
            <span>记录数</span><strong>{{ formatNumber(selectedLibrary.record_count) }} 条</strong>
          </div>
          <div class="resource-summary-row">
            <span>年份</span><strong>{{ yearRange }}</strong>
          </div>
          <div class="resource-summary-row">
            <span>状态</span><strong>{{ selectedLibrary.enabled ? '启用中' : '已停用' }}</strong>
          </div>
        </div>
        <div v-if="selectedLibrary && canWrite" class="resource-lib-actions">
          <button class="btn btn-outline btn-sm" type="button" @click="openImport">
            <AppIcon name="upload" />导入机读目录
          </button>
          <button class="btn btn-ghost btn-sm" type="button" @click="clearTarget = selectedLibrary">清空</button>
          <button class="btn btn-ghost btn-sm" type="button" @click="deleteTarget = selectedLibrary">删除库</button>
        </div>
      </aside>

      <article class="data-card">
        <div class="toolbar">
          <div class="input-icon search-field">
            <input
              v-model="filters.q"
              class="input"
              placeholder="搜索题名、ISBN 或责任者"
              autocomplete="off"
              @input="onSearchInput"
              @keydown.enter.prevent="resetPageAndLoad"
            >
            <AppIcon name="search" />
          </div>
          <select v-model="filters.sourceTag" class="select" aria-label="来源">
            <option value="">全部来源</option>
            <option v-for="item in facets.source_tags" :key="item.value" :value="item.value">{{ item.value }}（{{ item.count }}）</option>
          </select>
          <select v-model="filters.hasIsbn" class="select" aria-label="ISBN">
            <option value="">ISBN 不限</option>
            <option value="yes">有 ISBN</option>
            <option value="no">无 ISBN</option>
          </select>
          <input v-model="filters.classNo" class="input is-compact" placeholder="分类号前缀" aria-label="分类号前缀">
          <select v-model="filters.year" class="select" aria-label="出版年">
            <option value="">全部年份</option>
            <option v-for="year in facets.years" :key="year" :value="year">{{ year }}</option>
          </select>
          <span class="toolbar-spacer" />
          <label class="page-size">
            <select v-model="pageSizeChoice" class="select page-size-select"
              aria-label="每页展示条数" @change="onPageSizeChange">
              <option value="10">10 条/页</option>
              <option value="20">20 条/页</option>
              <option value="50">50 条/页</option>
              <option value="100">100 条/页</option>
              <option value="custom">自定义{{ pageSizeIsCustom ? ` ${records.pageSize}` : '' }}</option>
            </select>
          </label>
          <span class="toolbar-count">共 <strong>{{ formatNumber(records.total) }}</strong> 条</span>
          <details v-if="canIo" ref="exportMenu" class="export-menu">
            <summary class="btn btn-outline btn-sm export-trigger">
              <AppIcon name="download" />导出<AppIcon name="chevron-down" :size="13" />
            </summary>
            <div class="export-menu-pop">
              <button type="button" :disabled="!selectedId" @click="pickExport('excel')">导出 Excel</button>
              <button type="button" :disabled="!selectedId" @click="pickExport('iso')">导出机读目录</button>
            </div>
          </details>
        </div>

        <div v-if="selectedCount" class="bulk-bar">
          <span>已选 <strong>{{ selectedCount }}</strong> 条资源记录</span>
          <span class="spacer" />
          <button v-if="canWrite" class="btn btn-ghost btn-sm" type="button" @click="deleteSelectedOpen = true">删除所选</button>
          <button class="btn btn-ghost btn-sm" type="button" @click="selected = []">取消选择</button>
        </div>

        <div class="table-scroll-hint">左右滑动查看完整表格与操作</div>
        <PagerBar :page="records.page" :page-size="records.pageSize" :total="records.total" noun="条资源记录" @update:page="setPage" />
        <div class="table-wrap">
          <table class="table is-striped">
            <thead><tr>
              <th style="width: 38px"><input class="row-check" type="checkbox" :checked="allChecked" aria-label="全选资源记录" @change="toggleAll"></th>
              <ThSort label="书目" sort-key="title" :current="sortKey" :order="sortOrder" @sort="setSort" />
              <ThSort label="ISBN" sort-key="isbn" :current="sortKey" :order="sortOrder" @sort="setSort" />
              <ThSort label="分类号" sort-key="class_no" :current="sortKey" :order="sortOrder" @sort="setSort" />
              <ThSort class="col-hide-md" label="来源" sort-key="source_tag" :current="sortKey" :order="sortOrder" @sort="setSort" />
              <ThSort class="col-hide-md" label="更新时间" sort-key="updated_at" :current="sortKey" :order="sortOrder" @sort="setSort" />
              <th>操作</th>
            </tr></thead>
            <tbody>
              <TableState v-if="records.loading || records.error || !records.items.length"
                          :colspan="7" :list="records" loading-text="正在读取资源库…"
                          empty="该资源库当前没有记录" />
              <template v-else>
                <tr v-for="row in records.items" :key="row.id">
                  <td><input class="row-check" type="checkbox" :checked="selected.includes(row.id)" :aria-label="`选择 ${row.title || row.id}`" @change="toggleOne(row.id)"></td>
                  <td>
                    <div class="cell-main">{{ row.title || '未知题名' }}</div>
                    <div class="cell-sub">{{ row.author || '佚名' }}<template v-if="row.year"> · {{ row.year }}</template></div>
                  </td>
                  <td><span class="code-chip">{{ formatIsbn(row.isbn) || '无 ISBN' }}</span></td>
                  <td>{{ row.class_no || '—' }}</td>
                  <td class="col-hide-md"><span class="badge badge-neutral no-dot">{{ sourceLabel(row.source_tag) }}</span></td>
                  <td class="col-hide-md cell-date">{{ formatDate(row.updated_at) }}</td>
                  <td>
                    <div class="row-actions">
                      <button class="text-link is-secondary" type="button" :disabled="previewBusy" @click="openPreview(row)">查看 MARC</button>
                      <button v-if="canWrite" class="text-link" type="button" :disabled="loadBusy === row.id" @click="loadToEditor(row)">
                        {{ loadBusy === row.id ? '载入中…' : '载入编目' }}
                      </button>
                    </div>
                  </td>
                </tr>
              </template>
            </tbody>
          </table>
        </div>
      </article>
    </div>

    <BaseModal :open="pageSizeOpen" title="自定义展示条数" size="sm" @close="cancelPageSize">
      <div class="field">
        <label for="page-size-custom">每页展示条数</label>
        <input id="page-size-custom" v-model.number="pageSizeDraft" class="input" type="number"
          min="1" max="500" placeholder="1–500" @keydown.enter.prevent="confirmPageSize">
      </div>
      <p class="field-note">支持 1–500 条</p>
      <template #footer>
        <button class="btn btn-ghost" type="button" @click="cancelPageSize">取消</button>
        <button class="btn btn-primary" type="button" :disabled="!pageSizeValid" @click="confirmPageSize">应用</button>
      </template>
    </BaseModal>

    <BaseModal :open="createOpen" title="新建资源库" size="sm" @close="createOpen = false">
      <div class="field">
        <label for="rl-name">资源库名称</label>
        <input id="rl-name" v-model="createForm.name" class="input" maxlength="128" placeholder="如 2026 秋季书商数据">
      </div>
      <div class="field" style="margin-top: 12px">
        <label for="rl-origin">来源说明</label>
        <input id="rl-origin" v-model="createForm.origin" class="input" maxlength="256" placeholder="书商、批次或数据说明">
      </div>
      <p v-if="createError" class="field-note is-error" style="margin-top: 10px" role="alert">{{ createError }}</p>
      <template #footer>
        <button class="btn btn-ghost" type="button" :disabled="createBusy" @click="createOpen = false">取消</button>
        <button class="btn btn-primary" type="button" :disabled="createBusy" @click="submitCreate">
          {{ createBusy ? '创建中…' : '创建资源库' }}
        </button>
      </template>
    </BaseModal>

    <BaseModal :open="importOpen" :title="`导入机读目录 · ${selectedLibrary?.name || ''}`" size="sm" @close="importOpen = false">
      <input ref="importInput" type="file" accept=".iso,.mrc,.marc" style="display: none" @change="pickImportFile">
      <div class="import-drop" tabindex="0" @click="pickFile" @keydown.enter.prevent="pickFile">
        <AppIcon name="upload" />
        <strong>{{ importFile?.name || '选择机读目录文件' }}</strong>
        <span>按 ISBN 更新，重复导入不会产生重复记录</span>
      </div>
      <div v-if="importResult" class="import-result">
        新增 {{ importResult.added }} 条，更新 {{ importResult.updated }} 条。
        <p v-if="importResult.parse_errors.length" class="field-note is-error">{{ importResult.parse_errors.length }} 条解析失败</p>
      </div>
      <p v-if="importError" class="field-note is-error" style="margin-top: 10px" role="alert">{{ importError }}</p>
      <template #footer>
        <button class="btn btn-ghost" type="button" :disabled="importBusy" @click="importOpen = false">关闭</button>
        <button class="btn btn-primary" type="button" :disabled="importBusy || !importFile" @click="submitImport">
          {{ importBusy ? '导入中…' : '开始导入' }}
        </button>
      </template>
    </BaseModal>

    <BaseModal :open="Boolean(preview)" title="资源记录 MARC" :subtitle="`资源记录 #${preview?.id || ''}`" size="lg" @close="preview = null">
      <div v-if="preview" class="marc-list">
        <div v-for="(field, index) in preview.record.fields" :key="`${field.tag}-${index}`" class="marc-row">
          <span class="tag">{{ field.tag }}</span>
          <span class="ind">{{ field.data ? '    ' : `${field.i1 || ' '}${field.i2 || ' '}` }}</span>
          <span class="value">{{ fieldText(field) }}</span>
        </div>
      </div>
      <template #footer>
        <button class="btn btn-primary" type="button" @click="preview = null">关闭</button>
      </template>
    </BaseModal>

    <ConfirmDialog
      :open="deleteSelectedOpen"
      title="删除资源记录"
      :message="`确认删除选中的 ${selectedCount} 条资源记录？馆藏与图书库不受影响。此操作不可恢复。`"
      confirm-text="删除"
      danger
      :busy="actionBusy"
      @cancel="deleteSelectedOpen = false"
      @confirm="deleteRecords"
    />

    <ConfirmDialog
      :open="Boolean(deleteTarget)"
      title="删除资源库"
      :message="deleteTarget ? `确认删除资源库「${deleteTarget.name}」及其中 ${deleteTarget.record_count} 条记录？此操作不可恢复。` : ''"
      confirm-text="删除资源库"
      danger
      :busy="actionBusy"
      @cancel="deleteTarget = null"
      @confirm="submitDeleteLibrary"
    />

    <ConfirmDialog
      :open="Boolean(clearTarget)"
      title="清空资源库"
      :message="clearTarget ? `确认清空「${clearTarget.name}」中的 ${clearTarget.record_count} 条记录？资源库本身会保留，此操作不可恢复。` : ''"
      confirm-text="清空记录"
      danger
      :busy="actionBusy"
      @cancel="clearTarget = null"
      @confirm="submitClear"
    />
  </section>
</template>

<style scoped>
.resource-layout {
  display: grid;
  grid-template-columns: minmax(260px, 0.55fr) minmax(0, 1.45fr);
  gap: 16px;
  align-items: stretch;
}
.resource-libs {
  overflow: hidden;
  display: flex;
  flex-direction: column;
  min-height: 0;
}
.resource-lib-list { flex: 1; min-height: 0; overflow-y: auto; padding: 6px 10px 10px; }
.resource-lib {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 10px 11px;
  border-radius: 9px;
  text-align: left;
  color: var(--muted);
}
.resource-lib:hover { background: var(--surface-soft); }
.resource-lib.is-active { background: var(--primary-soft); color: var(--primary-deep); }
.resource-lib .metric-icon { width: 30px; height: 30px; }
.resource-lib-info { min-width: 0; flex: 1; }
.resource-lib-info strong { display: block; font-size: 12.5px; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.resource-lib-info small { display: block; margin-top: 1px; font-size: 10px; color: var(--faint); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.resource-lib-count { flex: none; font-size: 11px; color: var(--faint); font-variant-numeric: tabular-nums; }
.resource-lib.is-active .resource-lib-count { color: var(--primary); font-weight: 600; }
.resource-summary {
  display: grid;
  gap: 6px;
  margin-top: auto;
  padding: 10px 22px;
  border-top: 1px solid var(--line);
}
.resource-summary-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 11.5px;
  color: var(--muted);
}
.resource-summary-row strong { color: var(--text); font-weight: 600; font-variant-numeric: tabular-nums; }
.resource-lib-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
  padding: 10px 12px;
  border-top: 1px solid var(--line);
}
.toolbar { gap: 8px; }
.toolbar .search-field { width: 210px; height: 36px; }
.toolbar .search-field .input { height: 36px; }
.toolbar .select { width: 108px; height: 36px; }
.toolbar > .input.is-compact { width: 110px; height: 36px; }
.toolbar .btn { height: 36px; }
.toolbar-count {
  display: inline-flex;
  align-items: center;
  height: 36px;
}
.page-size { display: inline-flex; align-items: center; height: 36px; }
.toolbar .page-size-select {
  width: 108px;
  height: 36px;
  padding: 0 26px 0 8px;
  font-size: 12px;
}
.text-link.is-secondary { color: var(--muted); }
.text-link.is-secondary:hover { color: var(--text); }
.export-menu { position: relative; }
.export-trigger { list-style: none; cursor: pointer; gap: 5px; }
.export-trigger::-webkit-details-marker { display: none; }
.export-menu[open] .export-trigger { border-color: var(--primary); color: var(--primary); }
.export-menu-pop {
  position: absolute;
  right: 0;
  top: calc(100% + 6px);
  z-index: 30;
  min-width: 128px;
  padding: 5px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: 10px;
  box-shadow: var(--shadow-md);
}
.export-menu-pop button {
  display: block;
  width: 100%;
  padding: 7px 10px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  font-size: 12.5px;
  color: var(--text);
  text-align: left;
  cursor: pointer;
}
.export-menu-pop button:hover:not(:disabled) { background: var(--surface-soft); color: var(--ink); }
.export-menu-pop button:disabled { color: var(--faint); cursor: default; }
@media (max-width: 1024px) {
  .resource-layout { grid-template-columns: 1fr; }
}
</style>
