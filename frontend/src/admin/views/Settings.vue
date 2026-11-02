<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import AppIcon from '../../components/AppIcon.vue'
import MarcBrowser from '../components/MarcBrowser.vue'
import BaseModal from '../../components/BaseModal.vue'
import ConfirmDialog from '../../components/ConfirmDialog.vue'
import Dictionaries from './Dictionaries.vue'
import Logs from './Logs.vue'
import { can } from '../../lib/auth'
import {
  SOURCE_TYPES,
  clone,
  priorityLevel,
  sourceEndpoint,
  sourceTypeLabel,
  type Source,
} from '../composables/useSourceHelpers'
import { del, errorMessage, get, post, put } from '../../lib/api'
import { formatDateTime, formatNumber } from '../../lib/format'
import { toast, toastError } from '../../lib/toast'
import { libraryState, loadLibraries } from '../../stores/libraries'

interface LibraryItem {
  id: number
  name: string
  note: string | null
  record_count: number
}
interface TypeRule {
  label: string
  loan_days: number
  max_items: number
  max_renew: number
  renew_days: number
}
interface Rules {
  types: Record<string, TypeRule>
  skip_weekends: boolean
  closed_dates: string[]
  block_on_overdue: boolean
  reservation_hold_days: number
  fines: { enabled: boolean; per_day: number; grace_days: number;
           max_amount: number; block_on_unpaid: boolean }
}
interface OptimizeRule {
  id: string
  name: string
  desc: string
  enabled: boolean
  value: string | null
  param_label: string | null
  param_options: string[]
}
interface SeatArea {
  id: number
  library_id: number
  library_name: string | null
  name: string
  floor: string | null
  description: string | null
  rows: number
  cols: number
  open_time: string
  close_time: string
  enabled: boolean
  seat_count: number
  booking_count: number
  active_booking_count: number
}
interface ResourceLibraryItem {
  id: number
  name: string
  origin: string | null
  record_count: number
  enabled: boolean
}
interface SourceForm {
  type: string
  name: string
  host: string
  port: number
  database: string
  user: string
  password: string
  charset: string
  priority: number
  enabled: boolean
}

type Section = 'libraries' | 'circulation' | 'cataloging' | 'seats' | 'sources' | 'dictionaries' | 'marc' | 'logs'

const SECTIONS: Array<{ key: Section; label: string; hint: string; icon: string }> = [
  { key: 'libraries', label: '图书库', hint: '分馆与默认库', icon: 'building' },
  { key: 'sources', label: '资源库', hint: '启停与外部套录源', icon: 'database' },
  { key: 'circulation', label: '借阅规则', hint: '借期、续借与罚款', icon: 'swap' },
  { key: 'cataloging', label: '编目规范', hint: '条码、分类号与规范', icon: 'wand' },
  { key: 'seats', label: '座位区域', hint: '开放时间与座位布局', icon: 'seat' },
  { key: 'dictionaries', label: '字典中心', hint: '著录候选与地点', icon: 'book-open' },
  { key: 'marc', label: 'MARC 浏览', hint: '打开文件看字段', icon: 'file' },
  { key: 'logs', label: '操作日志', hint: '书目变更与责任人', icon: 'receipt' },
]

const libraryId = computed(() => libraryState.currentId)
const currentLibraryName = computed(
  () => libraryState.items.find((item) => item.id === libraryId.value)?.name || '当前图书库',
)
const section = ref<Section>('libraries')
const activeSection = computed(() => SECTIONS.find((item) => item.key === section.value))
const visibleSections = computed(() => SECTIONS.filter((item) => {
  if (item.key === 'dictionaries') return can('dictionaries')
  if (item.key === 'logs') return can('records')
  return true
}))
const canWrite = computed(() => can('settings', 'write'))

function chooseSection(key: Section) {
  section.value = key
  if (key === 'seats') void loadSeats()
  if (key === 'sources') {
    void loadSources()
    void loadResourceLibraries()
    void loadCatalogLimit()
  }
}

/* ---------------- 套录命中条数 ---------------- */
// 一次检索最多列多少条候选。默认 8，可在 1~50 之间调。
// 存在后端 settings 表（key="catalog"），**由服务端在检索时套用**——前端不传 limit，
// 否则候选列表、扩展检索、批量套录各写一个数字，迟早不一致。
const catalogLimit = ref(8)
const catalogLimitBusy = ref(false)
const catalogLimitBounds = reactive({ min: 1, max: 50 })

async function loadCatalogLimit() {
  try {
    const data = await get<{ value: { limit: number }; min: number; max: number }>(
      '/settings/catalog')
    catalogLimit.value = data.value.limit
    if (data.min) catalogLimitBounds.min = data.min
    if (data.max) catalogLimitBounds.max = data.max
  } catch (e) {
    toastError(errorMessage(e))
  }
}

async function saveCatalogLimit() {
  const n = Number(catalogLimit.value)
  if (!Number.isInteger(n) || n < catalogLimitBounds.min || n > catalogLimitBounds.max) {
    toastError(`套录命中条数须为 ${catalogLimitBounds.min}~${catalogLimitBounds.max} 之间的整数`)
    return
  }
  catalogLimitBusy.value = true
  try {
    await put('/settings/catalog', { value: { limit: n } })
    toast('套录命中条数已保存')
  } catch (e) {
    toastError(errorMessage(e))
  } finally {
    catalogLimitBusy.value = false
  }
}

/* ---------------- 图书库 ---------------- */
const libraries = ref<LibraryItem[]>([])
const librariesLoading = ref(true)
const libraryModalOpen = ref(false)
const editingLibrary = ref<LibraryItem | null>(null)
const libraryForm = reactive({ name: '', note: '' })
const libraryBusy = ref(false)
const libraryDeleteTarget = ref<LibraryItem | null>(null)

async function loadLibrariesList() {
  librariesLoading.value = true
  try {
    libraries.value = await get<LibraryItem[]>('/book-libraries')
  } catch (e) {
    toastError(errorMessage(e))
  } finally {
    librariesLoading.value = false
  }
}

function openLibraryModal(row: LibraryItem | null) {
  editingLibrary.value = row
  libraryForm.name = row?.name || ''
  libraryForm.note = row?.note || ''
  libraryModalOpen.value = true
}

async function saveLibrary() {
  if (libraryBusy.value) return
  if (!libraryForm.name.trim()) {
    toastError('图书库名称不能为空')
    return
  }
  libraryBusy.value = true
  try {
    const body = { name: libraryForm.name.trim(), note: libraryForm.note.trim() || null }
    if (editingLibrary.value) {
      await put(`/book-libraries/${editingLibrary.value.id}`, body)
      toast('图书库已保存')
    } else {
      await post('/book-libraries', body)
      toast('图书库已创建')
    }
    libraryModalOpen.value = false
    await loadLibrariesList()
    await loadLibraries(true)
  } catch (e) {
    toastError(errorMessage(e))
  } finally {
    libraryBusy.value = false
  }
}

async function deleteLibrary() {
  const target = libraryDeleteTarget.value
  if (!target) return
  libraryBusy.value = true
  try {
    await del(`/book-libraries/${target.id}`)
    toast('图书库已删除')
    libraryDeleteTarget.value = null
    await loadLibrariesList()
    await loadLibraries(true)
  } catch (e) {
    toastError(errorMessage(e))
  } finally {
    libraryBusy.value = false
  }
}

/* ---------------- 借阅规则 ---------------- */
const rules = ref<Rules | null>(null)
const rulesForm = ref<Rules | null>(null)
const rulesLoading = ref(true)
const rulesBusy = ref(false)
const closedDate = ref('')

