<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import AppIcon from '../../components/AppIcon.vue'
import BaseModal from '../../components/BaseModal.vue'
import { can } from '../../lib/auth'
import { downloadPost, errorMessage, get, post } from '../../lib/api'
import { formatDateTime, formatNumber } from '../../lib/format'
import { toast, toastError } from '../../lib/toast'
import {
  fieldPriority,
  jobStatusLabel,
  jobTone,
  jobTypeLabel,
  keepAsIsLabel,
  resultRows,
  sameMapping,
} from '../composables/useIsoMapping'
import { libraryState, loadLibraries } from '../../stores/libraries'

type IoMode = 'iso' | 'excel' | 'batch' | 'export' | 'jobs'

interface FieldStat {
  tag: string
  code: string
  key: string
  count: number
  sample: string
  known: boolean
  suggested: string | null
}

interface TargetField {
  key: string
  tag: string
  code: string
  label: string
}

interface IsoIssueReason {
  key: string
  label: string
  count: number
}
interface IsoVerdict {
  target: 'records' | 'resource'
  total: number
  will_import: number
  will_skip: number
  no_isbn?: number
  reasons: IsoIssueReason[]
}
interface IsoPreviewRow {
  index: number
  title: string
  isbn: string
  class_no: string
  publisher: string
  year: string
  barcode: string
  /** 这条会被拦下的原因；空数组 = 能入库 */
  issues: string[]
  rows: Array<{
    tag: string
    ind: string
    control: boolean
    subfields: Array<[string, string]>
    text: string
  }>
}
interface IsoPreview {
  total: number
  parse_errors: Array<{ index: number; error: string }>
  parse_error_count: number
  with_barcode: number
  with_class_no: number
  page: number
  page_size: number
  /** 这次预演实际套用的映射（前端据此判断"映射改过了没"） */
  applied_mapping: Record<string, string>
  /** 两套落点各算一遍，切换目标时不必重新上传 */
  verdicts: Record<'records' | 'resource', IsoVerdict>
  sample: Array<{ title: string; isbn: string; class_no: string; publisher: string; year: string; barcode: string }>
  records: IsoPreviewRow[]
  field_stats: FieldStat[]
  suggested_mapping: Record<string, string>
  /** 文件里有、但没被自动关联的源字段及原因（如"905$b 在文件里已有值"） */
  mapping_notes: Record<string, string>
  target_fields: TargetField[]
  compat_fields: Array<{ source: string; target: string; label: string }>
}

interface ResourceLibrary {
  id: number
  name: string
  record_count: number
}

interface ExcelColumn {
  index: number
  name: string
  sample: string
}

interface MappingField {
  key: string
  label: string
  required: boolean
  kind: string
}

interface ExcelInspect {
  sheets: string[]
  sheet: string
  header_row: number
  columns: ExcelColumn[]
  mapping: Record<string, string>
  preview: string[][]
  fields: MappingField[]
  sheets_info: Array<{ name: string; max_row: number }>
  data_rows: number
}

interface ExcelRow {
  seq: string
  isbn: string
  title: string
  publisher: string
  year: string
  class_no: string
  book_no: string
  barcode: string
  missing_required: string[]
  warnings: string[]
  is_summary: boolean
}

interface ExcelParse {
  rows: ExcelRow[]
  sheet: string
  sheets: string[]
  read_rows: number
  summary: { total: number; summary_rows: number; with_warnings: number; warnings: string[] }
  created?: number
  updated?: number
  skipped?: number
  errors?: Array<{ row: string; reason: string }>
  error_count?: number
  imported?: number
  with_quality_errors?: number
}

interface JobRow {
  id: number
  type: string
  status: 'pending' | 'running' | 'done' | 'error'
  progress: number
  message: string
  result: Record<string, unknown> | null
  error: string | null
  created_at?: string
}

const mode = ref<IoMode>('iso')
const isoMappingOpen = ref(false)
const isoOpenRow = ref<number | null>(null)
const canWrite = computed(() => can('io', 'write'))
/**
 * 顶部只放**导入方式**。
 *
 * 原来把「导出」「后台任务」也平铺在这里，等于把「怎么导入」「反着来」「干完没有」
 * 三种东西摆成同一层，用户看不出自己在哪一步。现在导出与任务改成右上角入口，
 * 点开后在内容区显示，并给一条「返回导入」的退路。
 */
const groups = [
  { key: 'iso' as const, label: '机读目录导入' },
  { key: 'excel' as const, label: '发货清单' },
  { key: 'batch' as const, label: '批量套录' },
  // 导出放回标签栏：放到右上角当按钮太不显眼，用户找不着
  { key: 'export' as const, label: '导出' },
]
const sideMode = computed(() => mode.value === 'jobs')
const sideModeTitle = computed(() => '后台任务')
/** 右上角角标：还有几个任务在跑（pending / running）。 */
const runningJobCount = computed(
  () => jobs.value.filter((j) => j.status === 'pending' || j.status === 'running').length,
)

const bookLibraries = ref<Array<{ id: number; name: string }>>([])
const resourceLibraries = ref<ResourceLibrary[]>([])
const activeLibraryName = computed(
  () => libraryState.items.find((item) => item.id === libraryState.currentId)?.name || '—',
)

async function loadLibraryOptions() {
  bookLibraries.value = libraryState.items.map((item) => ({ id: item.id, name: item.name }))
  try {
    resourceLibraries.value = await get<ResourceLibrary[]>('/resource-libraries')
  } catch {
    resourceLibraries.value = []
  }
}

/* ---------------- ISO ---------------- */
const isoFile = ref<File | null>(null)
const isoPreview = ref<IsoPreview | null>(null)
const isoPreviewBusy = ref(false)
const isoError = ref('')
const iso = reactive({
  target: 'resource' as 'resource' | 'records',
  libraryId: '',
  barcodeStart: '',
  force: false,
})
const isoMapping = reactive<Record<string, string>>({})
const isoInput = ref<HTMLInputElement | null>(null)

const isoLibraryOptions = computed(() =>
  iso.target === 'resource'
    ? resourceLibraries.value.map((item) => ({ id: item.id, name: `${item.name}（${item.record_count}）` }))
    : bookLibraries.value,
)

function pickIso() {
  isoInput.value?.click()
}

async function onIsoPicked(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) return
  isoFile.value = file
  isoPreview.value = null
  isoError.value = ''
  isoPreviewBusy.value = true
  try {
    const data = await fetchIsoPreview(file, {})
    isoPreview.value = data
    for (const key of Object.keys(isoMapping)) delete isoMapping[key]
    normalizeMapping(data)
    // 后端不传 mapping 时会用系统建议，这里把同一份建议回填到下拉框，
    // 两边口径才一致（否则"预演用的是建议、界面显示没映射"）。
    for (const [src, target] of Object.entries(data.applied_mapping || {})) {
      isoMapping[src] = target
    }
  } catch (e) {
    isoError.value = errorMessage(e)
  } finally {
    isoPreviewBusy.value = false
  }
}

