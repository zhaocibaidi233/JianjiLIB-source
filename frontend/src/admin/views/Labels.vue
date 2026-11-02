<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import JsBarcode from 'jsbarcode'
import AppIcon from '../../components/AppIcon.vue'
import BarcodeSvg from '../../components/BarcodeSvg.vue'
import BaseModal from '../../components/BaseModal.vue'
import { can } from '../../lib/auth'
import { errorMessage, get, put } from '../../lib/api'
import { formatNumber } from '../../lib/format'
import { toast, toastError } from '../../lib/toast'
import { libraryState, loadLibraries } from '../../stores/libraries'

interface LabelItem {
  kind: 'field' | 'barcode' | 'text'
  key?: string
  text?: string
  pt?: number
  bold?: boolean
}

interface LabelStyleRow {
  items: LabelItem[]
}

interface LabelStyle {
  id: string
  name: string
  row_gap: number
  rows: LabelStyleRow[]
}

interface LabelFormat {
  paper: 'A4' | 'custom'
  paper_w: number
  paper_h: number
  direction: 'portrait' | 'landscape'
  margin_top: number
  margin_left: number
  label_w: number
  label_h: number
  gap_h: number
  gap_v: number
  rows: number
  cols: number
  align: 'left' | 'center' | 'right'
  pad_book_no: boolean
  pad_width: number
  font_family: string
  style_id: string
  styles: LabelStyle[]
}

interface Candidate {
  holding_id: number
  record_id: number
  library_id: number
  barcode: string
  call_no: string | null
  volume: string | null
  shelf_no: string | null
  title: string | null
  copy_index: number
}

interface CandidateResult {
  total: number
  returned: number
  truncated: boolean
  items: Candidate[]
}

const DEFAULT_FORMAT: LabelFormat = {
  paper: 'A4',
  paper_w: 21,
  paper_h: 29.7,
  direction: 'portrait',
  margin_top: 900,
  margin_left: 600,
  label_w: 3800,
  label_h: 2600,
  gap_h: 200,
  gap_v: 200,
  rows: 10,
  cols: 5,
  align: 'center',
  pad_book_no: true,
  pad_width: 4,
  font_family: '宋体',
  style_id: 's-default',
  styles: [
    {
      id: 's-default',
      name: '三栏式',
      row_gap: 0,
      rows: [
        { items: [{ kind: 'field', key: 'callNo', pt: 14, bold: false }] },
        { items: [{ kind: 'field', key: 'shelfNo', pt: 14, bold: false }] },
        { items: [{ kind: 'field', key: 'barcodeText', pt: 14, bold: false }] },
      ],
    },
  ],
}

const SYMBOLOGIES = [
  { value: 'CODE39', label: 'Code 39' },
  { value: 'CODE128', label: 'Code 128' },
  { value: 'ITF14', label: 'ITF-14' },
  { value: 'EAN13', label: 'EAN-13' },
] as const
type Symbology = (typeof SYMBOLOGIES)[number]['value']
const SYMBOLOGY_KEY = 'jianji.label.symbology'
const FIELD_OPTIONS = [
  { key: 'callNo', label: '索书号' },
  { key: 'classNo', label: '分类号' },
  { key: 'bookNo', label: '书次号' },
  { key: 'volume', label: '卷册号' },
  { key: 'shelfNo', label: '排架号' },
  { key: 'copyNo', label: '复本号' },
  { key: 'title', label: '题名' },
  { key: 'barcodeText', label: '条码数字' },
]

const route = useRoute()
const recordScope = computed(() => {
  const value = route.query.ids
  if (value === undefined) return null
  const parts = (Array.isArray(value) ? value.join(',') : String(value ?? '')).split(',')
  if (parts.length > 2000 || parts.some((part) => !/^[0-9]{1,10}$/.test(part.trim())
    || Number(part) < 1 || Number(part) > 2147483647)) {
    return { ids: '', error: '选书记录 ID 无效，请从图书库重新选择' }
  }
  return { ids: [...new Set(parts.map((part) => Number(part)))].join(','), error: '' }
})
const canSaveFormat = computed(() => can('settings', 'write'))
const format = ref<LabelFormat>(structuredClone(DEFAULT_FORMAT))
const symbology = ref<Symbology>(
  (localStorage.getItem(SYMBOLOGY_KEY) as Symbology) || 'CODE39',
)
const candidates = ref<Candidate[]>([])
const total = ref(0)
const truncated = ref(false)
const selected = ref<number[]>([])
const loading = ref(true)
const listError = ref('')
const savingFormat = ref(false)
const settingsOpen = ref(false)
const styleOpen = ref(false)
const styleDraft = ref<LabelStyle | null>(null)
const styleError = ref('')
const filters = reactive({
  q: '', location: '', classNo: '',
  createdFrom: '', createdTo: '', prefix: '', from: '', to: '',
})
const filterLibraryId = ref<number | null>(null)
const locations = ref<string[]>([])
const timePreset = ref<'all' | 'today' | '3d' | '7d' | '30d' | 'custom'>('7d')
const TIME_PRESETS = [
  { key: 'all', label: '全部' },
  { key: 'today', label: '今天' },
  { key: '3d', label: '近 3 天' },
  { key: '7d', label: '近 7 天' },
  { key: '30d', label: '近 30 天' },
] as const

const form = reactive({
  paper: 'A4' as 'A4' | 'custom',
  paperW: 21,
  paperH: 29.7,
  direction: 'portrait' as 'portrait' | 'landscape',
  marginTop: 9,
  marginLeft: 6,
  labelW: 38,
  labelH: 26,
  gapH: 2,
  gapV: 2,
  rows: 10,
  cols: 5,
  align: 'center' as 'left' | 'center' | 'right',
  fontFamily: '宋体',
  styleId: 's-default',
})