async function loadRules() {
  rulesLoading.value = true
  try {
    const data = await get<{ rules: Rules; defaults: Rules }>('/circulation/rules')
    rules.value = data.rules
    rulesForm.value = clone(data.rules)
  } catch (e) {
    toastError(errorMessage(e))
  } finally {
    rulesLoading.value = false
  }
}

function addClosedDate() {
  const day = closedDate.value
  if (!day || !rulesForm.value) return
  if (!rulesForm.value.closed_dates.includes(day)) {
    rulesForm.value.closed_dates = [...rulesForm.value.closed_dates, day].sort()
  }
  closedDate.value = ''
}

function removeClosedDate(day: string) {
  if (!rulesForm.value) return
  rulesForm.value.closed_dates = rulesForm.value.closed_dates.filter((item) => item !== day)
}

async function saveRules() {
  if (!rulesForm.value || rulesBusy.value) return
  rulesBusy.value = true
  try {
    const data = await put<{ rules: Rules; message: string }>('/circulation/rules', rulesForm.value)
    rules.value = data.rules
    rulesForm.value = clone(data.rules)
    toast(data.message)
  } catch (e) {
    toastError(errorMessage(e))
  } finally {
    rulesBusy.value = false
  }
}

/* ---------------- 编目规范 ---------------- */
const barcodeDigits = ref<number[]>([])
const barcodeBusy = ref(false)
const allDigits = Array.from({ length: 13 }, (_, index) => index + 4)
const classNo = reactive({ digits: 2, dash_digits: 2, marks: 'auto', base_digits: 0, enabled: false })
const classBusy = ref(false)
/**
 * 本馆机构代码（801$b）。留空 = 不自动生成 801。
 *
 * 依据 GB/T 33286—2016 7.10：801 在交换书目数据时必备，`$b` 允许机构英文缩写，
 * 也允许中文全称——各家做法不一致（国图 NLC、安徽工程 AHPUL、某馆 bju），
 * 所以做成设置项，由馆里自己定。
 */
const orgCode = ref('')
const orgBusy = ref(false)
async function loadCataloging() {
  try {
    const barcode = await get<{ value: { digits: number[] } }>('/settings/barcode')
    barcodeDigits.value = barcode.value.digits
  } catch (e) {
    toastError(errorMessage(e))
  }
  try {
    const data = await get<{ value: typeof classNo }>('/settings/class_no')
    Object.assign(classNo, data.value)
  } catch (e) {
    toastError(errorMessage(e))
  }
  try {
    const data = await get<{ value: { code: string } }>('/settings/org')
    orgCode.value = data.value.code
  } catch (e) {
    toastError(errorMessage(e))
  }
}

function toggleDigit(digit: number) {
  if (barcodeDigits.value.includes(digit)) {
    if (barcodeDigits.value.length === 1) return
    barcodeDigits.value = barcodeDigits.value.filter((item) => item !== digit)
  } else {
    barcodeDigits.value = [...barcodeDigits.value, digit].sort((a, b) => a - b)
  }
}

async function saveBarcode() {
  barcodeBusy.value = true
  try {
    await put('/settings/barcode', { value: { digits: barcodeDigits.value } })
    toast('条码位数已保存')
  } catch (e) {
    toastError(errorMessage(e))
  } finally {
    barcodeBusy.value = false
  }
}

async function saveOrgCode() {
  orgBusy.value = true
  try {
    await put('/settings/org', { value: { code: orgCode.value.trim() } })
    orgCode.value = orgCode.value.trim()
    toast(orgCode.value ? '本馆机构代码已保存' : '已关闭 801 自动生成')
  } catch (e) {
    toastError(errorMessage(e))
  } finally {
    orgBusy.value = false
  }
}

async function saveClassNo() {
  classBusy.value = true
  try {
    await put('/settings/class_no', { value: { ...classNo } })
    toast('分类号规则已保存')
  } catch (e) {
    toastError(errorMessage(e))
  } finally {
    classBusy.value = false
  }
}

/* ---------------- 自动规范 ---------------- */
const optimizeMode = ref<'auto' | 'manual'>('manual')
const optimizeRules = ref<OptimizeRule[]>([])
const optimizeLoading = ref(true)
const optimizeBusy = ref(false)

async function loadOptimize() {
  optimizeLoading.value = true
  try {
    const data = await get<{ mode: 'auto' | 'manual'; rules: OptimizeRule[] }>('/settings/optimize')
    optimizeMode.value = data.mode
    optimizeRules.value = data.rules
  } catch (e) {
    toastError(errorMessage(e))
  } finally {
    optimizeLoading.value = false
  }
}

async function saveOptimize() {
  optimizeBusy.value = true
  try {
    const rulesPayload: Record<string, { enabled: boolean; value: string | null }> = {}
    for (const rule of optimizeRules.value) {
      rulesPayload[rule.id] = { enabled: rule.enabled, value: rule.value }
    }
    const data = await put<{ rules: OptimizeRule[]; mode: 'auto' | 'manual' }>(
      '/settings/optimize', { mode: optimizeMode.value, rules: rulesPayload },
    )
    optimizeRules.value = data.rules
    optimizeMode.value = data.mode
    toast('规范配置已保存')
  } catch (e) {
    toastError(errorMessage(e))
  } finally {
    optimizeBusy.value = false
  }
}

/* ---------------- 座位区域 ---------------- */
const seatAreas = ref<SeatArea[]>([])
const seatsLoading = ref(false)
const seatModalOpen = ref(false)
const editingSeatArea = ref<SeatArea | null>(null)
const seatBusy = ref(false)
const seatDeleteTarget = ref<SeatArea | null>(null)
const seatForm = reactive({
  name: '', floor: '', description: '', rows: 6, cols: 8,
  open_time: '08:00', close_time: '22:00', enabled: true,
})
const seatSizeLocked = computed(() => Boolean(editingSeatArea.value?.booking_count))

async function loadSeats() {
  if (!libraryId.value) return
  seatsLoading.value = true
  try {
    const data = await get<{ items: SeatArea[] }>('/settings/seats/areas', {
      library_id: libraryId.value,
    })
    seatAreas.value = data.items
  } catch (e) {
    toastError(errorMessage(e))
  } finally {
    seatsLoading.value = false
  }
}

function openSeatModal(area: SeatArea | null) {
  editingSeatArea.value = area
  seatForm.name = area?.name || ''
  seatForm.floor = area?.floor || ''
  seatForm.description = area?.description || ''
  seatForm.rows = area?.rows || 6
  seatForm.cols = area?.cols || 8
  seatForm.open_time = area?.open_time || '08:00'
  seatForm.close_time = area?.close_time || '22:00'
  seatForm.enabled = area?.enabled ?? true
  seatModalOpen.value = true
}

async function saveSeatArea() {
  if (!libraryId.value) {
    toastError('请先选择图书库')
    return
  }
  if (!seatForm.name.trim()) {
    toastError('区域名称不能为空')
    return
  }
  seatBusy.value = true
  try {
    const body = {
      name: seatForm.name.trim(),
      floor: seatForm.floor.trim() || null,
      description: seatForm.description.trim() || null,
      rows: Number(seatForm.rows) || 1,
      cols: Number(seatForm.cols) || 1,
      open_time: seatForm.open_time,
      close_time: seatForm.close_time,
      enabled: seatForm.enabled,
    }
    if (editingSeatArea.value) {
      const data = await put<{ area: SeatArea; message: string }>(
        `/settings/seats/areas/${editingSeatArea.value.id}`, body,
      )
      toast(data.message)
    } else {
      const data = await post<{ area: SeatArea; message: string }>(
        '/settings/seats/areas', { ...body, library_id: libraryId.value },
      )
      toast(data.message)
    }
    seatModalOpen.value = false
    await loadSeats()
  } catch (e) {
    toastError(errorMessage(e))
  } finally {
    seatBusy.value = false
  }
}