/** 上传并预演。`mapping` 变化时重跑一次（文件对象还在，不必让用户重选）。 */
async function fetchIsoPreview(file: File, mapping: Record<string, string>): Promise<IsoPreview> {
  const form = new FormData()
  form.append('file', file)
  const params: Record<string, unknown> = { limit: 50 }
  if (Object.keys(mapping).length) params.mapping = JSON.stringify(mapping)
  return post<IsoPreview>('/import/iso/preview', form, { params })
}

/** 当前界面上的映射（只保留有值的）。 */
function currentMapping(): Record<string, string> {
  const out: Record<string, string> = {}
  for (const [source, target] of Object.entries(isoMapping)) {
    if (target) out[source] = target
  }
  return out
}

/**
 * 给每个字段补一个空字符串的初始值。
 *
 * **不补会出真问题**：`isoMapping` 是空对象，`v-model="isoMapping[key]"` 拿到的是
 * `undefined`，而空选项的 value 是 `""` —— 两者对不上，`<select>` 的 selectedIndex
 * 就是 -1，界面上是个**完全空白**的下拉框，连"原样保留"都不显示。用户看到一片
 * 空框，自然以为映射功能坏了（2026-10-24 实测）。
 */
function normalizeMapping(data: IsoPreview) {
  for (const stat of data.field_stats) {
    if (!(stat.key in isoMapping)) isoMapping[stat.key] = ''
  }
}

/** 映射改过但还没重新预演 —— 此时第 4 步的结论是旧的，必须提示。 */
const isoMappingStale = computed(() => {
  if (!isoPreview.value) return false
  return !sameMapping(isoPreview.value.applied_mapping || {}, currentMapping())
})

async function rerunPreview() {
  if (!isoFile.value) return
  isoPreviewBusy.value = true
  isoError.value = ''
  try {
    const data = await fetchIsoPreview(isoFile.value, currentMapping())
    isoPreview.value = data
    normalizeMapping(data)
    isoOpenRow.value = null
    toast('已按当前映射重新预演')
  } catch (e) {
    isoError.value = errorMessage(e)
  } finally {
    isoPreviewBusy.value = false
  }
}

function applySuggestedMapping() {
  if (!isoPreview.value) return
  const suggested = isoPreview.value.suggested_mapping || {}
  const count = Object.keys(suggested).length
  if (!count) {
    // 没有可关联的字段时**不能**弹"已应用"——用户会以为按钮坏了。
    const note = Object.values(isoPreview.value.mapping_notes || {})[0]
    toast(note ? `没有需要自动关联的字段：${note}` : '没有需要自动关联的字段')
    return
  }
  for (const key of Object.keys(isoMapping)) delete isoMapping[key]
  Object.assign(isoMapping, suggested)
  toast(`已应用 ${count} 条系统建议`)
}

/** 建议条数：0 时「应用建议」按钮禁用，并说明为什么。 */
const isoSuggestCount = computed(
  () => Object.keys(isoPreview.value?.suggested_mapping || {}).length,
)

/** 映射三段式计数：无需映射（标准字段）/ 已映射 / 未识别（要人管）。 */
const isoMappingStats = computed(() => {
  let auto = 0
  let mapped = 0
  let need = 0
  for (const stat of isoPreview.value?.field_stats || []) {
    if (isoMapping[stat.key]) mapped += 1
    else if (stat.known) auto += 1
    else need += 1
  }
  return { auto, mapped, need }
})

/**
 * 映射表里只列"需要人管"的字段：未识别的 + 已映射的。
 * 标准字段（010$a、200$a 这类）本来就不用动，列出来只是让用户从上翻到下。
 */
/**
 * 映射表要列的字段。
 *
 * 2026-10-24 用户要求：**自动同名对应的字段也要列出来**——只列"需要处理的"虽然
 * 短，但用户没法核对系统到底把每个字段怎么了。所以默认全列，只是把**需要人管的
 * 排前面**（未识别 → 已映射 → 标准字段），出现次数多的再靠前。
 *
 * 「只看需要处理的」留成一个开关，想要短列表的人自己勾。
 */
const isoOnlyNeedsAttention = ref(false)

const isoFieldsToTune = computed(() =>
  (isoPreview.value?.field_stats || [])
    .filter((stat) => !isoOnlyNeedsAttention.value || !stat.known || isoMapping[stat.key])
    .slice()
    .sort((a, b) =>
      fieldPriority(a, isoMapping) - fieldPriority(b, isoMapping) || b.count - a.count,
    ),
)

// 不再截断：表格区自带滚动与吸顶表头，全列出来也看得清。
// 想要短列表的人勾上面的「只看需要处理的」。
const isoFieldsShown = computed(() => isoFieldsToTune.value)

const isoVerdict = computed(() => isoPreview.value?.verdicts?.[iso.target] || null)

async function startIsoImport() {
  if (!isoFile.value) {
    toastError('请选择书目文件')
    return
  }
  if (!canWrite.value) return
  if (iso.target === 'records' && !iso.libraryId) {
    toastError('导入图书库前请选择目标图书库')
    return
  }
  const mapping: Record<string, string> = {}
  for (const [source, target] of Object.entries(isoMapping)) {
    if (target) mapping[source] = target
  }
  try {
    const form = new FormData()
    form.append('file', isoFile.value)
    const data = await post<{ job_id: number; status: string }>('/jobs/import-iso', form, {
      params: {
        source: 'iso-upload',
        target: iso.target,
        library_id: iso.libraryId || undefined,
        barcode_start: iso.barcodeStart.trim() || undefined,
        force: iso.force,
        mapping: Object.keys(mapping).length ? JSON.stringify(mapping) : undefined,
      },
    })
    toast(`导入任务已创建（#${data.job_id}）`)
    mode.value = 'jobs'
    await loadJobs()
    startPolling(data.job_id)
  } catch (e) {
    toastError(errorMessage(e))
  }
}

/* ---------------- Excel ---------------- */
const excelFile = ref<File | null>(null)
const excelInspect = ref<ExcelInspect | null>(null)
const excelInspectBusy = ref(false)
const excelError = ref('')
const excelSheet = ref('')
const excelHeaderRow = ref(1)
const excelMapping = reactive<Record<string, string>>({})
const excelTarget = ref<'resource' | 'records'>('resource')
const excelLibrary = ref('')
const excelForce = ref(false)
const excelParse = ref<ExcelParse | null>(null)
const excelBusy = ref(false)
const excelInput = ref<HTMLInputElement | null>(null)
const excelSheets = ref<string[]>([])
const inspectedContext = ref<{ file: File; sheet: string; header: number; version: number } | null>(null)
let inspectVersion = 0
let parseVersion = 0
let applyingInspection = false
const excelReady = computed(() => {
  const context = inspectedContext.value
  return Boolean(excelInspect.value && context && !excelInspectBusy.value
    && context.version === inspectVersion && context.file === excelFile.value
    && context.sheet === excelSheet.value && context.header === excelHeaderRow.value)
})

const excelLibraryOptions = computed(() =>
  excelTarget.value === 'resource'
    ? resourceLibraries.value.map((item) => ({ id: item.id, name: `${item.name}（${item.record_count}）` }))
    : bookLibraries.value,
)
const excelMappedCount = computed(() => Object.values(excelMapping).filter(Boolean).length)