const currentStyle = computed(() =>
  format.value.styles.find((item) => item.id === form.styleId) || format.value.styles[0],
)
const selectedCandidates = computed(() => candidates.value.filter((row) => selected.value.includes(row.holding_id)))
const perPage = computed(() => Math.max(1, form.rows * form.cols))
const pages = computed(() =>
  selectedCandidates.value.length ? Math.ceil(selectedCandidates.value.length / perPage.value) : 0,
)
/* 预览翻页：手动指定当前页，超出范围自动回弹到最后一页 / 第一页 */
const previewPage = ref(1)
const previewRows = computed(() => {
  const start = (previewPage.value - 1) * perPage.value
  return selectedCandidates.value.slice(start, start + perPage.value)
})
watch([previewPage, pages], () => {
  if (pages.value > 0 && previewPage.value > pages.value) previewPage.value = pages.value
  if (previewPage.value < 1) previewPage.value = 1
})
function goPreviewPage(delta: number) {
  previewPage.value = Math.min(Math.max(1, previewPage.value + delta), Math.max(1, pages.value))
}
const paperWidthMm = computed(() => (form.direction === 'portrait' ? form.paperW * 10 : form.paperH * 10))
const paperHeightMm = computed(() => (form.direction === 'portrait' ? form.paperH * 10 : form.paperW * 10))
const sheetStyle = computed(() => ({
  width: `${paperWidthMm.value}mm`,
  minHeight: `${paperHeightMm.value}mm`,
  paddingTop: `${form.marginTop}mm`,
  paddingLeft: `${form.marginLeft}mm`,
  gridTemplateColumns: `repeat(${form.cols}, ${form.labelW}mm)`,
  gridAutoRows: `${form.labelH}mm`,
  columnGap: `${form.gapH}mm`,
  rowGap: `${form.gapV}mm`,
  fontFamily: fontFamilyCss(form.fontFamily),
}))
/* 预览缩放：纸面按真实毫米渲染（A4 ≈ 794px），要塞进左栏就得等比缩到栏宽。
   用 ResizeObserver 量滚动区的可用宽度，只缩不放（>100% 时保持原尺寸）。 */
const MM_PX = 96 / 25.4
const sheetScroll = ref<HTMLElement | null>(null)
const sheetBoxW = ref(0)
const sheetScale = computed(() => {
  const paperPx = paperWidthMm.value * MM_PX
  if (!sheetBoxW.value || paperPx <= 0) return 1
  return Math.min(1, sheetBoxW.value / paperPx)
})
const fitBoxStyle = computed(() => ({
  width: `${paperWidthMm.value * MM_PX * sheetScale.value}px`,
  height: `${paperHeightMm.value * MM_PX * sheetScale.value}px`,
}))
const sheetInnerStyle = computed(() => ({
  ...sheetStyle.value,
  transform: `scale(${sheetScale.value})`,
  transformOrigin: 'top left',
}))
let sheetObserver: ResizeObserver | null = null
watch(sheetScroll, (el) => {
  sheetObserver?.disconnect()
  sheetObserver = null
  if (!el) return
  sheetObserver = new ResizeObserver((entries) => {
    const box = entries[0]?.contentRect
    if (box) sheetBoxW.value = box.width
  })
  sheetObserver.observe(el)
})
onBeforeUnmount(() => sheetObserver?.disconnect())

const allChecked = computed(() =>
  candidates.value.length > 0 && candidates.value.every((row) => selected.value.includes(row.holding_id)),
)

const layoutOverflow = computed(() => {
  const width = form.cols * form.labelW + Math.max(0, form.cols - 1) * form.gapH + form.marginLeft
  const height = form.rows * form.labelH + Math.max(0, form.rows - 1) * form.gapV + form.marginTop
  return width > paperWidthMm.value + 0.01 || height > paperHeightMm.value + 0.01
})

function fitToPaper() {
  const availW = paperWidthMm.value - form.marginLeft - Math.max(0, form.cols - 1) * form.gapH
  const availH = paperHeightMm.value - form.marginTop - Math.max(0, form.rows - 1) * form.gapV
  if (availW <= 0 || availH <= 0) return
  form.labelW = Math.floor((availW / form.cols) * 10) / 10
  form.labelH = Math.floor((availH / form.rows) * 10) / 10
}

function fontFamilyCss(family: string): string {
  if (family === '宋体') return '"SimSun", "Songti SC", serif'
  if (family === '黑体') return '"SimHei", "Heiti SC", sans-serif'
  if (family === '微软雅黑') return '"Microsoft YaHei", sans-serif'
  return 'ui-monospace, "Cascadia Code", Consolas, monospace'
}

function applyFormat(value: LabelFormat) {
  format.value = value
  form.paper = value.paper
  form.paperW = value.paper_w
  form.paperH = value.paper_h
  form.direction = value.direction
  form.marginTop = value.margin_top / 100
  form.marginLeft = value.margin_left / 100
  form.labelW = value.label_w / 100
  form.labelH = value.label_h / 100
  form.gapH = value.gap_h / 100
  form.gapV = value.gap_v / 100
  form.rows = value.rows
  form.cols = value.cols
  form.align = value.align
  form.fontFamily = value.font_family
  form.styleId = value.style_id
}

async function loadFormat() {
  try {
    const data = await get<{ value: LabelFormat }>('/settings/label_format')
    applyFormat(data.value)
  } catch {
    applyFormat(structuredClone(DEFAULT_FORMAT))
  }
}

let candidateRequest = 0
let locationRequest = 0
let mounted = false
function candidateParams() {
  return {
    record_ids: recordScope.value?.ids,
    library_id: filterLibraryId.value || undefined,
    q: filters.q.trim() || undefined,
    location: filters.location || undefined,
    class_no: filters.classNo.trim() || undefined,
    created_from: filters.createdFrom || undefined,
    created_to: filters.createdTo || undefined,
    barcode_prefix: filters.prefix.trim() || undefined,
    barcode_from: filters.from ? Number(filters.from) : undefined,
    barcode_to: filters.to ? Number(filters.to) : undefined,
    limit: 500,
  }
}
function clearCandidates() {
  candidateRequest += 1
  candidates.value = []
  total.value = 0
  truncated.value = false
  selected.value = []
}
async function loadCandidates() {
  clearCandidates()
  const request = candidateRequest
  const params = candidateParams()
  const current = () => request === candidateRequest
    && JSON.stringify(params) === JSON.stringify(candidateParams())
  loading.value = true
  listError.value = ''
  if (recordScope.value?.error) {
    listError.value = recordScope.value.error
    loading.value = false
    return
  }
  try {
    const data = await get<CandidateResult>('/labels/candidates', params)
    if (!current()) return
    candidates.value = data.items
    total.value = data.total
    truncated.value = data.truncated
    selected.value = data.items.map((row) => row.holding_id)
  } catch (e) {
    if (current()) listError.value = errorMessage(e)
  } finally {
    if (current()) loading.value = false
  }
}

function toggleAll() {
  selected.value = allChecked.value ? [] : candidates.value.map((row) => row.holding_id)
}

function toggleOne(id: number) {
  const index = selected.value.indexOf(id)
  if (index >= 0) selected.value.splice(index, 1)
  else selected.value.push(id)
}

function isoDay(offset: number): string {
  const day = new Date()
  day.setDate(day.getDate() + offset)
  return `${day.getFullYear()}-${String(day.getMonth() + 1).padStart(2, '0')}-${String(day.getDate()).padStart(2, '0')}`
}