async function toggleSeatArea(area: SeatArea) {
  seatBusy.value = true
  try {
    await put(`/settings/seats/areas/${area.id}`, {
      name: area.name, floor: area.floor, description: area.description,
      rows: area.rows, cols: area.cols,
      open_time: area.open_time, close_time: area.close_time,
      enabled: !area.enabled,
    })
    toast(area.enabled ? '座位区域已停用' : '座位区域已启用')
    await loadSeats()
  } catch (e) {
    toastError(errorMessage(e))
  } finally {
    seatBusy.value = false
  }
}

async function deleteSeatArea() {
  const target = seatDeleteTarget.value
  if (!target) return
  seatBusy.value = true
  try {
    const data = await del<{ message: string }>(`/settings/seats/areas/${target.id}`)
    toast(data.message)
    seatDeleteTarget.value = null
    await loadSeats()
  } catch (e) {
    toastError(errorMessage(e))
  } finally {
    seatBusy.value = false
  }
}

/* ---------------- 资源库管理 ---------------- */
const resourceLibraries = ref<ResourceLibraryItem[]>([])
const resourceLibrariesLoading = ref(true)
const resourceLibBusy = ref<number | null>(null)

async function loadResourceLibraries() {
  resourceLibrariesLoading.value = true
  try {
    resourceLibraries.value = await get<ResourceLibraryItem[]>('/sources/libraries')
  } catch (e) {
    toastError(errorMessage(e))
  } finally {
    resourceLibrariesLoading.value = false
  }
}

async function toggleResourceLibrary(row: ResourceLibraryItem) {
  if (resourceLibBusy.value !== null) return
  resourceLibBusy.value = row.id
  try {
    const data = await put<{ id: number; enabled: boolean }>(
      `/sources/libraries/${row.id}`, { enabled: !row.enabled })
    row.enabled = data.enabled
    toast(data.enabled ? '资源库已启用' : '资源库已停用')
  } catch (e) {
    toastError(errorMessage(e))
  } finally {
    resourceLibBusy.value = null
  }
}

/* ---------------- 套录源 ---------------- */
const sources = ref<Source[]>([])
const sourcesLoading = ref(true)
const sourceModalOpen = ref(false)
const editingSource = ref<Source | null>(null)
const sourceBusy = ref(false)
const sourceDeleteTarget = ref<Source | null>(null)
const probeBusy = ref<number | null>(null)
const pgProbeBusy = ref(false)
const pgProbeError = ref('')
const pgProbeResult = ref<{ server: string; tables: Array<{ schema: string; table: string }> } | null>(null)
const sourceForm = reactive<SourceForm>({
  type: 'z3950', name: '', host: '', port: 2100, database: '', user: '',
  password: '', charset: 'gbk', priority: 100, enabled: true,
})

/**
 * 优先级只给三档。原来是一个 1~999 的数字框——数字大小对编目员没有意义，
 * 「高/中/低」才是他们实际要做的决策。
 */
/**
 * 字符集：**检索词按它编码后发给目标源**。
 *
 * 这不是"显示用"的元数据——`z3950.py` 用它把题名/责任者编成目标能识别的字节。
 * 国内源几乎都是 GBK；国图、CALIS 之类也有 GBK 的；少数国外源是 UTF-8。
 * 选错的后果是**静默 0 命中**（服务端不报错，就是查不到），所以必须可设。
 */
const SOURCE_CHARSETS = [
  { key: 'gbk', label: 'GBK（国内源，默认）' },
  { key: 'utf-8', label: 'UTF-8' },
  { key: 'big5', label: 'Big5（繁体）' },
]

const SOURCE_PRIORITY_OPTIONS = [
  { value: 10, label: '高（先检索）' },
  { value: 20, label: '中' },
  { value: 30, label: '低（后检索）' },
]

/**
 * 把库里存的任意数字归到最近的一档。
 *
 * 存量数据里有 1 / 100 / 999 这类手填值（旧界面的默认是 100 = 兜底），
 * 不归一的话下拉框会显示空白，看起来像"没设置"。
 */
async function loadSources() {
  sourcesLoading.value = true
  try {
    sources.value = await get<Source[]>('/sources')
  } catch (e) {
    toastError(errorMessage(e))
  } finally {
    sourcesLoading.value = false
  }
}

function openSourceModal(row: Source | null) {
  editingSource.value = row
  const cfg = (row?.config || {}) as Record<string, unknown>
  sourceForm.type = row?.type || 'z3950'
  sourceForm.name = row?.name || ''
  sourceForm.host = String(cfg.host || '')
  sourceForm.port = Number(cfg.port || 2100)
  sourceForm.database = String(cfg.database || '')
  sourceForm.user = String(cfg.user || '')
  sourceForm.password = String(cfg.password || '')
  sourceForm.charset = String(cfg.charset || 'gbk')
  sourceForm.priority = priorityLevel(cfg.priority ?? 100)
  sourceForm.enabled = row?.enabled ?? true
  pgProbeResult.value = null
  pgProbeError.value = ''
  srcTest.status = ''
  srcTest.message = ''
  sourceModalOpen.value = true
}

async function probePgSource() {
  if (pgProbeBusy.value) return
  pgProbeBusy.value = true
  pgProbeError.value = ''
  try {
    const data = await post<{ ok: boolean; server: string; tables: Array<{ schema: string; table: string }> }>(
      '/sources/pg/inspect',
      {
        host: sourceForm.host.trim(),
        port: Number(sourceForm.port) || 5432,
        database: sourceForm.database.trim(),
        user: sourceForm.user.trim(),
        password: sourceForm.password,
        source_id: editingSource.value?.id || null,
      },
    )
    pgProbeResult.value = { server: data.server, tables: data.tables }
    toast(`连接成功，共发现 ${data.tables.length} 张表`)
  } catch (e) {
    pgProbeResult.value = null
    pgProbeError.value = errorMessage(e)
  } finally {
    pgProbeBusy.value = false
  }
}

function sourcePayload(): Record<string, unknown> {
  return {
    type: sourceForm.type,
    name: sourceForm.name.trim(),
    host: sourceForm.host.trim(),
    port: Number(sourceForm.port) || 2100,
    database: sourceForm.database.trim(),
    user: sourceForm.user.trim(),
    password: sourceForm.password,
    charset: sourceForm.charset,
    priority: Number(sourceForm.priority) || 100,
    enabled: sourceForm.enabled,
    extra: (editingSource.value?.config || {}).extra || {},
  }
}

/**
 * 套录源表单的必填校验。
 *
 * 原先只校验「名称」：主机、库名留空也能保存成功，错误要等到探活或第一次套录
 * 才暴露 —— 而这时候编目员已经在检索界面等结果了，根本想不到是配置的问题。
 * 校验放在提交前，一次把话说清楚。
 */