function pickExcel() {
  excelInput.value?.click()
}

function invalidateExcel() {
  inspectVersion += 1
  parseVersion += 1
  inspectedContext.value = null
  excelInspect.value = null
  excelParse.value = null
  excelInspectBusy.value = false
  excelError.value = ''
}

async function inspectExcel() {
  invalidateExcel()
  const file = excelFile.value
  const sheet = excelSheet.value
  const header = excelHeaderRow.value
  const version = inspectVersion
  if (!file) return
  if (!Number.isInteger(header) || header < 1) {
    excelError.value = '表头行必须是正整数'
    return
  }
  const current = () => version === inspectVersion && file === excelFile.value
    && sheet === excelSheet.value && header === excelHeaderRow.value
  excelInspectBusy.value = true
  try {
    const form = new FormData()
    form.append('file', file)
    const data = await post<ExcelInspect>('/import/delivery-inspect', form, {
      params: { sheet: sheet || undefined, header_row: header },
    })
    if (!current()) return
    // 服务端可能自动选择首张工作表；同步归一化时不再触发二次检查。
    applyingInspection = true
    try {
      excelSheet.value = data.sheet
      excelHeaderRow.value = data.header_row
      excelSheets.value = data.sheets
      for (const key of Object.keys(excelMapping)) delete excelMapping[key]
      for (const [column, field] of Object.entries(data.mapping)) excelMapping[column] = field || ''
      inspectedContext.value = { file, sheet: data.sheet, header: data.header_row, version }
      excelInspect.value = data
    } finally {
      applyingInspection = false
    }
  } catch (e) {
    if (current()) excelError.value = errorMessage(e)
  } finally {
    if (version === inspectVersion) excelInspectBusy.value = false
  }
}

async function onExcelPicked(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) return
  applyingInspection = true
  try {
    invalidateExcel()
    excelFile.value = file
    excelSheets.value = []
    excelSheet.value = ''
    excelHeaderRow.value = 1
  } finally {
    applyingInspection = false
  }
  await inspectExcel()
}

async function parseExcel(create: boolean) {
  const file = excelFile.value
  const context = inspectedContext.value
  const inspection = excelInspect.value
  if (!file || !context || !inspection || !excelReady.value || excelBusy.value) {
    toastError('请等待文件与表头检查完成')
    return
  }
  if (create && !canWrite.value) return
  if (!excelLibrary.value && excelTarget.value === 'records') {
    toastError('请选择目标图书库')
    return
  }
  const mapping: Record<string, string> = {}
  for (const [column, field] of Object.entries(excelMapping)) {
    if (!field) continue
    if (!inspection.columns.some((item) => String(item.index) === column)
      || !inspection.fields.some((item) => item.key === field)) {
      toastError('列映射与检查结果不一致，请重新检查')
      return
    }
    mapping[column] = field
  }
  const version = ++parseVersion
  const params = {
    create,
    target: excelTarget.value,
    library_id: excelLibrary.value || undefined,
    force: excelTarget.value === 'records' ? excelForce.value : undefined,
    sheet: context.sheet,
    header_row: context.header,
    mapping: JSON.stringify(mapping),
  }
  const current = () => version === parseVersion && excelReady.value
    && inspectedContext.value === context
  excelBusy.value = true
  excelError.value = ''
  try {
    const form = new FormData()
    form.append('file', file)
    const data = await post<ExcelParse>('/import/delivery-excel', form, { params })
    if (current()) excelParse.value = data
    if (create) {
      toast(`导入完成：新增 ${data.created ?? 0} 条`)
      await loadLibraryOptions()
    } else if (current()) {
      toast('解析预览已更新')
    }
  } catch (e) {
    if (current()) excelError.value = errorMessage(e)
  } finally {
    excelBusy.value = false
  }
}

watch([excelFile, excelSheet, excelHeaderRow], () => {
  if (!applyingInspection) void inspectExcel()
}, { flush: 'sync' })
// 人工映射无需重新检查文件，但旧解析预览必须失效。
watch([excelMapping, excelTarget, excelLibrary, excelForce], () => {
  parseVersion += 1
  excelParse.value = null
}, { deep: true, flush: 'sync' })
watch(excelTarget, () => {
  excelLibrary.value = ''
})
watch(
  () => iso.target,
  () => {
    iso.libraryId = ''
  },
)

/* ---------------- Batch cataloging ---------------- */
const batchFile = ref<File | null>(null)
const batchInput = ref<HTMLInputElement | null>(null)
const batchBusy = ref(false)
const useRemote = ref(true)

function pickBatch() {
  batchInput.value?.click()
}

function onBatchPicked(event: Event) {
  const input = event.target as HTMLInputElement
  batchFile.value = input.files?.[0] || null
  input.value = ''
}

async function startBatch() {
  if (!batchFile.value) {
    toastError('请先选择发货清单文件')
    return
  }
  batchBusy.value = true
  try {
    const form = new FormData()
    form.append('file', batchFile.value)
    const data = await post<{ job_id: number; status: string }>('/jobs/batch-cataloging', form, {
      params: { use_remote: useRemote.value },
    })
    toast(`批量套录任务已创建（#${data.job_id}）`)
    mode.value = 'jobs'
    await loadJobs()
    startPolling(data.job_id)
  } catch (e) {
    toastError(errorMessage(e))
  } finally {
    batchBusy.value = false
  }
}

/* ---------------- Export ---------------- */
const exportForm = reactive({
  source: 'records' as 'records' | 'resources',
  format: 'excel' as 'excel' | 'iso',
  libraryId: '',
  encoding: 'gbk',
})
const exportBusy = ref(false)
const exportLibraryOptions = computed(() =>
  exportForm.source === 'resources' ? resourceLibraries.value : bookLibraries.value,
)

async function runExport() {
  if (!exportForm.libraryId) {
    toastError('请选择要导出的库')
    return
  }
  exportBusy.value = true
  try {
    const prefix = exportForm.source === 'resources' ? 'resource-' : ''
    await downloadPost(`/export/${prefix}${exportForm.format}`, {
      library_id: Number(exportForm.libraryId),
      encoding: exportForm.encoding,
    }, `导出文件.${exportForm.format === 'excel' ? 'xlsx' : 'iso'}`)
    toast('导出已开始下载')
  } catch (e) {
    toastError(errorMessage(e))
  } finally {
    exportBusy.value = false
  }
}

watch(() => exportForm.source, () => {
  exportForm.libraryId = ''
})

/* ---------------- Jobs ---------------- */
const jobs = ref<JobRow[]>([])
const jobsLoading = ref(false)
const jobDetail = ref<JobRow | null>(null)
const activeJobId = ref<number | null>(null)
const missedBusy = ref(false)
let pollTimer = 0

async function loadJobs() {
  jobsLoading.value = true
  try {
    jobs.value = await get<JobRow[]>('/jobs')
  } catch (e) {
    toastError(errorMessage(e))
  } finally {
    jobsLoading.value = false
  }
}

async function openJob(id: number) {
  try {
    jobDetail.value = await get<JobRow>(`/jobs/${id}`)
  } catch (e) {
    toastError(errorMessage(e))
  }
}