function applyTimePreset(key: typeof timePreset.value) {
  timePreset.value = key
  if (key === 'all') {
    filters.createdFrom = ''
    filters.createdTo = ''
    return
  }
  if (key === 'custom') return
  const days = key === 'today' ? 0 : key === '3d' ? 2 : key === '7d' ? 6 : 29
  filters.createdFrom = isoDay(-days)
  filters.createdTo = isoDay(0)
}

async function loadLocations() {
  const request = ++locationRequest
  const library = filterLibraryId.value
  const current = () => request === locationRequest && library === filterLibraryId.value
  try {
    const data = await get<{ locations: string[] }>('/holdings/locations', {
      library_id: library || undefined,
    })
    if (!current()) return
    locations.value = data.locations
    if (filters.location && !data.locations.includes(filters.location)) filters.location = ''
  } catch {
    if (current()) locations.value = []
  }
}

async function onLibraryFilterChange() {
  clearCandidates()
  const request = candidateRequest
  await loadLocations()
  if (request === candidateRequest && mounted) await loadCandidates()
}

function resetAndLoad() {
  void loadCandidates()
}

function resetFilterValues() {
  filters.q = ''
  filters.location = ''
  filters.classNo = ''
  filters.prefix = ''
  filters.from = ''
  filters.to = ''
  // 显式记录范围跨库有效，不默认叠加当前库或近七日；筛选仍可在该范围内收窄。
  filterLibraryId.value = recordScope.value ? null : libraryState.currentId
  applyTimePreset(recordScope.value ? 'all' : '7d')
}

async function resetFilters() {
  clearCandidates()
  const request = candidateRequest
  resetFilterValues()
  await loadLocations()
  if (request === candidateRequest && mounted) await loadCandidates()
}

watch(() => route.query, () => {
  clearCandidates()
  resetFilterValues()
  if (mounted) void resetFilters()
}, { deep: true, flush: 'sync' })

function libraryName(id: number): string {
  return libraryState.items.find((item) => item.id === id)?.name || `库 ${id}`
}

function fieldValue(candidate: Candidate, key: string | undefined): string {
  if (!key) return ''
  const classNo = (candidate.call_no || '').split('/')[0] || ''
  const bookNo = (candidate.call_no || '').split('/').slice(1).join('/') || ''
  if (key === 'callNo') return candidate.call_no || ''
  if (key === 'classNo') return classNo
  if (key === 'bookNo') return bookNo
  if (key === 'volume') return candidate.volume || ''
  if (key === 'shelfNo') return candidate.shelf_no || ''
  if (key === 'copyNo') return String(candidate.copy_index)
  if (key === 'title') return candidate.title || ''
  if (key === 'barcodeText') return candidate.barcode
  return ''
}

function itemCss(item: LabelItem) {
  return {
    fontSize: item.pt ? `${item.pt}pt` : undefined,
    fontWeight: item.bold ? 700 : 400,
  }
}

function saveFormat() {
  const payload: LabelFormat = {
    ...format.value,
    paper: form.paper,
    paper_w: Number(form.paperW),
    paper_h: Number(form.paperH),
    direction: form.direction,
    margin_top: Math.round(Number(form.marginTop) * 100),
    margin_left: Math.round(Number(form.marginLeft) * 100),
    label_w: Math.round(Number(form.labelW) * 100),
    label_h: Math.round(Number(form.labelH) * 100),
    gap_h: Math.round(Number(form.gapH) * 100),
    gap_v: Math.round(Number(form.gapV) * 100),
    rows: Number(form.rows),
    cols: Number(form.cols),
    align: form.align,
    font_family: form.fontFamily,
    style_id: form.styleId,
  }
  if (!Number.isFinite(payload.label_w) || payload.label_w < 1000 || payload.label_w > 20000
    || !Number.isFinite(payload.label_h) || payload.label_h < 1000 || payload.label_h > 20000) {
    toastError('标签宽高应在 10–200 mm 之间')
    return
  }
  savingFormat.value = true
  put('/settings/label_format', { value: payload })
    .then(() => {
      format.value = payload
      toast('书标版式已保存')
    })
    .catch((e) => toastError(errorMessage(e)))
    .finally(() => {
      savingFormat.value = false
    })
}

watch(symbology, (value) => localStorage.setItem(SYMBOLOGY_KEY, value))

function cloneStyle(style: LabelStyle): LabelStyle {
  return JSON.parse(JSON.stringify(style)) as LabelStyle
}

function openStyleEditor() {
  styleDraft.value = cloneStyle(currentStyle.value)
  styleError.value = ''
  styleOpen.value = true
}

function addStyleRow() {
  if (!styleDraft.value) return
  styleDraft.value.rows.push({ items: [{ kind: 'field', key: 'title', pt: 9 }] })
}

function removeStyleRow(index: number) {
  if (!styleDraft.value || styleDraft.value.rows.length <= 1) return
  styleDraft.value.rows.splice(index, 1)
}

function moveStyleRow(index: number, direction: -1 | 1) {
  if (!styleDraft.value) return
  const target = index + direction
  if (target < 0 || target >= styleDraft.value.rows.length) return
  const [row] = styleDraft.value.rows.splice(index, 1)
  styleDraft.value.rows.splice(target, 0, row)
}

function addStyleItem(rowIndex: number) {
  if (!styleDraft.value) return
  const row = styleDraft.value.rows[rowIndex]
  if (!row || row.items.length >= 12) return
  row.items.push({ kind: 'text', text: '·', pt: 9 })
}

function removeStyleItem(rowIndex: number, itemIndex: number) {
  if (!styleDraft.value) return
  styleDraft.value.rows[rowIndex]?.items.splice(itemIndex, 1)
}

function changeItemKind(item: LabelItem) {
  if (item.kind === 'field') {
    item.key = item.key || 'title'
    delete item.text
  } else if (item.kind === 'barcode') {
    delete item.key
    delete item.text
  } else {
    item.text = item.text || ''
    delete item.key
  }
}

function applyStyle() {
  if (!styleDraft.value) return
  const draft = styleDraft.value
  if (!draft.name.trim()) {
    styleError.value = '样式名称不能为空'
    return
  }
  if (!draft.rows.length || draft.rows.every((row) => !row.items.length)) {
    styleError.value = '样式至少需要一栏内容'
    return
  }
  for (const row of draft.rows) {
    for (const item of row.items) {
      if (!item.pt || item.pt < 6 || item.pt > 24) {
        styleError.value = '字号应在 6–24 pt 之间'
        return
      }
    }
  }
  const index = format.value.styles.findIndex((item) => item.id === draft.id)
  if (index >= 0) format.value.styles[index] = cloneStyle(draft)
  else format.value.styles.push(cloneStyle(draft))
  form.styleId = draft.id
  styleOpen.value = false
  toast('样式已应用，保存版式后生效')
}