function sourceProblem(): string | null {
  if (!sourceForm.name.trim()) return '请填写套录源名称'
  if (sourceForm.type === 'local') return null
  if (!sourceForm.host.trim()) return '请填写主机地址（host 或 IP）'
  const port = Number(sourceForm.port)
  if (!Number.isInteger(port) || port < 1 || port > 65535) return '端口应为 1–65535 的整数'
  if (!sourceForm.database.trim()) {
    return sourceForm.type === 'z3950'
      ? '请填写数据库名（Z39.50 目标库，由对方馆提供）'
      : '请填写数据库名'
  }
  return null
}

// 弹窗内的连接测试状态
const srcTest = reactive({ busy: false, status: '' as '' | 'ok' | 'fail', message: '' })

/**
 * 弹窗内测试连接。
 *
 * 后端的探活接口是按 **source_id** 的（要读落库配置，并回写 last_ok_at/last_fail_at），
 * 所以新建时只能"先保存再测"——按钮文案如实写成「保存并测试」，不假装能只测不存。
 * 保存后立刻把 editingSource 切过去，否则用户再点一次「保存」会又建一条（后端 409）。
 */
async function testSourceInModal() {
  if (srcTest.busy || sourceBusy.value) return
  const problem = sourceProblem()
  if (problem) {
    toastError(problem)
    return
  }
  srcTest.busy = true
  srcTest.status = ''
  srcTest.message = ''
  let createdNow = false
  try {
    let id = editingSource.value?.id
    if (!id) {
      const created = await post<{ id: number; name: string }>('/sources', sourcePayload())
      id = created.id
      createdNow = true
      await loadSources()
      const saved = sources.value.find((item) => item.id === id) || null
      if (!saved) throw new Error('保存后未能读回该套录源，请重新打开弹窗')
      editingSource.value = saved      // 已落库：此后的保存是 PUT，不会建第二条
      toast('已保存，正在测试连接…', 'info')
    }
    const data = await post<Record<string, unknown>>(`/sources/${id}/test`)
    const ok = String(data.status) === 'ok'
    const detail = String(data.message || data.records || '')
    srcTest.status = ok ? 'ok' : 'fail'
    srcTest.message = ok
      ? `连接正常${detail ? `：${detail}` : ''}`
      : `连接失败${detail ? `：${detail}` : ''}`
    if (!ok && createdNow) {
      // 刚建就测不通的源**别留着启用**：套录是逐个源串行检索的，一条连不上的源
      // 会让每次套录都白等一个超时（默认 30s），编目员只会觉得"系统卡"。
      await put(`/sources/${id}`, { ...sourcePayload(), enabled: false })
      sourceForm.enabled = false
      await loadSources()
      srcTest.message += '（已自动停用，修好后在列表里重新启用）'
    }
    if (ok) toast('套录源连接正常')
    else toastError(srcTest.message)
    await loadSources()
  } catch (e) {
    srcTest.status = 'fail'
    srcTest.message = errorMessage(e)
    toastError(srcTest.message)
  } finally {
    srcTest.busy = false
  }
}

async function saveSource() {
  const problem = sourceProblem()
  if (problem) {
    toastError(problem)
    return
  }
  sourceBusy.value = true
  try {
    if (editingSource.value) {
      await put(`/sources/${editingSource.value.id}`, sourcePayload())
      toast('套录源已保存')
    } else {
      await post('/sources', sourcePayload())
      toast('套录源已创建')
    }
    sourceModalOpen.value = false
    await loadSources()
  } catch (e) {
    toastError(errorMessage(e))
  } finally {
    sourceBusy.value = false
  }
}

async function toggleSource(row: Source) {
  const cfg = row.config || {}
  sourceBusy.value = true
  try {
    await put(`/sources/${row.id}`, {
      type: row.type, name: row.name,
      host: cfg.host || '', port: cfg.port || 2100,
      database: cfg.database || '', user: cfg.user || '',
      password: cfg.password || '',
      charset: cfg.charset || 'gbk',
      record_syntax: cfg.record_syntax || 'unimarc',
      priority: cfg.priority ?? 100,
      enabled: !row.enabled,
      extra: cfg.extra || {},
    })
    toast(row.enabled ? '套录源已停用' : '套录源已启用')
    await loadSources()
  } catch (e) {
    toastError(errorMessage(e))
  } finally {
    sourceBusy.value = false
  }
}

async function probeSource(row: Source) {
  if (probeBusy.value) return
  probeBusy.value = row.id
  try {
    const data = await post<Record<string, unknown>>(`/sources/${row.id}/test`)
    const status = String(data.status || '')
    const message = String(data.message || data.records || '')
    if (status === 'ok') toast(`${row.name} 连接正常${message ? `：${message}` : ''}`)
    else toastError(`${row.name} 连接失败${message ? `：${message}` : ''}`)
    await loadSources()
  } catch (e) {
    toastError(errorMessage(e))
  } finally {
    probeBusy.value = null
  }
}

async function deleteSource() {
  const target = sourceDeleteTarget.value
  if (!target) return
  sourceBusy.value = true
  try {
    await del(`/sources/${target.id}`)
    toast('套录源已删除')
    sourceDeleteTarget.value = null
    await loadSources()
  } catch (e) {
    toastError(errorMessage(e))
  } finally {
    sourceBusy.value = false
  }
}

watch(libraryId, () => {
  if (section.value === 'seats') void loadSeats()
})

onMounted(() => {
  void loadLibrariesList()
  void loadRules()
  void loadCataloging()
  void loadOptimize()
  void loadSeats()
  void loadSources()
  void loadResourceLibraries()
})
</script>