async function sendMissedToDraft() {
  if (!jobDetail.value) return
  missedBusy.value = true
  try {
    const data = await post<{ library: string; imported: number; updated: number }>(
      `/jobs/${jobDetail.value.id}/missed-to-draft`,
    )
    toast(`未命中清单已转入「${data.library}」：新增 ${data.imported}，更新 ${data.updated}`)
    await Promise.all([loadJobs(), loadLibraryOptions()])
  } catch (e) {
    toastError(errorMessage(e))
  } finally {
    missedBusy.value = false
  }
}

async function pollJob(id: number) {
  try {
    const job = await get<JobRow>(`/jobs/${id}`)
    const index = jobs.value.findIndex((item) => item.id === id)
    if (index >= 0) jobs.value[index] = { ...jobs.value[index], ...job }
    else jobs.value.unshift(job)
    if (job.status === 'done' || job.status === 'error') {
      activeJobId.value = null
      await Promise.all([loadJobs(), loadLibraryOptions()])
      if (job.status === 'done') toast(`后台任务 #${id} 已完成`)
      else toastError(`后台任务 #${id} 失败：${job.error || '未知错误'}`)
      return
    }
    activeJobId.value = id
    pollTimer = window.setTimeout(() => void pollJob(id), 1200)
  } catch (e) {
    activeJobId.value = null
    toastError(errorMessage(e))
  }
}

function startPolling(id: number) {
  window.clearTimeout(pollTimer)
  void pollJob(id)
}

async function refreshAll() {
  await loadLibraryOptions()
  await loadJobs()
}

onMounted(async () => {
  await loadLibraries()
  await refreshAll()
})

onBeforeUnmount(() => {
  inspectVersion += 1
  parseVersion += 1
  window.clearTimeout(pollTimer)
})
</script>