function duplicateStyle() {
  const base = cloneStyle(currentStyle.value)
  const id = `s-${Date.now().toString(36)}`
  base.id = id
  base.name = `${base.name} 拷贝`
  format.value.styles.push(base)
  form.styleId = id
  toast('已复制该样式，可继续编辑')
  openStyleEditor()
}

/* ---------------- print ---------------- */
function escapeHtml(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

function barcodeSvgMarkup(value: string, heightPt = 30): string {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg')
  try {
    JsBarcode(svg, value, {
      format: symbology.value,
      displayValue: false,
      height: heightPt,
      width: 1.4,
      margin: 0,
      lineColor: '#182420',
      background: 'transparent',
    })
    return svg.outerHTML
  } catch {
    return `<span class="bad">${escapeHtml(value)}</span>`
  }
}

function labelHtml(candidate: Candidate): string {
  const rows = (currentStyle.value?.rows || [])
    .map((row) => {
      const items = row.items.map((item) => {
        if (item.kind === 'barcode') {
          return `<span class="l-barcode">${barcodeSvgMarkup(candidate.barcode)}</span>`
        }
        const text = item.kind === 'text'
          ? item.text || ''
          : fieldValue(candidate, item.key)
        const style = `font-size:${item.pt || 9}pt;font-weight:${item.bold ? 700 : 400};`
        return `<span style="${style}">${escapeHtml(text)}</span>`
      }).join('')
      return `<div class="l-row">${items}</div>`
    })
    .join('')
  return `<div class="label align-${form.align}">${rows}</div>`
}

function pageHtml(rows: Candidate[]): string {
  return `<div class="sheet" style="grid-template-columns:repeat(${form.cols},${form.labelW}mm);grid-auto-rows:${form.labelH}mm;column-gap:${form.gapH}mm;row-gap:${form.gapV}mm;padding:${form.marginTop}mm 0 0 ${form.marginLeft}mm;">${rows.map(labelHtml).join('')}</div>`
}

function printLabels() {
  if (!selectedCandidates.value.length) {
    toastError('请先选择要打印的条码')
    return
  }
  const win = window.open('', '_blank', 'width=920,height=800')
  if (!win) {
    toastError('浏览器拦截了打印窗口，请允许弹出窗口后重试')
    return
  }
  const pagesHtml: string[] = []
  for (let i = 0; i < selectedCandidates.value.length; i += perPage.value) {
    pagesHtml.push(pageHtml(selectedCandidates.value.slice(i, i + perPage.value)))
  }
  win.document.write(`<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
    <title>书标打印</title>
    <style>
      @page { size: ${paperWidthMm.value}mm ${paperHeightMm.value}mm; margin: 0; }
      * { box-sizing: border-box; }
      body { margin: 0; background: #fff; color: #182420; font-family: ${fontFamilyCss(form.fontFamily)}; }
      .sheet { width: ${paperWidthMm.value}mm; height: ${paperHeightMm.value}mm; display: grid; page-break-after: always; overflow: hidden; }
      .sheet:last-child { page-break-after: auto; }
      .label { overflow: hidden; display: flex; flex-direction: column; justify-content: center; gap: 0.2mm; padding: 0.6mm; }
      .align-left { align-items: flex-start; text-align: left; }
      .align-center { align-items: center; text-align: center; }
      .align-right { align-items: flex-end; text-align: right; }
      .l-row { display: flex; align-items: center; justify-content: inherit; width: 100%; gap: 1mm; line-height: 1.1; }
      .align-center .l-row { justify-content: center; }
      .align-right .l-row { justify-content: flex-end; }
      .l-barcode { display: block; width: 100%; line-height: 0; }
      .l-barcode svg { width: 100%; height: auto; max-height: 13mm; }
      .bad { font-size: 6pt; color: #bf5540; border: 1px dashed #bf5540; }
    </style></head><body>${pagesHtml.join('')}</body></html>`)
  win.document.close()
  win.focus()
  window.setTimeout(() => win.print(), 350)
}

function holdingSub(row: Candidate): string {
  const parts: string[] = []
  if (row.volume) parts.push(`卷册 ${row.volume}`)
  if (row.shelf_no) parts.push(`架位 ${row.shelf_no}`)
  return parts.join(' · ') || '—'
}

onMounted(async () => {
  await loadLibraries()
  mounted = true
  resetFilterValues()
  const request = candidateRequest
  await loadLocations()
  await Promise.all([
    loadFormat(), request === candidateRequest && mounted ? loadCandidates() : Promise.resolve(),
  ])
})

/* 版式设置是浮层：ESC 关闭 */
function onSettingsKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape' && settingsOpen.value) {
    event.preventDefault()
    settingsOpen.value = false
  }
}
onMounted(() => window.addEventListener('keydown', onSettingsKeydown))

onBeforeUnmount(() => {
  mounted = false
  candidateRequest += 1
  locationRequest += 1
  window.removeEventListener('keydown', onSettingsKeydown)
})
</script>