<template>
  <section class="view settings-view">
    <div class="settings-layout">
      <aside class="settings-nav card">
        <div class="settings-nav-head">
          <span class="settings-nav-eyebrow">SYSTEM SETTINGS</span>
          <strong>全局设置</strong>
          <p>修改后立即作用于全馆业务。</p>
        </div>
        <button
          v-for="item in visibleSections"
          :key="item.key"
          class="settings-nav-item"
          :class="{ 'is-active': section === item.key }"
          type="button"
          @click="chooseSection(item.key)"
        >
          <span class="settings-nav-icon"><AppIcon :name="item.icon" :size="17" /></span>
          <span><strong>{{ item.label }}</strong><small>{{ item.hint }}</small></span>
          <AppIcon name="chevron-right" :size="14" class="settings-nav-arrow" />
        </button>
        <div class="settings-nav-foot">
          <AppIcon name="info" :size="15" />
          <span>图书库相关的座位配置跟随顶部当前图书库切换。</span>
        </div>
      </aside>

      <main class="settings-content">
        <header class="settings-head">
          <div>
            <span class="settings-head-kicker">{{ activeSection?.hint }}</span>
            <h2>{{ activeSection?.label }}</h2>
          </div>
          <span class="badge badge-neutral no-dot">{{ currentLibraryName }}</span>
        </header>
        <div v-if="!canWrite" class="notice-strip">
          <AppIcon name="info" />
          <span><strong>只读权限</strong>：当前角色可以查看设置，但不能修改。</span>
        </div>

        <!-- 图书库 -->
        <template v-if="section === 'libraries'">
          <article class="card">
            <div class="card-head">
              <div>
                <div class="card-title">图书库管理</div>
                <div class="card-sub">每校或每馆一个库。书目、条码与书次号按库独立。</div>
              </div>
              <button v-if="canWrite" class="btn btn-primary btn-sm" type="button" @click="openLibraryModal(null)">
                <AppIcon name="plus" />新增图书库
              </button>
            </div>
            <div v-if="librariesLoading" class="settings-loading"><span class="spinner" />正在读取图书库…</div>
            <div v-else class="table-wrap">
              <table class="table">
                <thead><tr><th>图书库</th><th>备注</th><th class="num">书目录数</th><th>操作</th></tr></thead>
                <tbody>
                  <tr v-for="(row, index) in libraries" :key="row.id">
                    <td>
                      <div class="settings-lib-cell">
                        <span class="settings-lib-mark"><AppIcon name="building" :size="16" /></span>
                        <div><strong>{{ row.name }}</strong><div class="cell-sub" v-if="index === 0">默认图书库</div></div>
                      </div>
                    </td>
                    <td class="cell-sub">{{ row.note || '—' }}</td>
                    <td class="num">{{ formatNumber(row.record_count) }}</td>
                    <td>
                      <div v-if="canWrite" class="row-actions">
                        <button class="text-link" type="button" @click="openLibraryModal(row)">编辑</button>
                        <button class="text-link danger" type="button" :disabled="libraries.length <= 1" @click="libraryDeleteTarget = row">删除</button>
                      </div>
                      <span v-else class="cell-sub">只读</span>
                    </td>
                  </tr>
                  <tr v-if="!libraries.length"><td colspan="4" class="table-state">当前没有图书库</td></tr>
                </tbody>
              </table>
            </div>
          </article>
        </template>

        <!-- 借阅规则 -->
        <template v-else-if="section === 'circulation'">
          <div v-if="rulesLoading && !rulesForm" class="card settings-loading"><span class="spinner" />正在读取借阅规则…</div>
          <template v-else-if="rulesForm">
            <article class="card">
              <div class="card-head">
                <div>
                  <div class="card-title">读者类型与借阅额度</div>
                  <div class="card-sub">借期与续借按读者类型生效。续借次数为 0 表示不允许续借。</div>
                </div>
                <button v-if="canWrite" class="btn btn-primary btn-sm" type="button" :disabled="rulesBusy" @click="saveRules">
                  {{ rulesBusy ? '保存中…' : '保存规则' }}
                </button>
              </div>
              <div class="card-body">
                <div class="settings-type-grid">
                  <div v-for="(rule, key) in rulesForm.types" :key="key" class="settings-type-card">
                    <header>
                      <strong>{{ rule.label }}</strong>
                    </header>
                    <div class="form-grid">
                      <div class="field">
                        <label>借期（天）</label>
                        <input v-model.number="rule.loan_days" class="input" type="number" min="1" max="365" :aria-label="`${rule.label}借期（天）`" />
                      </div>
                      <div class="field">
                        <label>借阅上限（册）</label>
                        <input v-model.number="rule.max_items" class="input" type="number" min="1" max="100" :aria-label="`${rule.label}借阅上限（册）`" />
                      </div>
                      <div class="field">
                        <label>续借次数</label>
                        <input v-model.number="rule.max_renew" class="input" type="number" min="0" max="10" :aria-label="`${rule.label}续借次数`" />
                      </div>
                      <div class="field">
                        <label>每次续借（天）</label>
                        <input v-model.number="rule.renew_days" class="input" type="number" min="1" max="365" :aria-label="`${rule.label}每次续借（天）`" />
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </article>

            <article class="card">
              <div class="card-head">
                <div>
                  <div class="card-title">日期与预约</div>
                  <div class="card-sub">设置应还日期的顺延规则与预约到书保留天数</div>
                </div>
              </div>
              <div class="card-body settings-switch-grid">
                <label class="switch-line">
                  <span>周末不计入应还日期</span>
                  <input v-model="rulesForm.skip_weekends" type="checkbox" />
                </label>
                <label class="switch-line">
                  <span>有逾期时禁止继续借阅</span>
                  <input v-model="rulesForm.block_on_overdue" type="checkbox" />
                </label>
                <label class="switch-line">
                  <span>有未缴罚款或赔偿时禁止继续借阅</span>
                  <input v-model="rulesForm.fines.block_on_unpaid" type="checkbox" />
                </label>
                <div class="field settings-hold-field">
                  <label>预约到书保留（天）</label>
                  <input v-model.number="rulesForm.reservation_hold_days" class="input" type="number" min="1" max="30" />
                </div>
                <div class="field span-2">
                  <label>闭馆日</label>
                  <div class="settings-date-add">
                    <input v-model="closedDate" class="input" type="date" />
                    <button class="btn btn-outline btn-sm" type="button" @click="addClosedDate">添加</button>
                  </div>
                  <div v-if="rulesForm.closed_dates.length" class="settings-tags">
                    <span v-for="day in rulesForm.closed_dates" :key="day" class="settings-tag">
                      {{ day }}
                      <button type="button" :aria-label="`移除 ${day}`" @click="removeClosedDate(day)"><AppIcon name="x" :size="12" /></button>
                    </span>
                  </div>
                  <span v-else class="field-note">未设置闭馆日，添加后应还日期自动避开</span>
                </div>
              </div>
            </article>

            <article class="card">
              <div class="card-head">
                <div>
                  <div class="card-title">逾期罚款</div>
                  <div class="card-sub">关闭时归还不计罚，界面不显示罚款金额。历史金额保留。</div>
                </div>
                <label class="settings-inline-switch">
                  <input v-model="rulesForm.fines.enabled" type="checkbox" />
                  {{ rulesForm.fines.enabled ? '已启用' : '未启用' }}
                </label>
              </div>
              <div class="card-body">
                <div class="form-grid settings-fine-grid" :class="{ 'is-disabled': !rulesForm.fines.enabled }">
                  <div class="field">
                    <label>每日罚款（元）</label>
                    <input v-model.number="rulesForm.fines.per_day" class="input" type="number" min="0" step="0.01" :disabled="!rulesForm.fines.enabled" />
                  </div>
                  <div class="field">
                    <label>宽限天数</label>
                    <input v-model.number="rulesForm.fines.grace_days" class="input" type="number" min="0" max="365" :disabled="!rulesForm.fines.enabled" />
                  </div>
                  <div class="field">
                    <label>单册封顶（元）</label>
                    <input v-model.number="rulesForm.fines.max_amount" class="input" type="number" min="0" step="0.01" :disabled="!rulesForm.fines.enabled" />
                    <span class="field-note">0 表示不封顶</span>
                  </div>
                </div>
              </div>
            </article>
          </template>
        </template>

        <!-- 编目规范 -->
        <template v-else-if="section === 'cataloging'">
          <article class="card">
            <div class="card-head">
              <div>
                <div class="card-title">本馆机构代码</div>
                <div class="card-sub">
                  新建或保存记录时，若记录没有 801 记录来源，自动补一条
                  <code>801 #0 $aCN $b本馆代码 $c建档日期</code>。留空则不生成。
                </div>
              </div>
              <button v-if="canWrite" class="btn btn-primary btn-sm" type="button"
                :disabled="orgBusy" @click="saveOrgCode">
                {{ orgBusy ? '保存中…' : '保存' }}
              </button>
            </div>
            <div class="card-body">
              <div class="field">
                <label for="org-code">机构代码（801$b）</label>
                <input id="org-code" v-model="orgCode" class="input" maxlength="32"
                  placeholder="如 NLC / AHPUL，留空则不自动生成" />
                <span class="hint">
                  标准允许使用英文缩写或中文全称。已有的 801 不会被覆盖，
                  它标注的是原编目机构。
                </span>
              </div>
            </div>
          </article>

          <article class="card">
            <div class="card-head">
              <div>
                <div class="card-title">条码位数</div>
                <div class="card-sub">允许并存的条码位数。自动流水按其中最宽的位数生成。</div>
              </div>
              <button v-if="canWrite" class="btn btn-primary btn-sm" type="button" :disabled="barcodeBusy" @click="saveBarcode">
                {{ barcodeBusy ? '保存中…' : '保存' }}
              </button>
            </div>
            <div class="card-body">
              <div class="settings-digit-row">
                <button
                  v-for="digit in allDigits"
                  :key="digit"
                  class="settings-digit"
                  :class="{ 'is-active': barcodeDigits.includes(digit) }"
                  type="button"
                  @click="toggleDigit(digit)"
                >{{ digit }}</button>
              </div>
              <p class="field-note" style="margin-top: 10px">当前允许 {{ barcodeDigits.join(' / ') }} 位，至少保留一个。</p>
            </div>
          </article>

          <article class="card">
            <div class="card-head">
              <div>
                <div class="card-title">分类号截取</div>
                <div class="card-sub">套录与导入时按以下规则截短分类号，影响索书号与排架顺序。</div>
              </div>
              <button v-if="canWrite" class="btn btn-primary btn-sm" type="button" :disabled="classBusy" @click="saveClassNo">
                {{ classBusy ? '保存中…' : '保存' }}
              </button>
            </div>
            <div class="card-body">
              <label class="settings-rule-toggle" style="margin-bottom: 14px">
                <input v-model="classNo.enabled" type="checkbox" :disabled="!canWrite" />
                <span><strong>启用分类号截取</strong><small>关闭时套录与导入保留原分类号</small></span>
              </label>
              <div class="form-grid settings-four-col">
                <div class="field">
                  <label>小数点后保留位数</label>
                  <input v-model.number="classNo.digits" class="input" type="number" min="0" max="6" :disabled="!classNo.enabled || !canWrite" />
                </div>
                <div class="field">
                  <label>总论复分号位数</label>
                  <input v-model.number="classNo.dash_digits" class="input" type="number" min="0" max="6" :disabled="!classNo.enabled || !canWrite" />
                </div>
                <div class="field">
                  <label>字母后数字阈值</label>
                  <input v-model.number="classNo.base_digits" class="input" type="number" min="0" max="6" :disabled="!classNo.enabled || !canWrite" />
                  <span class="field-note">0 表示关闭此规则</span>
                </div>
                <div class="field">
                  <label>复分号口径</label>
                  <select v-model="classNo.marks" class="select" :disabled="!classNo.enabled || !canWrite">
                    <option value="auto">自动判定</option>
                    <option value="keep">一律保留</option>
                    <option value="drop">一律去掉</option>
                  </select>
                </div>
              </div>
            </div>
          </article>

          <article class="card">
            <div class="card-head">
              <div>
                <div class="card-title">自动规范</div>
                <div class="card-sub">按既定规则自动修正著录。逐条启停，保存后立即生效。</div>
              </div>
              <div class="settings-head-actions">
                <div class="settings-mode">
                  <button type="button" :disabled="!canWrite" :class="{ 'is-active': optimizeMode === 'auto' }" @click="optimizeMode = 'auto'">进入编辑自动执行</button>
                  <button type="button" :disabled="!canWrite" :class="{ 'is-active': optimizeMode === 'manual' }" @click="optimizeMode = 'manual'">仅手动执行</button>
                </div>
                <button v-if="canWrite" class="btn btn-primary btn-sm" type="button" :disabled="optimizeBusy" @click="saveOptimize">
                  {{ optimizeBusy ? '保存中…' : '保存配置' }}
                </button>
              </div>
            </div>
            <div v-if="optimizeLoading" class="settings-loading"><span class="spinner" />正在读取规范规则…</div>
            <div v-else class="card-body">
              <div class="settings-rule-grid">
                <div v-for="rule in optimizeRules" :key="rule.id" class="settings-rule" :class="{ 'is-on': rule.enabled }">
                  <label class="settings-rule-toggle">
                    <input v-model="rule.enabled" type="checkbox" :disabled="!canWrite" />
                    <span><strong>{{ rule.name }}</strong><small>{{ rule.desc }}</small></span>
                  </label>
                  <div v-if="rule.param_label" class="settings-rule-param">
                    <span>{{ rule.param_label }}</span>
                    <select v-if="rule.param_options.length" v-model="rule.value" class="select" :disabled="!rule.enabled || !canWrite">
                      <option v-for="option in rule.param_options" :key="option" :value="option">{{ option }}</option>
                    </select>
                    <input v-else v-model="rule.value" class="input" :disabled="!rule.enabled || !canWrite" />
                  </div>
                </div>
              </div>
            </div>
          </article>
        </template>

        <!-- 座位区域 -->
        <template v-else-if="section === 'seats'">
          <article class="card">
            <div class="card-head">
              <div>
                <div class="card-title">{{ currentLibraryName }} · 座位区域</div>
                <div class="card-sub">读者门户按开放时间生成 2 小时时段。已有预约的区域不可删除或调整行列。</div>
              </div>
              <button v-if="canWrite" class="btn btn-primary btn-sm" type="button" @click="openSeatModal(null)">
                <AppIcon name="plus" />新增区域
              </button>
            </div>
            <div v-if="seatsLoading" class="settings-loading"><span class="spinner" />正在读取座位区域…</div>
            <div v-else-if="!seatAreas.length" class="empty-card" style="margin: 18px">当前图书库没有座位区域</div>
            <div v-else class="settings-area-list">
              <div v-for="area in seatAreas" :key="area.id" class="settings-area-card" :class="{ 'is-off': !area.enabled }">
                <span class="settings-area-mark"><AppIcon name="seat" :size="19" /></span>
                <div class="settings-area-main">
                  <div class="settings-area-title">
                    <strong>{{ area.name }}</strong>
                    <span class="badge no-dot" :class="area.enabled ? 'badge-ok' : 'badge-neutral'">{{ area.enabled ? '开放' : '停用' }}</span>
                  </div>
                  <p>{{ area.floor || '未标注楼层' }} · 开放 {{ area.open_time }}–{{ area.close_time }} · {{ area.rows }} × {{ area.cols }} 布局</p>
                  <div class="settings-area-stats">
                    <span><strong>{{ area.seat_count }}</strong> 个座位</span>
                    <span><strong>{{ area.active_booking_count }}</strong> 个进行中预约</span>
                    <span v-if="area.booking_count" class="muted">历史预约 {{ area.booking_count }}</span>
                  </div>
                </div>
                <div v-if="canWrite" class="row-actions">
                  <button class="text-link" type="button" :disabled="seatBusy" @click="toggleSeatArea(area)">{{ area.enabled ? '停用' : '启用' }}</button>
                  <button class="text-link" type="button" @click="openSeatModal(area)">编辑</button>
                  <button v-if="!area.booking_count" class="text-link danger" type="button" @click="seatDeleteTarget = area">删除</button>
                </div>
                <span v-else class="cell-sub">只读</span>
              </div>
            </div>
          </article>
        </template>

        <template v-else-if="section === 'dictionaries'">
          <Dictionaries />
        </template>
        <template v-else-if="section === 'marc'">
          <MarcBrowser />
        </template>

        <template v-else-if="section === 'logs'">
          <Logs />
        </template>

        <!-- 资源库管理 -->
        <template v-else>
          <article class="card">
            <div class="card-head">
              <div>
                <div class="card-title">套录命中条数</div>
                <div class="card-sub">一次检索最多列出多少条候选。数值越小响应越快，越大越不易遗漏。</div>
              </div>
              <button v-if="canWrite" class="btn btn-primary btn-sm" type="button"
                :disabled="catalogLimitBusy" @click="saveCatalogLimit">
                {{ catalogLimitBusy ? '保存中…' : '保存' }}
              </button>
            </div>
            <div class="card-body">
              <div class="field" style="max-width: 220px">
                <input id="catalog-limit" v-model.number="catalogLimit" class="input"
                  type="number" :min="catalogLimitBounds.min" :max="catalogLimitBounds.max" />
                <p class="field-note">
                  默认 8，可设 {{ catalogLimitBounds.min }}~{{ catalogLimitBounds.max }} 条；
                  对图书库、资源库与 Z39.50 同时生效。
                </p>
              </div>
            </div>
          </article>

          <article class="card">
            <div class="card-head">
              <div>
                <div class="card-title">本地资源库</div>
                <div class="card-sub">停用后不参与编目与批量套录。记录保留，可随时启用。</div>
              </div>
              <RouterLink class="btn btn-outline btn-sm" to="/admin/resources">
                <AppIcon name="database" :size="15" />浏览资源库
              </RouterLink>
            </div>
            <div v-if="resourceLibrariesLoading" class="settings-loading"><span class="spinner" />正在读取资源库…</div>
            <div v-else-if="!resourceLibraries.length" class="empty-card" style="margin: 18px">当前没有资源库</div>
            <div v-else class="settings-source-list">
              <div v-for="lib in resourceLibraries" :key="lib.id" class="settings-source-card" :class="{ 'is-off': !lib.enabled }">
                <span class="settings-source-mark"><AppIcon name="archive" :size="18" /></span>
                <div class="settings-source-main">
                  <div class="settings-source-title">
                    <strong>{{ lib.name }}</strong>
                    <span class="badge badge-neutral no-dot">{{ formatNumber(lib.record_count) }} 条</span>
                    <span v-if="!lib.enabled" class="badge badge-neutral no-dot">已停用</span>
                  </div>
                  <p>{{ lib.origin || '本地资源库' }}</p>
                </div>
                <div v-if="canWrite" class="row-actions">
                  <button class="text-link" type="button" :disabled="resourceLibBusy === lib.id" @click="toggleResourceLibrary(lib)">
                    {{ resourceLibBusy === lib.id ? '处理中…' : (lib.enabled ? '停用' : '启用') }}
                  </button>
                </div>
              </div>
            </div>
            <div class="settings-source-foot">
              <AppIcon name="info" :size="15" />
              资源库记录的浏览、导入与删除在
              <RouterLink class="text-link" to="/admin/resources">资源库</RouterLink> 中完成。
            </div>
          </article>

          <article class="card">
            <div class="card-head">
              <div>
                <div class="card-title">外部套录源</div>
                <div class="card-sub">管理 Z39.50 与外部数据库套录源。系统记录每次探测的成功与失败时间。</div>
              </div>
              <button v-if="canWrite" class="btn btn-primary btn-sm" type="button" @click="openSourceModal(null)">
                <AppIcon name="plus" />新增套录源
              </button>
            </div>
            <div v-if="sourcesLoading" class="settings-loading"><span class="spinner" />正在读取套录源…</div>
            <div v-else-if="!sources.length" class="empty-card" style="margin: 18px">当前没有套录源</div>
            <div v-else class="settings-source-list">
              <div v-for="row in sources" :key="row.id" class="settings-source-card" :class="{ 'is-off': !row.enabled }">
                <span class="settings-source-mark"><AppIcon :name="row.type === 'z3950' ? 'compass' : row.type === 'pg' ? 'database' : 'archive'" :size="18" /></span>
                <div class="settings-source-main">
                  <div class="settings-source-title">
                    <strong>{{ row.name }}</strong>
                    <span class="badge badge-neutral no-dot">{{ sourceTypeLabel(row.type) }}</span>
                    <span v-if="!row.enabled" class="badge badge-neutral no-dot">已停用</span>
                  </div>
                  <p>{{ sourceEndpoint(row) }}</p>
                  <div class="settings-source-dates">
                    <span>成功 {{ formatDateTime(row.last_ok_at) || '—' }}</span>
                    <span>失败 {{ formatDateTime(row.last_fail_at) || '—' }}</span>
                  </div>
                </div>
                <div v-if="canWrite" class="row-actions">
                  <button class="text-link" type="button" :disabled="probeBusy === row.id" @click="probeSource(row)">{{ probeBusy === row.id ? '探活中…' : '探活' }}</button>
                  <button class="text-link" type="button" :disabled="sourceBusy" @click="toggleSource(row)">{{ row.enabled ? '停用' : '启用' }}</button>
                  <button class="text-link" type="button" @click="openSourceModal(row)">编辑</button>
                  <button class="text-link danger" type="button" @click="sourceDeleteTarget = row">删除</button>
                </div>
              </div>
            </div>
            <div class="settings-source-foot">
              <AppIcon name="info" :size="15" />
              外部套录源用于编目检索兜底。停用后不参与套录。
            </div>
          </article>
        </template>
      </main>
    </div>

    <BaseModal :busy="libraryBusy" :open="libraryModalOpen" :title="editingLibrary ? '编辑图书库' : '新增图书库'" size="sm" @close="libraryModalOpen = false">
      <div class="auth-form">
        <div class="field">
          <label for="lib-name">名称</label>
          <input id="lib-name" v-model="libraryForm.name" class="input" maxlength="64" placeholder="如 简籍示范中学" />
        </div>
        <div class="field">
          <label for="lib-note">备注</label>
          <input id="lib-note" v-model="libraryForm.note" class="input" maxlength="256" placeholder="可选" />
        </div>
      </div>
      <template #footer>
        <button class="btn btn-ghost" type="button" :disabled="libraryBusy" @click="libraryModalOpen = false">取消</button>
        <button class="btn btn-primary" type="button" :disabled="libraryBusy" @click="saveLibrary">{{ libraryBusy ? '保存中…' : '保存' }}</button>
      </template>
    </BaseModal>

    <BaseModal :busy="seatBusy" :open="seatModalOpen" :title="editingSeatArea ? '编辑座位区域' : '新增座位区域'" :subtitle="currentLibraryName" size="md" @close="seatModalOpen = false">
      <div class="form-grid">
        <div class="field span-2">
          <label for="seat-name">区域名称</label>
          <input id="seat-name" v-model="seatForm.name" class="input" maxlength="64" placeholder="如 2F 静默自习区" />
        </div>
        <div class="field">
          <label for="seat-floor">楼层</label>
          <input id="seat-floor" v-model="seatForm.floor" class="input" maxlength="32" placeholder="如 2F" />
        </div>
        <div class="field">
          <label for="seat-desc">说明</label>
          <input id="seat-desc" v-model="seatForm.description" class="input" maxlength="256" placeholder="可选" />
        </div>
        <div class="field">
          <label for="seat-rows">行数</label>
          <input id="seat-rows" v-model.number="seatForm.rows" class="input" type="number" min="1" max="20" :disabled="seatSizeLocked" />
        </div>
        <div class="field">
          <label for="seat-cols">列数</label>
          <input id="seat-cols" v-model.number="seatForm.cols" class="input" type="number" min="1" max="20" :disabled="seatSizeLocked" />
        </div>
        <div class="field">
          <label for="seat-open">开放时间</label>
          <input id="seat-open" v-model="seatForm.open_time" class="input" type="time" />
        </div>
        <div class="field">
          <label for="seat-close">闭馆时间</label>
          <input id="seat-close" v-model="seatForm.close_time" class="input" type="time" />
        </div>
        <label class="switch-line span-2">
          <span>对读者开放</span>
          <input v-model="seatForm.enabled" type="checkbox" />
        </label>
        <p v-if="seatSizeLocked" class="field-note span-2">该区域已有预约记录，行列不可调整。如需改布局，请先停用并新建区域。</p>
        <p v-else class="field-note span-2">保存后按行列自动生成座位，编号从 A-01 起。开放时长至少 2 小时。</p>
      </div>
      <template #footer>
        <button class="btn btn-ghost" type="button" :disabled="seatBusy" @click="seatModalOpen = false">取消</button>
        <button class="btn btn-primary" type="button" :disabled="seatBusy" @click="saveSeatArea">{{ seatBusy ? '保存中…' : '保存' }}</button>
      </template>
    </BaseModal>

    <BaseModal :busy="sourceBusy || pgProbeBusy" :open="sourceModalOpen" :title="editingSource ? '编辑套录源' : '新增套录源'" size="md" @close="sourceModalOpen = false">
      <div class="form-grid">
        <div class="field">
          <label for="src-type">类型</label>
          <select id="src-type" v-model="sourceForm.type" class="select">
            <option v-for="item in SOURCE_TYPES" :key="item.key" :value="item.key">{{ item.label }}</option>
          </select>
        </div>
        <div class="field">
          <label for="src-name">名称</label>
          <input id="src-name" v-model="sourceForm.name" class="input" maxlength="64" placeholder="如 国家图书馆 Z39.50" />
        </div>
        <template v-if="sourceForm.type !== 'local'">
          <div class="field">
            <label for="src-host">主机</label>
            <input id="src-host" v-model="sourceForm.host" class="input" placeholder="host 或 IP" />
          </div>
          <div class="field">
            <label for="src-port">端口</label>
            <input id="src-port" v-model.number="sourceForm.port" class="input" type="number" min="1" max="65535" />
          </div>
          <div class="field">
            <label for="src-database">数据库 / 库名</label>
            <input id="src-database" v-model="sourceForm.database" class="input" />
          </div>
          <div class="field">
            <label for="src-user">用户名</label>
            <input id="src-user" v-model="sourceForm.user" class="input" autocomplete="off" />
          </div>
          <div class="field">
            <label for="src-password">密码</label>
            <input id="src-password" v-model="sourceForm.password" class="input" type="password" autocomplete="new-password" />
          </div>
        </template>
        <div class="field">
          <label for="src-priority">优先级</label>
          <select id="src-priority" v-model.number="sourceForm.priority" class="select">
            <option v-for="opt in SOURCE_PRIORITY_OPTIONS" :key="opt.value" :value="opt.value">
              {{ opt.label }}
            </option>
          </select>
          <p class="field-note">同一层级内，高优先级的源先检索。</p>
        </div>
        <!-- 字符集**必须在这里选**：检索词要按它编码后发给目标源（见 z39.50.py
             `_query_encoding`），选错的后果是静默 0 命中——服务端不报错，就是查不到。
             记录格式则相反：解析时按 MARC 头标区第 9 位自动识别，不用选
             （原先那个 `record_syntax` 字段存了却从不被读，已从表单移除）。 -->
        <div v-if="sourceForm.type !== 'local'" class="field">
          <label for="src-charset">字符集</label>
          <select id="src-charset" v-model="sourceForm.charset" class="select">
            <option v-for="opt in SOURCE_CHARSETS" :key="opt.key" :value="opt.key">{{ opt.label }}</option>
          </select>
          <p class="field-note">按目标源要求选；选错会查不到（不报错）。</p>
        </div>
        <p class="field-note span-2">记录格式按 MARC 头标区自动识别，无需选择。</p>
        <div v-if="sourceForm.type !== 'local'" class="settings-pg-probe span-2">
          <div class="settings-pg-head">
            <span>连接测试</span>
            <button class="btn btn-outline btn-sm" type="button" :disabled="srcTest.busy || sourceBusy" @click="testSourceInModal">
              <AppIcon name="compass" />{{ srcTest.busy ? '测试中…' : editingSource ? '测试连接' : '保存并测试' }}
            </button>
          </div>
          <p v-if="srcTest.message" class="field-note" :class="{ 'is-error': srcTest.status === 'fail' }">{{ srcTest.message }}</p>
          <p v-else class="field-note">
            {{ editingSource ? '按当前已保存的配置发起一次检索，确认地址与账号可用。' : '新套录源需先保存，点击后将先保存再测试。' }}
          </p>
        </div>
        <div v-if="sourceForm.type === 'pg'" class="settings-pg-probe span-2">
          <div class="settings-pg-head">
            <span>连接探测</span>
            <button class="btn btn-outline btn-sm" type="button" :disabled="pgProbeBusy" @click="probePgSource">
              <AppIcon name="database" />{{ pgProbeBusy ? '探测中…' : '探测表结构' }}
            </button>
          </div>
          <p v-if="pgProbeError" class="field-note is-error">{{ pgProbeError }}</p>
          <template v-else-if="pgProbeResult">
            <p class="field-note">{{ pgProbeResult.server || '连接正常' }}</p>
            <div class="settings-pg-tables">
              <span v-for="table in pgProbeResult.tables.slice(0, 12)" :key="`${table.schema}.${table.table}`" class="settings-pg-table">{{ table.schema }}.{{ table.table }}</span>
              <span v-if="pgProbeResult.tables.length > 12" class="field-note">另有 {{ pgProbeResult.tables.length - 12 }} 张表</span>
            </div>
          </template>
          <p v-else class="field-note">按当前主机、库名与账号连接一次，确认可访问的表后再保存。</p>
        </div>
        <label class="switch-line span-2">
          <span>启用该套录源</span>
          <input v-model="sourceForm.enabled" type="checkbox" />
        </label>
        <p v-if="editingSource" class="field-note span-2">密码显示为 *** 表示保持原密码不变</p>
      </div>
      <template #footer>
        <button class="btn btn-ghost" type="button" :disabled="sourceBusy || pgProbeBusy" @click="sourceModalOpen = false">取消</button>
        <button class="btn btn-primary" type="button" :disabled="sourceBusy" @click="saveSource">{{ sourceBusy ? '保存中…' : '保存' }}</button>
      </template>
    </BaseModal>

    <ConfirmDialog
      :open="Boolean(libraryDeleteTarget)"
      title="删除图书库"
      :message="libraryDeleteTarget ? `确认删除「${libraryDeleteTarget.name}」？仅当库内没有书目且不是最后一个图书库时才能删除。` : ''"
      confirm-text="删除"
      danger
      :busy="libraryBusy"
      @cancel="libraryDeleteTarget = null"
      @confirm="deleteLibrary"
    />

    <ConfirmDialog
      :open="Boolean(seatDeleteTarget)"
      title="删除座位区域"
      :message="seatDeleteTarget ? `确认删除「${seatDeleteTarget.name}」及其 ${seatDeleteTarget.seat_count} 个座位？已有预约记录的区域不能删除。` : ''"
      confirm-text="删除区域"
      danger
      :busy="seatBusy"
      @cancel="seatDeleteTarget = null"
      @confirm="deleteSeatArea"
    />

    <ConfirmDialog
      :open="Boolean(sourceDeleteTarget)"
      title="删除套录源"
      :message="sourceDeleteTarget ? `确认删除「${sourceDeleteTarget.name}」？删除后相关套录检索将不再可用。` : ''"
      confirm-text="删除"
      danger
      :busy="sourceBusy"
      @cancel="sourceDeleteTarget = null"
      @confirm="deleteSource"
    />
  </section>
</template>