<template>
  <section class="view">
    <div class="page-tools">
      <span class="page-tools-note">
        <AppIcon name="swap" />导入与导出都在服务端完成，大文件转入后台任务。
      </span>
      <span class="spacer" />
      <span class="page-tools-note">当前图书库：{{ activeLibraryName }}</span>
      <button class="btn btn-outline btn-sm" type="button" @click="mode = 'jobs'">
        <AppIcon name="clock" />后台任务<template v-if="runningJobCount">（{{ runningJobCount }}）</template>
      </button>
    </div>

    <div v-if="sideMode" class="io-back">
      <button class="text-link" type="button" @click="mode = 'iso'">← 返回导入</button>
      <span class="io-back-title">{{ sideModeTitle }}</span>
    </div>
    <div v-else class="tabs io-tabs" role="tablist">
      <button v-for="item in groups" :key="item.key" class="tab" type="button" :class="{ 'is-active': mode === item.key }" :aria-selected="mode === item.key" @click="mode = item.key">
        {{ item.label }}
      </button>
    </div>

    <template v-if="mode === 'iso'">
      <div class="io-flow">
        <section class="io-step">
          <div class="io-step-head">
            <span class="io-step-no">1</span>
            <div>
              <div class="io-step-title">选文件</div>
              <div class="io-step-sub">.iso / .mrc / .marc，GBK 或 UTF-8，单文件建议不超过 50 MB</div>
            </div>
          </div>
          <div class="io-step-body">
            <input ref="isoInput" type="file" accept=".iso,.mrc,.marc" style="display: none" @change="onIsoPicked">
            <div class="import-drop" tabindex="0" @click="pickIso" @keydown.enter.prevent="pickIso">
              <AppIcon name="upload" />
              <strong>{{ isoPreviewBusy ? '正在解析并预演…' : isoFile?.name || '选择机读目录文件' }}</strong>
              <span>{{ isoFile ? '点击可更换文件' : '只读文件，不写库' }}</span>
            </div>
            <div v-if="isoPreview" class="io-file-meta">
              <span><strong>{{ formatNumber(isoPreview.total) }}</strong> 条记录</span>
              <span v-if="isoPreview.parse_error_count" class="is-warn">{{ isoPreview.parse_error_count }} 条解析失败</span>
              <span v-else class="is-ok">全部解析成功</span>
              <span>{{ formatNumber(isoPreview.with_class_no) }} 条有分类号</span>
            </div>
            <p v-if="isoError" class="field-note is-error" style="margin-top: 10px" role="alert">{{ isoError }}</p>
          </div>
        </section>

        <section v-if="isoPreview" class="io-step">
          <div class="io-step-head">
            <span class="io-step-no">2</span>
            <div>
              <div class="io-step-title">选择导入目标</div>
              <div class="io-step-sub">两个导入目标的规则不同，选错会影响导入结果</div>
            </div>
          </div>
          <div class="io-step-body">
            <div class="io-target-grid">
              <button class="io-target" :class="{ 'is-active': iso.target === 'resource' }" type="button"
                @click="iso.target = 'resource'">
                <strong>资源库</strong>
                <span>按 ISBN 去重，作为套录数据源。不要求条码，不做质检。</span>
              </button>
              <button class="io-target" :class="{ 'is-active': iso.target === 'records' }" type="button"
                @click="iso.target = 'records'">
                <strong>图书库</strong>
                <span>正式入库。每条须有条码，逐条质检，不合格的记录将被跳过。</span>
              </button>
            </div>

            <div class="field" style="margin-top: 14px">
              <label>目标库</label>
              <select v-model="iso.libraryId" class="select">
                <option value="">{{ iso.target === 'resource' ? '自动创建「手工导入」库' : `使用默认图书库（${activeLibraryName}）` }}</option>
                <option v-for="item in isoLibraryOptions" :key="item.id" :value="item.id">{{ item.name }}</option>
              </select>
            </div>

            <template v-if="iso.target === 'records'">
              <div class="field" style="margin-top: 12px">
                <label for="iso-start">起始条码</label>
                <input id="iso-start" v-model="iso.barcodeStart" class="input" placeholder="留空使用文件条码或自动流水">
                <span class="hint">仅对无条码的记录生成，格式如 A00000001</span>
              </div>
              <label class="check-line" style="margin-top: 12px">
                <input v-model="iso.force" type="checkbox">忽略质检错误并强制入库
              </label>
            </template>
          </div>
        </section>

        <section v-if="isoPreview" class="io-step">
          <div class="io-step-head">
            <span class="io-step-no">3</span>
            <div style="flex: 1">
              <div class="io-step-title">字段映射</div>
              <div class="io-step-sub">
                无需映射 {{ isoMappingStats.auto }} · 已映射 {{ isoMappingStats.mapped }}
                · <span :class="{ 'is-warn': isoMappingStats.need }">未识别 {{ isoMappingStats.need }}</span>
              </div>
            </div>
            <button class="text-link" type="button" @click="isoMappingOpen = !isoMappingOpen">
              {{ isoMappingOpen ? '收起' : '展开调整' }}
            </button>
          </div>
          <div v-if="isoMappingOpen" class="io-step-body">
            <div class="io-map-bar">
              <label class="check-line">
                <input v-model="isoOnlyNeedsAttention" type="checkbox">只看需要处理的
              </label>
              <div class="io-map-actions">
                <span v-if="!isoSuggestCount" class="field-note">
                  系统没有需要自动关联的字段
                </span>
                <button class="btn btn-outline btn-sm" type="button"
                  :disabled="!isoSuggestCount" @click="applySuggestedMapping">
                  应用建议<template v-if="isoSuggestCount">（{{ isoSuggestCount }}）</template>
                </button>
              </div>
            </div>
            <div class="io-mapping-scroll">
              <table class="table io-mapping-table">
                <thead><tr><th>源字段</th><th>出现次数</th><th>示例</th><th>映射到</th></tr></thead>
                <tbody>
                  <tr v-for="stat in isoFieldsShown" :key="stat.key">
                    <td>
                      <span class="code-chip">{{ stat.key }}</span>
                      <span v-if="stat.known" class="io-tag"
                        title="系统可识别的标准字段，无需映射，原样保留">标准字段</span>
                    </td>
                    <td class="num">{{ formatNumber(stat.count) }}</td>
                    <td class="io-sample">{{ stat.sample || '—' }}</td>
                    <td>
                      <select v-model="isoMapping[stat.key]" class="select" style="min-width: 150px">
                        <option value="">{{ keepAsIsLabel(stat, isoPreview?.target_fields || []) }}</option>
                        <option v-for="field in isoPreview.target_fields" :key="field.key" :value="field.key">{{ field.key }} · {{ field.label }}</option>
                      </select>
                      <div v-if="!isoMapping[stat.key] && isoPreview.mapping_notes?.[stat.key]"
                        class="io-map-note">
                        {{ isoPreview.mapping_notes[stat.key] }}
                      </div>
                    </td>
                  </tr>
                  <tr v-if="!isoFieldsToTune.length">
                    <td colspan="4" class="table-state">所有字段都是标准字段，不需要调整。</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <p v-if="isoMappingStats.auto && isoOnlyNeedsAttention" class="field-note" style="margin-top: 8px">
              另有 {{ isoMappingStats.auto }} 个标准字段（如 010$a、200$a）会原样保留，无需处理。
            </p>
            <div v-if="isoMappingStale" class="io-stale">
              <AppIcon name="alert" />
              <span>映射改过了，下面的预演结论还是旧的。</span>
              <button class="btn btn-outline btn-sm" type="button" :disabled="isoPreviewBusy" @click="rerunPreview">
                {{ isoPreviewBusy ? '重算中…' : '重新预演' }}
              </button>
            </div>
          </div>
        </section>

        <section v-if="isoPreview" class="io-step">
          <div class="io-step-head">
            <span class="io-step-no">4</span>
            <div>
              <div class="io-step-title">导入结果预演</div>
              <div class="io-step-sub">按上面选择的导入目标预演，开始导入前即可查看结果</div>
            </div>
          </div>
          <div class="io-step-body">
            <div class="io-verdict">
              <div class="io-verdict-card">
                <span>将入库</span>
                <strong>{{ formatNumber(isoVerdict?.will_import || 0) }}</strong>
              </div>
              <div class="io-verdict-card" :class="{ 'is-bad': isoVerdict?.will_skip }">
                <span>将跳过</span>
                <strong>{{ formatNumber(isoVerdict?.will_skip || 0) }}</strong>
              </div>
              <div class="io-verdict-reasons">
                <span v-for="r in isoVerdict?.reasons || []" :key="r.key" class="io-reason">
                  {{ r.label }} · {{ r.count }}
                </span>
                <span v-if="!isoVerdict?.reasons?.length" class="io-reason is-ok">没有会被拦下的记录</span>
              </div>
            </div>
            <p v-if="iso.target === 'records' && isoVerdict?.will_skip && !iso.force" class="field-note" style="margin-top: 8px">
              这些记录不会入库。想全部收进来，可以在第 2 步勾「忽略质检错误并强制入库」。
            </p>

            <div class="io-preview-head">
              <span class="field-note">
                下面是前 {{ isoPreview.records.length }} 条（共 {{ formatNumber(isoPreview.total) }} 条），
                点一行看它的 MARC 字段。想看整个文件，用「全局设置 → MARC 浏览」。
              </span>
            </div>
            <div class="table-wrap io-preview-table">
              <table class="table">
                <thead>
                  <tr><th style="width: 46px">#</th><th>题名</th><th style="width: 150px">ISBN</th>
                    <th style="width: 96px">分类号</th><th style="width: 64px">年份</th>
                    <th style="width: 100px">条码</th><th style="width: 130px">预演</th></tr>
                </thead>
                <tbody>
                  <template v-for="row in isoPreview.records" :key="row.index">
                    <tr class="io-preview-row" :class="{ 'has-issue': row.issues.length }"
                      @click="isoOpenRow = isoOpenRow === row.index ? null : row.index">
                      <td class="num">{{ row.index }}</td>
                      <td><div class="cell-main">{{ row.title || '（无题名）' }}</div></td>
                      <td>{{ row.isbn || '—' }}</td>
                      <td>{{ row.class_no || '—' }}</td>
                      <td>{{ row.year || '—' }}</td>
                      <td>{{ row.barcode || '—' }}</td>
                      <td>
                        <span v-if="!row.issues.length" class="io-ok">可入库</span>
                        <span v-else class="io-bad" :title="row.issues.join('；')">
                          {{ row.issues[0] }}<template v-if="row.issues.length > 1"> +{{ row.issues.length - 1 }}</template>
                        </span>
                      </td>
                    </tr>
                    <tr v-if="isoOpenRow === row.index" class="io-fields-row">
                      <td colspan="7">
                        <div class="io-fields">
                          <div v-for="(f, fi) in row.rows" :key="fi" class="io-field">
                            <span class="io-field-tag">{{ f.tag }}</span>
                            <span class="io-field-ind">{{ f.control ? '' : f.ind }}</span>
                            <span class="io-field-val">
                              <template v-if="f.control">{{ f.text || '—' }}</template>
                              <template v-else>
                                <span v-for="(sf, si) in f.subfields" :key="si" class="io-field-sf">
                                  <i>${{ sf[0] }}</i>{{ sf[1] }}
                                </span>
                              </template>
                            </span>
                          </div>
                        </div>
                      </td>
                    </tr>
                  </template>
                </tbody>
              </table>
            </div>
          </div>
        </section>

        <div v-if="isoPreview" class="io-submit">
          <div class="io-submit-text">
            <strong>{{ formatNumber(isoVerdict?.will_import || 0) }}</strong> 条 →
            {{ iso.target === 'resource' ? '资源库' : '图书库' }}
            <template v-if="isoVerdict?.will_skip">，跳过 {{ isoVerdict.will_skip }} 条</template>
          </div>
          <button class="btn btn-primary" type="button"
            :disabled="!isoPreview || !canWrite || isoPreviewBusy" @click="startIsoImport">
            <AppIcon name="upload" />开始导入
          </button>
        </div>
        <p v-if="!canWrite" class="field-note">当前账号无导入权限</p>
      </div>
    </template>

    <template v-else-if="mode === 'excel'">
      <div class="io-layout">
        <article class="card">
          <div class="card-head">
            <div>
              <div class="card-title">发货清单 Excel</div>
              <div class="card-sub">支持工作表与表头行选择、手动列映射</div>
            </div>
          </div>
          <div class="card-body">
            <input ref="excelInput" type="file" accept=".xlsx" style="display: none" @change="onExcelPicked">
            <div class="import-drop" tabindex="0" @click="pickExcel" @keydown.enter.prevent="pickExcel">
              <AppIcon name="file" />
              <strong>{{ excelInspectBusy ? '正在读取工作表…' : excelFile?.name || '选择发货清单 .xlsx' }}</strong>
              <span>可先解析预览，再决定是否写入</span>
            </div>
            <div v-if="excelFile" class="form-grid" style="margin-top: 14px">
              <div class="field">
                <label>工作表</label>
                <select v-model="excelSheet" class="select">
                  <option v-for="sheet in excelSheets" :key="sheet" :value="sheet">{{ sheet }}</option>
                </select>
              </div>
              <div class="field">
                <label>表头行</label>
                <input v-model.number="excelHeaderRow" class="input" type="number" min="1">
              </div>
            </div>
            <div class="field" style="margin-top: 12px">
              <label>导入目标</label>
              <select v-model="excelTarget" class="select">
                <option value="resource">资源库（草稿）</option>
                <option value="records">图书库（直接入库）</option>
              </select>
            </div>
            <div class="field" style="margin-top: 12px">
              <label>目标库</label>
              <select v-model="excelLibrary" class="select">
                <option value="">{{ excelTarget === 'resource' ? '自动创建「发货清单草稿」库' : '使用默认图书库' }}</option>
                <option v-for="item in excelLibraryOptions" :key="item.id" :value="item.id">{{ item.name }}</option>
              </select>
            </div>
            <label v-if="excelTarget === 'records'" class="check-line" style="margin-top: 12px">
              <input v-model="excelForce" type="checkbox">忽略质检错误并强制入库
            </label>
            <p v-if="excelError" class="field-note is-error" style="margin-top: 10px" role="alert">{{ excelError }}</p>
            <div class="page-tools" style="margin: 14px 0 0">
              <button class="btn btn-outline btn-sm" type="button" :disabled="!excelReady || excelBusy" @click="parseExcel(false)">解析预览</button>
              <span class="spacer" />
              <button class="btn btn-primary btn-sm" type="button" :disabled="!excelReady || !canWrite || excelBusy" @click="parseExcel(true)">
                {{ excelBusy ? '处理中…' : '确认导入' }}
              </button>
            </div>
          </div>
        </article>

        <article class="card io-mapping-card">
          <div class="card-head">
            <div>
              <div class="card-title">列映射</div>
              <div class="card-sub">{{ excelInspect ? `${excelMappedCount} 列已映射 · ${excelInspect.data_rows} 行数据` : '选择文件后显示列对照' }}</div>
            </div>
          </div>
          <div v-if="!excelInspect" class="empty-state io-empty">
            <AppIcon name="upload" />
            <strong>尚未选择发货清单</strong>
            <p>支持 .xlsx，解析后可调整列映射并预览必填项。</p>
          </div>
          <div v-else class="io-preview">
            <div class="io-mapping-scroll">
              <table class="table io-mapping-table">
                <thead><tr><th>Excel 列</th><th>示例</th><th>映射字段</th></tr></thead>
                <tbody>
                  <tr v-for="column in excelInspect.columns" :key="column.index">
                    <td><div class="cell-main" style="font-size: 12px">{{ column.name || `第 ${column.index + 1} 列` }}</div></td>
                    <td class="io-sample">{{ column.sample || '—' }}</td>
                    <td>
                      <select v-model="excelMapping[String(column.index)]" class="select" style="min-width: 160px">
                        <option value="">原样保留（不搬运）</option>
                        <option v-for="field in excelInspect.fields" :key="field.key" :value="field.key">
                          {{ field.label }}{{ field.required ? ' *' : '' }}
                        </option>
                      </select>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </article>
      </div>

      <article v-if="excelParse" class="data-card io-result-card">
        <div class="card-head">
          <div>
            <div class="card-title">解析结果</div>
            <div class="card-sub">
              读取 {{ excelParse.read_rows }} 行
              <template v-if="excelParse.summary"> · 警告 {{ excelParse.summary.with_warnings }} 行</template>
            </div>
          </div>
        </div>
        <div class="table-wrap">
          <table class="table">
            <thead><tr><th>序号</th><th>题名</th><th>ISBN</th><th>条码</th><th>缺失项</th></tr></thead>
            <tbody>
              <tr v-for="(row, index) in excelParse.rows.slice(0, 30)" :key="index">
                <td>{{ row.seq }}</td>
                <td><div class="cell-main">{{ row.title || '—' }}</div></td>
                <td>{{ row.isbn || '—' }}</td>
                <td>{{ row.barcode || '—' }}</td>
                <td>
                  <span v-if="row.missing_required.length" class="status-text danger"><span class="dot" />{{ row.missing_required.join('、') }}</span>
                  <span v-else class="status-text ok"><span class="dot" />完整</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <div v-if="excelParse.created !== undefined" class="import-result">
          导入完成：新增 {{ excelParse.created ?? 0 }} 条<template v-if="excelParse.updated !== undefined">，更新 {{ excelParse.updated }} 条</template><template v-if="excelParse.skipped !== undefined">，跳过 {{ excelParse.skipped }} 条</template>。
        </div>
      </article>
    </template>

    <template v-else-if="mode === 'batch'">
      <div class="io-layout">
        <article class="card">
          <div class="card-head">
            <div>
              <div class="card-title">批量套录</div>
              <div class="card-sub">逐行查重 → 本地资源库 → 外部套录源</div>
            </div>
          </div>
          <div class="card-body">
            <input ref="batchInput" type="file" accept=".xlsx" style="display: none" @change="onBatchPicked">
            <div class="import-drop" tabindex="0" @click="pickBatch" @keydown.enter.prevent="pickBatch">
              <AppIcon name="file" />
              <strong>{{ batchFile?.name || '选择发货清单 .xlsx' }}</strong>
              <span>命中数据写入资源库，未命中行保留在任务结果中</span>
            </div>
            <label class="check-line" style="margin-top: 14px">
              <input v-model="useRemote" type="checkbox">允许查询外部 Z39.50 套录源
            </label>
            <button class="btn btn-primary btn-block" type="button" style="margin-top: 16px" :disabled="!batchFile || !canWrite || batchBusy" @click="startBatch">
              <AppIcon name="wand" />{{ batchBusy ? '创建中…' : '开始批量套录' }}
            </button>
            <p v-if="!canWrite" class="field-note" style="margin-top: 8px">当前账号无导入权限</p>
          </div>
        </article>
        <article class="card">
          <div class="card-head">
            <div class="card-title">处理方式</div>
          </div>
          <div class="card-body">
            <div class="info-line"><span>命中</span><strong>写入资源库，由编目员逐条载入校对</strong></div>
            <div class="info-line"><span>未命中</span><strong>整行保留在任务结果，可一键转待编目草稿</strong></div>
            <div class="info-line"><span>外部源</span><strong>仅查询已启用且测试通过的 Z39.50 源</strong></div>
            <div class="notice-strip">
              <AppIcon name="info" />
              <span>批量套录不直接写入图书库。任务完成后可在后台任务详情中查看命中与未命中数量。</span>
            </div>
          </div>
        </article>
      </div>
    </template>

    <template v-else-if="mode === 'export'">
      <div class="io-layout">
        <article class="card">
          <div class="card-head">
            <div>
              <div class="card-title">批量导出</div>
              <div class="card-sub">按图书库或资源库整库导出</div>
            </div>
          </div>
          <div class="card-body">
            <div class="field">
              <label>数据来源</label>
              <select v-model="exportForm.source" class="select">
                <option value="records">图书库</option>
                <option value="resources">资源库</option>
              </select>
            </div>
            <div class="field" style="margin-top: 12px">
              <label>选择库</label>
              <select v-model="exportForm.libraryId" class="select">
                <option value="">请选择</option>
                <option v-for="item in exportLibraryOptions" :key="item.id" :value="item.id">{{ item.name }}</option>
              </select>
            </div>
            <div class="form-grid" style="margin-top: 12px">
              <div class="field">
                <label>格式</label>
                <select v-model="exportForm.format" class="select">
                  <option value="excel">Excel</option>
                  <option value="iso">机读目录（CNMARC）</option>
                </select>
              </div>
              <div class="field">
                <label>ISO 编码</label>
                <select v-model="exportForm.encoding" class="select" :disabled="exportForm.format !== 'iso'">
                  <option value="gbk">GBK</option>
                  <option value="utf-8">UTF-8</option>
                </select>
              </div>
            </div>
            <button class="btn btn-primary btn-block" type="button" style="margin-top: 16px" :disabled="!exportForm.libraryId || exportBusy" @click="runExport">
              <AppIcon name="download" />{{ exportBusy ? '生成中…' : '导出并下载' }}
            </button>
            <p class="field-note" style="margin-top: 10px">导出结果按当前编目数据生成。如需改写字段，请在全局设置中维护导出预设。</p>
          </div>
        </article>
        <article class="card">
          <div class="card-head">
            <div class="card-title">说明</div>
          </div>
          <div class="card-body">
            <div class="info-line"><span>Excel</span><strong>一册一行，含 ISBN、题名、责任者、分类与馆藏信息</strong></div>
            <div class="info-line"><span>机读目录</span><strong>按 CNMARC 输出，可选择 GBK 编码供旧系统使用</strong></div>
            <div class="info-line"><span>资源库</span><strong>导出原始套录字段，不做图书库字段映射</strong></div>
            <div class="notice-strip">
              <AppIcon name="info" />
              <span>导出不修改数据。数据量较大时建议按图书库分批导出。</span>
            </div>
          </div>
        </article>
      </div>
    </template>

    <template v-else>
      <article class="data-card">
        <div class="card-head">
          <div>
            <div class="card-title">后台任务</div>
            <div class="card-sub">显示最近 20 个导入与套录任务。服务重启将中断未完成的任务。</div>
          </div>
          <div class="card-head-actions">
            <span v-if="activeJobId" class="badge badge-warn no-dot">任务 #{{ activeJobId }} 执行中</span>
            <button class="btn btn-outline btn-sm" type="button" :disabled="jobsLoading" @click="loadJobs">
              <AppIcon name="refresh" />刷新
            </button>
          </div>
        </div>
        <div class="table-scroll-hint">左右滑动查看完整任务信息</div>
        <div class="table-wrap">
          <table class="table">
            <thead><tr><th>任务</th><th>状态</th><th>进度</th><th>消息</th><th class="col-hide-md">创建时间</th><th>结果</th></tr></thead>
            <tbody>
              <tr v-if="jobsLoading"><td colspan="6" class="table-state"><span class="spinner" />正在读取任务…</td></tr>
              <tr v-else-if="!jobs.length"><td colspan="6" class="table-state">当前没有后台任务</td></tr>
              <template v-else>
                <tr v-for="job in jobs" :key="job.id">
                  <td>
                    <div class="cell-main">#{{ job.id }} · {{ jobTypeLabel(job.type) }}</div>
                  </td>
                  <td><span class="status-text" :class="jobTone(job.status)"><span class="dot" />{{ jobStatusLabel(job.status) }}</span></td>
                  <td style="min-width: 120px">
                    <div class="job-progress"><span :style="{ width: `${job.progress || 0}%` }" /></div>
                    <div class="cell-sub">{{ job.progress || 0 }}%</div>
                  </td>
                  <td class="io-sample">{{ job.message || '—' }}</td>
                  <td class="col-hide-md cell-date">{{ formatDateTime(job.created_at) }}</td>
                  <td>
                    <div class="row-actions">
                      <button class="text-link" type="button" @click="openJob(job.id)">查看</button>
                    </div>
                  </td>
                </tr>
              </template>
            </tbody>
          </table>
        </div>
      </article>
    </template>

    <BaseModal :open="Boolean(jobDetail)" title="任务详情" :subtitle="jobDetail ? `#${jobDetail.id} · ${jobTypeLabel(jobDetail.type)}` : ''" size="sm" @close="jobDetail = null">
      <template v-if="jobDetail">
        <div class="info-row"><span>状态</span><strong>{{ jobStatusLabel(jobDetail.status) }}</strong></div>
        <div class="info-row"><span>进度</span><strong>{{ jobDetail.progress || 0 }}%</strong></div>
        <div class="info-row"><span>消息</span><strong>{{ jobDetail.message || '—' }}</strong></div>
        <div v-if="jobDetail.error" class="import-result" style="color: var(--danger)">{{ jobDetail.error }}</div>
        <div v-if="jobDetail.result" class="job-result">
          <div v-for="row in resultRows(jobDetail.result)" :key="row.label" class="info-row">
            <span>{{ row.label }}</span><strong>{{ row.value }}</strong>
          </div>
          <button
            v-if="jobDetail.type === 'batch-cataloging' && Number(jobDetail.result.missed_count || 0) > 0"
            class="btn btn-outline btn-sm btn-block"
            type="button"
            style="margin-top: 10px"
            :disabled="missedBusy"
            @click="sendMissedToDraft"
          >
            {{ missedBusy ? '转入中…' : '未命中清单转待编目草稿' }}
          </button>
        </div>
      </template>
      <template #footer>
        <button class="btn btn-primary" type="button" @click="jobDetail = null">关闭</button>
      </template>
    </BaseModal>
  </section>