<template>
  <section class="view">
    <div class="page-tools">
      <span class="page-tools-note">
        <AppIcon name="info" />书标按馆藏册生成。选择条码后可预览版式并打印。
      </span>
      <span class="spacer" />
      <span class="label-selected" :class="{ 'is-empty': !selectedCandidates.length }">
        已选 <strong>{{ selectedCandidates.length }}</strong> 册
      </span>
      <button
        class="btn btn-outline btn-sm"
        type="button"
        :aria-expanded="settingsOpen"
        aria-controls="label-settings"
        @click="settingsOpen = !settingsOpen"
      >
        <AppIcon name="settings" />{{ settingsOpen ? '收起版式' : '版式设置' }}
      </button>
      <button class="btn btn-primary btn-sm" type="button" :disabled="!selectedCandidates.length" @click="printLabels">
        <AppIcon name="printer" />打印书标
      </button>
    </div>

    <div class="labels-layout">
      <!-- 左：打印预览常驻。宽度沿用原版式设置那一栏，缩放到栏宽后整页纸都看得见，
           勾选条码、调版式时都不用滚动去找预览。 -->
      <aside class="label-preview" aria-label="打印预览">
        <article class="card label-preview-card">
          <div class="card-head">
            <div>
              <div class="card-title">打印预览</div>
              <div class="card-sub">
                <template v-if="previewRows.length">缩放 {{ Math.round(sheetScale * 100) }}% · </template>第 {{ previewPage }} 页 · {{ previewRows.length }} 个书标<template v-if="pages > 1"> · 共 {{ pages }} 页</template>
              </div>
            </div>
            <div class="label-preview-tools">
              <span class="badge badge-neutral no-dot">{{ form.labelW }} × {{ form.labelH }} mm</span>
              <!-- 手动翻页：只显示一页纸时不需要翻页控件 -->
              <div v-if="pages > 1" class="label-pager">
                <button
                  class="icon-btn"
                  type="button"
                  title="上一页"
                  aria-label="预览上一页"
                  :disabled="previewPage <= 1"
                  @click="goPreviewPage(-1)"
                >
                  <AppIcon name="chevron-left" :size="16" />
                </button>
                <span class="label-pager-text">{{ previewPage }} / {{ pages }}</span>
                <button
                  class="icon-btn"
                  type="button"
                  title="下一页"
                  aria-label="预览下一页"
                  :disabled="previewPage >= pages"
                  @click="goPreviewPage(1)"
                >
                  <AppIcon name="chevron-right" :size="16" />
                </button>
              </div>
            </div>
          </div>
          <div class="card-body label-preview-body">
            <div v-if="!previewRows.length" class="empty-card">
              <AppIcon name="printer" />勾选右侧条码后显示书标预览
            </div>
            <template v-else>
              <div ref="sheetScroll" class="label-sheet-scroll">
                <div class="label-sheet-fit" :style="fitBoxStyle">
                  <div class="label-sheet" :style="sheetInnerStyle">
                    <div
                      v-for="row in previewRows"
                      :key="row.holding_id"
                      class="label-cell"
                      :class="`align-${form.align}`"
                    >
                      <div v-for="(line, lineIndex) in currentStyle?.rows || []" :key="lineIndex" class="label-row">
                        <template v-for="(item, itemIndex) in line.items" :key="itemIndex">
                          <BarcodeSvg
                            v-if="item.kind === 'barcode'"
                            :value="row.barcode"
                            :format="symbology"
                            :height="26"
                          />
                          <span v-else :style="itemCss(item)">{{ item.kind === 'text' ? item.text : fieldValue(row, item.key) }}</span>
                        </template>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
              <p v-if="pages > 1" class="field-note" style="margin-top: 10px">预览当前第 {{ previewPage }} 页，打印输出全部 {{ selectedCandidates.length }} 个书标。</p>
            </template>
          </div>
        </article>
      </aside>

      <div class="label-work">
      <article class="data-card label-candidates">
        <div class="toolbar">
          <span class="card-title" style="font-size: 14px">条码候选</span>
          <span class="toolbar-count">共 <strong>{{ formatNumber(total) }}</strong> 册 · 返回 {{ formatNumber(candidates.length) }}</span>
          <span class="toolbar-spacer" />
          <button class="btn btn-ghost btn-sm" type="button" @click="toggleAll">
            {{ allChecked ? '取消全选' : '全选' }}
          </button>
        </div>
        <div class="label-filters">
          <div class="label-filter-grid">
            <label class="filter-field">
              <span>图书库</span>
              <select v-model="filterLibraryId" class="select" aria-label="图书库" @change="onLibraryFilterChange">
                <option :value="null">全部图书库</option>
                <option v-for="lib in libraryState.items" :key="lib.id" :value="lib.id">{{ lib.name }}</option>
              </select>
            </label>
            <label class="filter-field">
              <span>关键词</span>
              <input v-model="filters.q" class="input" placeholder="题名 / 条码" aria-label="关键词" @keyup.enter="resetAndLoad">
            </label>
            <label class="filter-field">
              <span>馆藏地点</span>
              <select v-model="filters.location" class="select" aria-label="馆藏地点">
                <option value="">全部馆藏地点</option>
                <option v-for="loc in locations" :key="loc" :value="loc">{{ loc }}</option>
              </select>
            </label>
            <label class="filter-field">
              <span>分类号</span>
              <input v-model="filters.classNo" class="input" placeholder="前缀，如 I247" aria-label="分类号前缀" @keyup.enter="resetAndLoad">
            </label>
          </div>
          <div class="label-filter-bottom">
            <div class="filter-cluster">
              <span class="filter-label">录入时间</span>
              <button
                v-for="preset in TIME_PRESETS"
                :key="preset.key"
                class="chip"
                :class="{ 'is-active': timePreset === preset.key }"
                type="button"
                @click="applyTimePreset(preset.key); resetAndLoad()"
              >{{ preset.label }}</button>
              <input v-model="filters.createdFrom" type="date" class="input filter-date" aria-label="入藏开始日期" @change="timePreset = 'custom'">
              <span class="faint">至</span>
              <input v-model="filters.createdTo" type="date" class="input filter-date" aria-label="入藏结束日期" @change="timePreset = 'custom'">
            </div>
            <div class="filter-cluster">
              <span class="filter-label">连续号段</span>
              <input v-model="filters.prefix" class="input filter-prefix" placeholder="条码前缀" aria-label="条码前缀">
              <input v-model="filters.from" class="input filter-from" placeholder="起始号" aria-label="起始号" inputmode="numeric">
              <span class="faint">~</span>
              <input v-model="filters.to" class="input filter-to" placeholder="结束号" aria-label="结束号" inputmode="numeric">
            </div>
            <span class="toolbar-spacer" />
            <div class="filter-actions">
              <button class="btn btn-primary btn-sm" type="button" @click="resetAndLoad">
                <AppIcon name="search" />筛选
              </button>
              <button class="btn btn-ghost btn-sm" type="button" @click="resetFilters">重置</button>
            </div>
          </div>
        </div>
        <div v-if="truncated" class="notice-strip" style="margin: 10px 14px 0">
          <AppIcon name="alert" />
          <span>匹配 {{ formatNumber(total) }} 册，本次最多显示 500 册，请用日期或条码区间缩小范围。</span>
        </div>
        <div class="table-wrap">
          <table class="table is-striped">
            <thead>
              <tr><th style="width: 38px"><label class="check-hit"><input class="row-check" type="checkbox" :checked="allChecked" aria-label="全选条码" @change="toggleAll"></label></th><th>条码</th><th>题名</th><th>索书号</th><th>复本号</th><th>图书库</th></tr>
            </thead>
            <tbody>
              <tr v-if="loading"><td colspan="6" class="table-state"><span class="spinner" />正在读取可打印馆藏…</td></tr>
              <tr v-else-if="listError"><td colspan="6" class="table-state">{{ listError }}</td></tr>
              <tr v-else-if="!candidates.length"><td colspan="6" class="table-state">当前没有符合条件的在架馆藏</td></tr>
              <template v-else>
                <tr v-for="row in candidates" :key="row.holding_id">
                  <td><label class="check-hit"><input class="row-check" type="checkbox" :checked="selected.includes(row.holding_id)" :aria-label="`选择 ${row.barcode}`" @change="toggleOne(row.holding_id)"></label></td>
                  <td>{{ row.barcode }}</td>
                  <td><div>{{ row.title || '未知题名' }}</div><div class="cell-sub">{{ holdingSub(row) }}</div></td>
                  <td>{{ row.call_no || '—' }}</td>
                  <td class="num">{{ row.copy_index }}</td>
                  <td>{{ libraryName(row.library_id) }}</td>
                </tr>
              </template>
            </tbody>
          </table>
        </div>
      </article>

      <!-- 点浮层外侧关闭：独立一层铺在面板下方，面板本身在其上 -->
      <div v-if="settingsOpen" class="label-settings-dismiss" aria-hidden="true" @click="settingsOpen = false" />
      <aside v-if="settingsOpen" id="label-settings" class="label-settings" role="dialog" aria-label="版式设置">
        <header class="label-settings-head">
          <div>
            <div class="label-settings-title">版式设置</div>
            <div class="label-settings-sub">{{ canSaveFormat ? '保存后应用于全馆书标' : '当前账号无版式保存权限' }}</div>
          </div>
          <button class="icon-btn" type="button" title="收起版式设置" aria-label="收起版式设置" @click="settingsOpen = false">
            <AppIcon name="x" />
          </button>
        </header>
        <div class="label-settings-body">
          <div class="form-grid">
              <div class="field">
                <label>纸张</label>
                <select v-model="form.paper" class="select"><option value="A4">A4</option><option value="custom">自定义</option></select>
              </div>
              <div class="field">
                <label>方向</label>
                <select v-model="form.direction" class="select"><option value="portrait">纵向</option><option value="landscape">横向</option></select>
              </div>
              <div class="field">
                <label>纸张宽（cm）</label>
                <input v-model.number="form.paperW" class="input" type="number" step="0.1" min="5" max="100">
              </div>
              <div class="field">
                <label>纸张高（cm）</label>
                <input v-model.number="form.paperH" class="input" type="number" step="0.1" min="5" max="100">
              </div>
              <div class="field">
                <label>标签宽（mm）</label>
                <input v-model.number="form.labelW" class="input" type="number" min="10" max="200">
              </div>
              <div class="field">
                <label>标签高（mm）</label>
                <input v-model.number="form.labelH" class="input" type="number" min="10" max="200">
              </div>
              <div class="field">
                <label>列数</label>
                <input v-model.number="form.cols" class="input" type="number" min="1" max="50">
              </div>
              <div class="field">
                <label>行数</label>
                <input v-model.number="form.rows" class="input" type="number" min="1" max="50">
              </div>
              <div class="field">
                <label>水平间距（mm）</label>
                <input v-model.number="form.gapH" class="input" type="number" min="0" max="20">
              </div>
              <div class="field">
                <label>垂直间距（mm）</label>
                <input v-model.number="form.gapV" class="input" type="number" min="0" max="20">
              </div>
              <div class="field">
                <label>上边距（mm）</label>
                <input v-model.number="form.marginTop" class="input" type="number" min="0" max="50">
              </div>
              <div class="field">
                <label>左边距（mm）</label>
                <input v-model.number="form.marginLeft" class="input" type="number" min="0" max="50">
              </div>
              <div class="field">
                <label>对齐</label>
                <select v-model="form.align" class="select"><option value="left">左对齐</option><option value="center">居中</option><option value="right">右对齐</option></select>
              </div>
              <div class="field">
                <label>字体</label>
                <select v-model="form.fontFamily" class="select"><option>宋体</option><option>黑体</option><option>微软雅黑</option><option>等宽</option></select>
              </div>
              <div class="field">
                <label>码制</label>
                <select v-model="symbology" class="select">
                  <option v-for="item in SYMBOLOGIES" :key="item.value" :value="item.value">{{ item.label }}</option>
                </select>
              </div>
              <div class="field span-2">
                <label>书标样式</label>
                <select v-model="form.styleId" class="select">
                  <option v-for="style in format.styles" :key="style.id" :value="style.id">{{ style.name }}</option>
                </select>
                <div class="page-tools" style="margin: 6px 0 0">
                  <button class="btn btn-outline btn-sm" type="button" @click="openStyleEditor">
                    <AppIcon name="edit" />编辑当前样式
                  </button>
                  <button class="btn btn-ghost btn-sm" type="button" @click="duplicateStyle">
                    <AppIcon name="plus" />复制为新样式
                  </button>
                </div>
              </div>
            </div>
          </div>
        <!-- 操作区吸在卡片底部：表单可滚动时保存按钮始终可见 -->
        <footer class="label-save-actions">
          <p v-if="layoutOverflow" class="field-note is-error">
            当前行列数超出纸张可打印区域，末尾书标将被裁切。
          </p>
          <div class="label-save-row">
            <button v-if="layoutOverflow" class="btn btn-outline btn-sm" type="button" @click="fitToPaper">
              按纸张适配
            </button>
            <button class="btn btn-primary" type="button" :disabled="!canSaveFormat || savingFormat" @click="saveFormat">
              {{ savingFormat ? '保存中…' : '保存版式' }}
            </button>
          </div>
        </footer>
      </aside>
      </div>
    </div>

    <BaseModal :open="styleOpen" title="编辑书标样式" subtitle="按栏组织字段、条码或固定文字" size="lg" @close="styleOpen = false">
      <template v-if="styleDraft">
        <div class="field">
          <label for="style-name">样式名称</label>
          <input id="style-name" v-model="styleDraft.name" class="input" maxlength="32">
        </div>
        <div class="style-editor">
          <div v-for="(row, rowIndex) in styleDraft.rows" :key="rowIndex" class="style-row">
            <div class="style-row-head">
              <strong>第 {{ rowIndex + 1 }} 栏</strong>
              <span class="faint">{{ row.items.length }} 项</span>
              <span class="spacer" />
              <button class="icon-btn" type="button" title="上移" aria-label="上移" :disabled="rowIndex === 0" @click="moveStyleRow(rowIndex, -1)">
                <AppIcon name="arrow-up" :size="14" />
              </button>
              <button class="icon-btn" type="button" title="下移" aria-label="下移" :disabled="rowIndex === styleDraft.rows.length - 1" @click="moveStyleRow(rowIndex, 1)">
                <AppIcon name="arrow-down" :size="14" />
              </button>
              <button class="icon-btn" type="button" title="删除栏" aria-label="删除栏" :disabled="styleDraft.rows.length <= 1" @click="removeStyleRow(rowIndex)">
                <AppIcon name="trash" :size="14" />
              </button>
            </div>
            <div class="style-items">
              <div v-for="(item, itemIndex) in row.items" :key="itemIndex" class="style-item">
                <select v-model="item.kind" class="select" aria-label="项目类型" @change="changeItemKind(item)">
                  <option value="field">字段</option>
                  <option value="barcode">条码图形</option>
                  <option value="text">固定文字</option>
                </select>
                <select v-if="item.kind === 'field'" v-model="item.key" class="select" aria-label="字段">
                  <option v-for="field in FIELD_OPTIONS" :key="field.key" :value="field.key">{{ field.label }}</option>
                </select>
                <input v-else-if="item.kind === 'text'" v-model="item.text" class="input" maxlength="40" placeholder="固定文字">
                <span v-else class="style-barcode-label">Code 39 / {{ symbology }}</span>
                <template v-if="item.kind !== 'barcode'">
                  <input v-model.number="item.pt" class="input style-pt" type="number" min="6" max="24" aria-label="字号">
                  <span class="faint">pt</span>
                  <label class="check-line"><input v-model="item.bold" type="checkbox">加粗</label>
                </template>
                <button class="icon-btn" type="button" title="删除项目" aria-label="删除项目" @click="removeStyleItem(rowIndex, itemIndex)">
                  <AppIcon name="x" :size="14" />
                </button>
              </div>
            </div>
            <button class="btn btn-ghost btn-sm" type="button" :disabled="row.items.length >= 12" @click="addStyleItem(rowIndex)">
              <AppIcon name="plus" />添加项目
            </button>
          </div>
        </div>
        <button class="btn btn-outline btn-block btn-sm" type="button" style="margin-top: 10px" :disabled="styleDraft.rows.length >= 12" @click="addStyleRow">
          <AppIcon name="plus" />添加一栏
        </button>
        <p v-if="styleError" class="field-note is-error" style="margin-top: 10px" role="alert">{{ styleError }}</p>
        <p class="field-note" style="margin-top: 10px">应用样式后，需点击「保存版式」才会写入全馆设置。</p>
      </template>
      <template #footer>
        <button class="btn btn-ghost" type="button" @click="styleOpen = false">取消</button>
        <button class="btn btn-primary" type="button" @click="applyStyle">应用样式</button>
      </template>
    </BaseModal>
  </section>
</template>

<style scoped>
.style-editor { display: flex; flex-direction: column; gap: 10px; margin-top: 14px; }
.style-row {
  padding: 10px 12px;
  border: 1px solid var(--line);
  border-radius: 10px;
  background: var(--surface-soft);
}
.style-row-head { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.style-row-head strong { font-size: 12px; color: var(--text); }
.style-row-head .spacer { flex: 1; }
.style-row-head .icon-btn { width: 26px; height: 26px; }
.style-items { display: flex; flex-direction: column; gap: 6px; margin-bottom: 6px; }
.style-item {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  padding: 6px 8px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: 8px;
}
.style-item .select { width: auto; height: 32px; font-size: 12px; min-width: 86px; }
.style-item .input { width: auto; height: 32px; font-size: 12px; }
.style-item .style-pt { width: 58px; }
.style-barcode-label {
  flex: 1;
  min-width: 110px;
  font-size: 11px;
  color: var(--faint);
}
.style-item .check-line { font-size: 11px; }
.style-item .icon-btn { width: 26px; height: 26px; }
@media (max-width: 760px) {
  .style-item .select,
  .style-item .input { flex: 1; min-width: 110px; }
}
/* 左：打印预览常驻（占主位，缩放到栏宽后整页纸都看得见）；右：条码候选。
   两列高度统一由 --label-panel-h 控制，卡片底边严格对齐。
   版式设置是浮层，盖住候选表右侧不影响预览。 */
.labels-layout {
  --label-panel-h: calc(100dvh - var(--topbar-h, 62px) - 118px);
  display: grid;
  grid-template-columns: clamp(420px, 30vw, 620px) minmax(0, 1fr);
  gap: 16px;
  align-items: start;
}
.label-preview {
  position: sticky;
  top: calc(var(--topbar-h, 62px) + 12px);
  min-width: 0;
}
.label-preview-card {
  display: flex;
  flex-direction: column;
  height: var(--label-panel-h);
  max-height: var(--label-panel-h);
}
/* 卡头右侧：尺寸徽标 + 翻页控件 */
.label-preview-tools { display: flex; align-items: center; gap: 10px; flex-shrink: 0; }
.label-pager {
  display: flex;
  align-items: center;
  gap: 2px;
  padding: 2px 4px;
  border-radius: 999px;
  background: var(--surface-soft);
  border: 1px solid var(--line);
}
.label-pager .icon-btn { width: 26px; height: 26px; border-radius: 999px; }
.label-pager .icon-btn:disabled { opacity: 0.35; cursor: not-allowed; }
.label-pager-text {
  min-width: 46px;
  text-align: center;
  font-size: 12px;
  font-weight: 600;
  color: var(--muted);
  font-variant-numeric: tabular-nums;
}
.label-preview-body {
  display: flex;
  flex-direction: column;
  flex: 1 1 auto;
  min-height: 0;
  padding: 0 16px 16px;
}
.label-preview .label-sheet-scroll { flex: 1 1 auto; min-height: 0; }
/* 纸面按真实毫米渲染，缩放盒给出缩放后的实际尺寸，滚动区不会横向溢出 */
.label-sheet-fit { position: relative; overflow: hidden; margin: 0 auto; }
.label-work { position: relative; display: flex; flex-direction: column; min-width: 0; }
/* 右卡：与预览卡同高，多出来的交给表格内部滚动——整页不滚，预览才留得住 */
.label-candidates {
  display: flex;
  flex-direction: column;
  min-height: 0;
  height: var(--label-panel-h);
  max-height: var(--label-panel-h);
}
.label-candidates .table-wrap {
  flex: 1 1 auto;
  min-height: 240px;
  max-height: none;
  overflow: auto;
}
.label-filters {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 14px 18px 16px;
  background: var(--surface-soft);
  border-bottom: 1px solid var(--line);
}
.label-filter-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
}
.filter-field { display: flex; flex-direction: column; gap: 5px; min-width: 0; }
.filter-field > span {
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.03em;
  color: var(--muted);
}
.filter-field .input,
.filter-field .select { width: 100%; height: 36px; font-size: 12.5px; }
.label-filter-bottom { display: flex; align-items: center; flex-wrap: wrap; gap: 10px 18px; }
.filter-cluster { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }
.filter-label {
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.03em;
  color: var(--muted);
  white-space: nowrap;
}
.filter-cluster .input { height: 34px; }
.filter-date { width: 142px; }
.filter-prefix { width: 116px; }
.filter-from,
.filter-to { width: 82px; }
/* 32px 是桌面点击目标的实用下限；30px 与全局 .chip（33px）不一致，实测这排筛选
   是书标页第二小的目标（2026-10-26 评测 P2）。 */