</template>

<style scoped>
.io-tabs { margin-bottom: 16px; }
.io-layout {
  display: grid;
  grid-template-columns: minmax(420px, 0.85fr) minmax(0, 1.15fr);
  gap: 16px;
  align-items: stretch;
}
.io-layout > .card {
  display: flex;
  flex-direction: column;
  min-height: 0;
}
.io-layout > .card > .card-head { flex: none; }
.io-layout > .card > .card-body,
.io-layout > .card > .io-preview { flex: 1; min-height: 0; }
.io-layout > .card > .card-body { display: flex; flex-direction: column; }
.io-file-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 14px;
  margin-top: 10px;
  font-size: 11px;
  color: var(--faint);
}
.io-file-meta strong { color: var(--text); }
.io-empty { flex: 1; justify-content: center; }
.io-preview { padding-bottom: 12px; display: flex; flex-direction: column; }
.io-mapping-scroll { min-height: 240px; max-height: 420px; overflow: auto; margin: 0 18px; border: 1px solid var(--line); border-radius: 9px; }
.io-mapping-table { min-width: 520px; }
.io-mapping-table th { position: sticky; top: 0; z-index: 1; }
.io-mapping-table .select { height: 32px; font-size: 12px; width: 100%; }
.io-sample {
  max-width: 220px;
  font-size: 11px;
  color: var(--muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.io-layout .info-line {
  display: grid;
  grid-template-columns: 60px minmax(0, 1fr);
  gap: 14px;
  align-items: start;
}
.io-layout .info-line strong { text-align: left; word-break: normal; }
.io-layout .card-body > .notice-strip { margin-top: auto; margin-bottom: 0; }
.io-result-card { margin-top: 16px; }
.io-result-card .table-wrap { max-height: 460px; overflow: auto; }
.io-result-card .table th { position: sticky; top: 0; z-index: 1; }
.io-result-card .import-result { margin: 0 18px 16px; }
.job-progress {
  height: 5px;
  border-radius: 99px;
  background: var(--surface-soft);
  overflow: hidden;
}
.job-progress span { display: block; height: 100%; background: var(--primary); border-radius: inherit; }
.job-result { margin-top: 10px; }
@media (max-width: 1100px) {
  .io-layout { grid-template-columns: 1fr; }
}
/* ===== 单列步骤流（机读目录导入） =====
   为什么要步骤化：原来「选文件 / 选落点 / 映射 / 预览 / 提交」挤在两栏里，
   视线上来回跳，用户不知道下一步该干什么。改成纵向四步 + 底部提交条，
   阅读顺序就是操作顺序。 */
.io-flow {
  display: flex;
  flex-direction: column;
  gap: 12px;
  max-width: 940px;
  /* 底部提交条是 sticky 的，留出高度免得它压住最后一张卡片 */
  padding-bottom: 64px;
}
.io-step {
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  padding: 14px 16px;
}
.io-step-head {
  display: flex;
  align-items: flex-start;
  gap: 10px;
}
.io-step-no {
  flex: 0 0 22px;
  height: 22px;
  margin-top: 1px;
  border-radius: 50%;
  background: var(--primary);
  color: #fff;
  font-size: 12px;
  line-height: 22px;
  text-align: center;
  font-variant-numeric: tabular-nums;
}
.io-step-title { font-size: 14px; font-weight: 600; color: var(--text); }
.io-step-sub { margin-top: 2px; font-size: 12px; color: var(--text-secondary); }
.io-step-body { margin-top: 12px; padding-left: 32px; }

/* 落点二选一：两个并排卡片，各自一句话说明后果 */
.io-target-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
}
.io-target {
  text-align: left;
  padding: 10px 12px;
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  background: var(--surface);
  cursor: pointer;
  transition: border-color 0.15s, background 0.15s;
}
.io-target strong { display: block; font-size: 13px; color: var(--text); }
.io-target span { display: block; margin-top: 4px; font-size: 12px; line-height: 1.5; color: var(--text-secondary); }
.io-target:hover { border-color: var(--line-strong); }
.io-target.is-active { border-color: var(--primary); background: var(--primary-softer); }

.io-map-bar { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 8px; }
.io-tag {
  margin-left: 6px;
  padding: 0 5px;
  font-size: 11px;
  line-height: 16px;
  color: var(--text-secondary);
  background: var(--surface-soft);
  border-radius: 3px;
}
.io-stale {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 10px;
  padding: 8px 10px;
  font-size: 12px;
  color: var(--accent-deep);
  background: var(--accent-soft);
  border-radius: var(--radius-sm);
}
.io-stale .btn { margin-left: auto; }

/* 预演结论：两个大数字 + 原因标签 */
.io-verdict { display: flex; gap: 12px; align-items: stretch; flex-wrap: wrap; }
.io-verdict-card {
  flex: 0 0 118px;
  padding: 8px 12px;
  border-radius: var(--radius-sm);
  background: var(--surface-soft);
}
.io-verdict-card span { display: block; font-size: 12px; color: var(--text-secondary); }
.io-verdict-card strong { display: block; margin-top: 2px; font-size: 22px; font-weight: 600; color: var(--text); font-variant-numeric: tabular-nums; }
.io-verdict-card.is-bad strong { color: var(--danger); }
.io-verdict-reasons { flex: 1; display: flex; flex-wrap: wrap; gap: 6px; align-content: center; }
.io-reason {
  padding: 2px 8px;
  font-size: 12px;
  border-radius: 5px;
  background: var(--danger-soft);
  color: var(--danger-deep);
}
.io-reason.is-ok { background: var(--success-soft); color: var(--success); }

.io-preview-head { margin: 14px 0 8px; }
.io-preview-table { max-height: 420px; overflow: auto; }
.io-preview-row { cursor: pointer; }
.io-preview-row:hover td { background: var(--surface-hover, #f7f9f6); }
.io-preview-row.has-issue td { background: #fdf6f4; }
.io-ok { font-size: 12px; color: var(--success); }
.io-bad { font-size: 12px; color: var(--danger); }
/* 展开行是一个跨列的 td，正好命中全局 `.table td:last-child { text-align: right }`，
   字段值会被甩到最右边。这里用更具体的选择器扳回左对齐。 */
.io-preview-table .io-fields-row td {
  padding: 0;
  background: var(--surface-soft);
  text-align: left;
}
.io-fields { padding: 8px 12px 10px 34px; font-size: 12px; line-height: 1.85; }
.io-field { display: flex; gap: 8px; }
.io-field-tag { flex: 0 0 34px; color: var(--text-tertiary); font-variant-numeric: tabular-nums; }
.io-field-ind { flex: 0 0 20px; color: var(--accent-deep); white-space: pre; }
.io-field-val { flex: 1; word-break: break-all; }
.io-field-sf { margin-right: 10px; }
.io-field-sf i { margin-right: 1px; font-style: normal; font-size: 11px; color: var(--primary-600); }

/* 底部提交条 */
.io-submit {
  position: sticky;
  bottom: 0;
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 14px;
  padding: 10px 16px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  box-shadow: 0 -2px 12px -6px rgba(24, 36, 32, 0.18);
}
.io-submit-text { font-size: 13px; color: var(--text-secondary); }
.io-submit-text strong { color: var(--text); }

.io-file-meta .is-warn { color: var(--accent-deep); }
.io-file-meta .is-ok { color: var(--success); }
.io-step-sub .is-warn { color: var(--accent-deep); font-weight: 600; }

@media (max-width: 760px) {
  .io-target-grid { grid-template-columns: 1fr; }
  .io-step-body { padding-left: 0; }
}
.io-back {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 14px;
}
.io-back-title { font-size: 15px; font-weight: 600; color: var(--text); }
.io-map-actions { display: flex; align-items: center; gap: 10px; }
.io-map-note {
  margin-top: 3px;
  font-size: 11px;
  line-height: 1.5;
  color: var(--text-tertiary);
}
</style>