.filter-cluster .chip { height: 32px; padding: 0 11px; font-size: 11.5px; }
.label-filters .chip.is-active {
  background: var(--label-chip-active, var(--primary));
  border-color: var(--label-chip-active, var(--primary));
  color: #fff;
}
.filter-actions { display: flex; align-items: center; gap: 8px; }
.filter-actions .btn { height: 34px; }
.label-filters + .table-wrap { border-top: 0; }
.label-selected {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 30px;
  padding: 0 10px;
  border-radius: 999px;
  background: var(--primary-soft);
  color: var(--primary-deep);
  font-size: 11.5px;
  white-space: nowrap;
}
.label-selected strong { font-weight: 700; font-variant-numeric: tabular-nums; }
.label-selected.is-empty { background: var(--surface-soft); color: var(--faint); }
.label-candidates .table td { padding-top: 9px; padding-bottom: 9px; }
.label-candidates .table .num { text-align: center; }
/* 关闭层：全屏铺一层但什么都不画，点击即收起浮层（面板本身在其上，不受影响） */
.label-settings-dismiss {
  position: fixed;
  inset: 0;
  z-index: 35;
  cursor: default;
}
@media (max-width: 1240px) {
  .label-filter-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
/* 版式设置：右侧浮出的磨砂玻璃卡片，浮在条码候选之上，不再挤走表格。
   结构上从「网格第二列」改成「绝对定位浮层」，因此与左右栏宽无关，
   窄屏回退单列时也照样浮在右侧。 */
.label-settings {
  position: absolute;
  top: 0;
  right: 0;
  z-index: 40;
  display: flex;
  flex-direction: column;
  width: min(470px, 92%);
  /* 浮层顶在内容区顶部（顶栏 + 工具条之下），高度按内容收缩，
     只在内容超长时才滚动，底边始终留在视口内。 */
  max-height: calc(100dvh - var(--topbar-h, 62px) - 84px);
  border-radius: 18px;
  overflow: hidden;
  background: var(--surface);
  border: 1px solid var(--line-strong);
  box-shadow:
    0 32px 72px -24px rgba(24, 36, 32, 0.42),
    0 10px 28px -12px rgba(24, 36, 32, 0.28);
  animation: labelPanelIn 0.24s var(--ease);
}
.label-settings-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 14px;
  padding: 16px 18px 13px;
  border-bottom: 1px solid var(--line);
  background: var(--surface-soft);
}
.label-settings-title { font-size: 15px; font-weight: 700; color: var(--ink); }
.label-settings-sub { margin-top: 3px; font-size: 12px; color: var(--muted); }
.label-settings-body {
  flex: 0 1 auto;
  min-height: 0;
  overflow: auto;
  padding: 14px 18px 4px;
}
/* 字段本身只要一个窄框，三栏排布才能把卡片压短 */
.label-settings :deep(.form-grid) { grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 9px 10px; }
.label-settings :deep(.form-grid .span-2) { grid-column: span 2; }
/* 浮层里的控件压到 34px 高：字号跟着收一档，视觉上更紧凑 */
.label-settings :deep(.field) { gap: 4px; }
.label-settings :deep(.field > label) { font-size: 11.5px; }
.label-settings :deep(.input),
.label-settings :deep(.select) { height: 34px; padding: 0 9px; font-size: 12.5px; }
.label-settings :deep(.page-tools) { display: flex; gap: 8px; }
.label-settings :deep(.page-tools .btn) { flex: 1 1 auto; }
/* 保存区吸底：表单滚动时按钮仍在视野内 */
.label-save-actions {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px 18px 14px;
  border-top: 1px solid var(--line);
  background: var(--surface-soft);
}
.label-save-row { display: flex; justify-content: flex-end; gap: 10px; }
.label-save-actions .field-note { margin: 0; }
@keyframes labelPanelIn {
  from { opacity: 0; transform: translateY(-8px) scale(0.985); }
  to { opacity: 1; transform: translateY(0) scale(1); }
}
.label-sheet-scroll {
  overflow: auto;
  padding: 12px;
  border: 1px solid var(--line);
  border-radius: 10px;
  background: #e9ede7;
}
.label-sheet {
  display: grid;
  background: #fff;
  color: #182420;
  margin: 0 auto;
  box-shadow: 0 2px 12px rgba(24, 36, 32, 0.16);
  box-sizing: border-box;
}
.label-cell {
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 0.2mm;
  padding: 0.6mm;
  overflow: hidden;
  border: 1px dashed rgba(24, 36, 32, 0.08);
}
.label-cell.align-left { align-items: flex-start; text-align: left; }
.label-cell.align-center { align-items: center; text-align: center; }
.label-cell.align-right { align-items: flex-end; text-align: right; }
.label-row {
  display: flex;
  align-items: center;
  width: 100%;
  gap: 1mm;
  line-height: 1.1;
  white-space: nowrap;
  overflow: hidden;
}
.align-center .label-row { justify-content: center; }
.align-right .label-row { justify-content: flex-end; }
.label-row :deep(.label-barcode-svg) { width: 100%; height: auto; max-height: 13mm; }
@media (max-width: 1240px) {
  /* 窄屏堆叠：先条码候选再预览（order 让预览落到下面），预览不再吸顶 */
  .labels-layout { grid-template-columns: minmax(0, 1fr); }
  .label-preview { position: static; order: 2; }
  /* 堆叠时两卡不再需要等高对齐，各自按内容展开 */
  .label-preview-card { height: auto; max-height: none; }
  .label-candidates { height: auto; max-height: none; }
  .label-candidates .table-wrap { max-height: 60dvh; }
}
@media (max-width: 760px) {
  /* 窄屏浮层改为贴底抽屉，避免盖住整屏 */
  .label-settings {
    top: auto;
    bottom: 0;
    left: 0;
    right: 0;
    width: 100%;
    max-height: 78dvh;
    border-radius: 18px 18px 0 0;
  }
  .label-settings :deep(.form-grid) { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .label-settings :deep(.form-grid .span-2) { grid-column: span 2; }
}
@media (max-width: 480px) {
  .label-settings :deep(.form-grid) { grid-template-columns: 1fr; }
  .label-settings :deep(.form-grid .span-2) { grid-column: auto; }
}
</style>
