<template>
  <el-card shadow="never" class="legacy-page legacy-barcode-page">
    <PageHeader :icon="Tickets" title="条码管理"
      :desc="isWithdrawnView ? '已注销的册，可批量恢复' : '条码、馆藏地点与状态管理'" />
    <!-- ① 筛选栏：查册条件 + 查询/重置 -->
    <div class="app-row bc-filters">
      <el-select v-model="libId" placeholder="全部图书库" clearable style="width: 160px"
        @change="reload">
        <el-option v-for="l in libs" :key="l.id" :label="l.name" :value="l.id" />
      </el-select>
      <el-select v-model="status" style="width: 130px" @change="reload">
        <el-option :label="holdingStatusLabel('AVAILABLE')" value="active" />
        <el-option :label="holdingStatusLabel('WITHDRAWN')" value="WITHDRAWN" />
        <el-option :label="holdingStatusLabel('LOST')" value="LOST" />
        <!-- 停流通/另库保存（D14）：走 /holdings 的通用 status 过滤，不是注销清单 -->
        <el-option :label="holdingStatusLabel('SUSPENDED')" value="SUSPENDED" />
        <el-option label="全部状态" value="all" />
      </el-select>
      <el-input v-model="q" placeholder="条码片段，如 0002" clearable style="width: 170px"
        @keyup.enter="reload" @clear="reload" />
      <el-select v-if="!isWithdrawnView" v-model="loc" placeholder="全部馆藏地点" clearable
        style="width: 160px" @change="reload">
        <el-option v-for="x in locations" :key="x" :label="x" :value="x" />
      </el-select>
      <el-select v-else v-model="reason" placeholder="全部注销原因" clearable
        style="width: 150px" @change="reload">
        <el-option v-for="x in REASONS" :key="x" :label="x" :value="x" />
      </el-select>
      <template v-if="isWithdrawnView">
        <el-date-picker v-model="dateFrom" type="date" placeholder="注销起始日"
          value-format="YYYY-MM-DD" style="width: 150px" @change="reload" />
        <el-date-picker v-model="dateTo" type="date" placeholder="注销截止日"
          value-format="YYYY-MM-DD" style="width: 150px" @change="reload" />
      </template>
      <el-button type="primary" :loading="loading" @click="reload">查询</el-button>
      <el-button @click="reset">重置</el-button>
    </div>

    <!-- ② 批量动作：依赖勾选，与筛选分开一档 -->
    <div class="app-row bc-actions">
      <span class="bc-selection" :class="{ 'is-active': sel.length }">
        <template v-if="sel.length">已勾选 <strong>{{ sel.length }}</strong> 册</template>
        <template v-else>勾选后可批量操作</template>
      </span>
      <el-button v-if="sel.length" link size="small" class="bc-clear" @click="clearSelection">
        清除选择
      </el-button>
      <div class="app-spacer" />
      <template v-if="sel.length">
        <el-button v-if="!isWithdrawnView" @click="batchLocation">
          批量改地点（{{ sel.length }}）
        </el-button>
        <el-button v-if="!isWithdrawnView" type="warning" @click="doWithdraw">
          注销（{{ sel.length }}）
        </el-button>
        <el-button v-else type="success" @click="doRestore">
          恢复（{{ sel.length }}）
        </el-button>
        <el-button v-if="!isWithdrawnView" type="danger" @click="batchDelete">
          批量删除（{{ sel.length }}）
        </el-button>
      </template>
      <ColumnPicker :columns="cols.columns" :hidden="cols.hidden" :shown="cols.shown"
        :visible-count="cols.visibleCount"
        @toggle="cols.toggle" @show-all="cols.showAll" @reset="cols.reset" />
    </div>

    <el-table ref="tableRef" :max-height="tableMaxH" :data="rows" v-loading="loading" border stripe
      @selection-change="(v: any[]) => sel = v" @sort-change="onSortChange">
      <el-table-column type="selection" width="44" />
      <el-table-column v-if="cols.isVisible('barcode')" prop="barcode" label="条码" width="165" sortable="custom">
        <template #default="{ row }">
          <el-input
            v-if="editingBarcodeId === row.id"
            :id="`barcode-edit-${row.id}`"
            v-model="barcodeDraft"
            size="small"
            maxlength="32"
            @keyup.enter="commitBarcode(row)"
            @keyup.esc.stop="cancelBarcode"
            @blur="commitBarcode(row)"
          />
          <button
            v-else-if="row.status === 'AVAILABLE'"
            class="bc-barcode-trigger"
            type="button"
            :aria-label="`编辑条码 ${row.barcode}`"
            @click="startBarcodeEdit(row)"
          >
            <span>{{ row.barcode }}</span>
            <el-icon class="bc-barcode-pencil"><EditPen /></el-icon>
          </button>
          <span v-else class="bc-barcode-readonly">{{ row.barcode }}</span>
        </template>
      </el-table-column>
      <el-table-column v-if="cols.isVisible('title')" prop="title" label="题名" min-width="260" show-overflow-tooltip sortable="custom" />
      <el-table-column v-if="cols.isVisible('call_no')" prop="call_no" label="索书号" width="135" show-overflow-tooltip sortable="custom" />
      <el-table-column v-if="!isWithdrawnView && cols.isVisible('location')" prop="location" label="馆藏地点" width="210"
        sortable="custom">
        <template #default="{ row }">
          <div class="bc-location">
            <el-select
              v-if="editingLocationId === row.id"
              :ref="(el: any) => bindLocationSelect(row.id, el)"
              v-model="locationDraft"
              size="small"
              placeholder="未指定" aria-label="馆藏地点"
              clearable
              @change="commitLocation(row)"
              @visible-change="onLocationVisible"
            >
              <el-option v-for="x in locations" :key="x" :label="x" :value="x" />
            </el-select>
            <button
              v-else
              type="button"
              class="bc-location-trigger"
              :aria-label="`编辑馆藏地点 ${row.location || '未指定'}`"
              @click="startLocationEdit(row)"
            >
              <span :class="{ 'is-empty': !row.location }">{{ row.location || '未指定' }}</span>
              <el-icon class="bc-location-pencil"><EditPen /></el-icon>
            </button>
            <span v-if="row.shelf_no" class="bc-shelf">{{ row.shelf_no }}</span>
          </div>
        </template>
      </el-table-column>
      <el-table-column v-else-if="cols.isVisible('location')" prop="location" label="馆藏地点" width="190"
        show-overflow-tooltip sortable="custom">
        <template #default="{ row }">{{ holdingLocationLabel(row.location, row.shelf_no) }}</template>
      </el-table-column>
      <el-table-column v-if="cols.isVisible('status')" prop="status" label="状态" width="96" sortable="custom">
        <template #default="{ row }">
          <el-tag v-if="row.status === 'AVAILABLE'" type="success" size="small">在架</el-tag>
          <el-tag v-else-if="row.status === 'WITHDRAWN'" type="info" size="small">已注销</el-tag>
          <el-tag v-else-if="row.status === 'LOST'" type="danger" size="small">已遗失</el-tag>
          <!-- 停流通不是注销：用深色 warning 让它在列表里比「外借中」更扎眼 -->
          <el-tag v-else-if="row.status === 'SUSPENDED'" type="warning" effect="dark" size="small">
            {{ holdingStatusLabel(row.status) }}
          </el-tag>
          <el-tag v-else type="warning" size="small">{{ holdingStatusLabel(row.status) }}</el-tag>
        </template>
      </el-table-column>
      <template v-if="isWithdrawnView">
        <el-table-column v-if="cols.isVisible('withdraw_no')" prop="withdraw_no" label="注销号" width="130" show-overflow-tooltip sortable="custom" />
        <el-table-column v-if="cols.isVisible('withdraw_reason')" prop="withdraw_reason" label="注销原因" width="110" sortable="custom" />
        <el-table-column v-if="cols.isVisible('withdrawn_by')" prop="withdrawn_by" label="注销人" width="100" />
        <el-table-column v-if="cols.isVisible('withdrawn_at')" prop="withdrawn_at" label="注销时间" width="175" show-overflow-tooltip sortable="custom">
          <template #default="{ row }">{{ fmt(row.withdrawn_at) }}</template>
        </el-table-column>
      </template>
      <el-table-column v-if="cols.isVisible('library_id')" prop="library_id" label="所属图书库" width="160" show-overflow-tooltip sortable="custom">
        <template #default="{ row }">{{ libName(row.library_id) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="220" fixed="right">
        <template #default="{ row }">
          <div class="bc-row-actions">
            <el-button size="small" link type="primary" @click="openRecord(row)">记录</el-button>
            <!-- 停流通（D14）：在架册才可停；不是注销，故与「注销」并列但文案不同 -->
            <el-button v-if="row.status === 'AVAILABLE'" size="small" link type="warning"
              @click="openSuspend(row)">停流通</el-button>
            <el-button v-if="row.status === 'AVAILABLE'" size="small" link type="warning"
              @click="withdrawOne(row)">注销</el-button>
            <!-- 停流通的册只能「解除停流通」：后端 restore 只接受 WITHDRAWN/LOST，
                 点「恢复」必然失败；其余非在架状态保持原来的「恢复」 -->
            <el-button v-if="row.status === 'SUSPENDED'" size="small" link type="success"
              @click="releaseOne(row)">解除停流通</el-button>
            <el-button v-else-if="row.status !== 'AVAILABLE'" size="small" link type="success"
              @click="restoreOne(row)">恢复</el-button>
            <el-button v-if="row.status === 'AVAILABLE'" size="small" link type="danger"
              @click="removeOne(row)">删除</el-button>
          </div>
        </template>
      </el-table-column>
      <template #empty>{{ isWithdrawnView ? '当前没有符合条件的注销记录' : '当前没有符合条件的条码' }}</template>
    </el-table>

    <el-pagination style="margin-top: 10px" background layout="total, sizes, prev, pager, next"
      :total="total" v-model:current-page="page" v-model:page-size="pageSize"
      :page-sizes="[20, 50, 100, 200]" @current-change="loadFromPager" @size-change="reloadFromPager" />

    <el-dialog v-model="batchLocationOpen" title="批量改地点" width="420px" append-to-body>
      <p class="bc-batch-hint">把勾选的 {{ sel.length }} 个条码的馆藏地点改为（留空为清除）：</p>
      <el-select v-model="batchLocationValue" placeholder="选择馆藏地点" clearable style="width: 100%">
        <el-option v-for="x in locations" :key="x" :label="x" :value="x" />
      </el-select>
      <template #footer>
        <el-button @click="batchLocationOpen = false">取消</el-button>
        <el-button type="primary" @click="confirmBatchLocation">确定</el-button>
      </template>
    </el-dialog>

    <!-- 停流通 / 另库保存（契约 D14）：原因必填；批次/审批依据/去向进审计，
         Phase 2 起也落 holdings 的同名列。不是注销——剔旧损毁请走「注销」。 -->
    <el-dialog v-model="suspendOpen" title="停流通（另库保存）" width="460px" append-to-body
      @closed="onSuspendClosed">
      <p class="bc-batch-hint">
        条码 {{ suspendRow?.barcode }}（《{{ suspendRow?.title ?? '' }}》）将停止流通、移出 905，
        不再外借或转库，但仍是馆藏资产（进资产台账）。
      </p>
      <el-form label-width="86px" label-position="left" @submit.prevent>
        <el-form-item label="停流通原因" required>
          <el-input v-model="suspendForm.reason" maxlength="64" show-word-limit
            placeholder="审查清理-不适宜 / 外观差 / 隔离待处置" />
        </el-form-item>
        <el-form-item label="审查批次">
          <el-input v-model="suspendForm.review_batch_no" maxlength="32"
            placeholder="可留空，如 SH-2026-01" />
        </el-form-item>
        <el-form-item label="审批依据">
          <el-input v-model="suspendForm.approval_ref" maxlength="128"
            placeholder="可留空，如 校图审〔2026〕3号" />
        </el-form-item>
        <el-form-item label="去向">
          <el-input v-model="suspendForm.destination" maxlength="128"
            placeholder="可留空，如 闭架库 A" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="suspendOpen = false">取消</el-button>
        <el-button type="warning" :loading="suspendBusy" @click="confirmSuspend">确定停流通</el-button>
      </template>
    </el-dialog>
  </el-card>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref } from 'vue'
import { EditPen, Tickets } from '@element-plus/icons-vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'

import PageHeader from '../components/PageHeader.vue'
import ColumnPicker from '../components/ColumnPicker.vue'
import { useColumnPrefs } from '../composables/useColumnPrefs'
import { holdingLocationLabel, holdingStatusLabel } from '../../lib/displayLabels'
import { scrollElementToTop } from '../../lib/scroll'
import { resetTableScroll, useTableFill } from '../composables/useTableFill'
import {
  apiErrorText, batchDeleteHoldings, batchUpdateLocation, deleteHolding,
  holdingLocations, listBookLibraries, listWithdrawn, releaseHoldings, restoreHoldings,
  searchHoldings, suspendHoldings, updateHolding, withdrawHoldings,
} from '../api'

const REASONS = ['剔旧', '损毁', '遗失', '其他']

const router = useRouter()

/**
 * 可隐藏的列。`key` 是 localStorage 里偏好的键，必须稳定；
 * 注销相关的四列只在「已注销」视图出现，但在两个视图共用同一份偏好。
 */
const cols = useColumnPrefs('barcodes', [
  { key: 'barcode', label: '条码' },
  { key: 'title', label: '题名' },
  { key: 'call_no', label: '索书号' },
  { key: 'location', label: '馆藏地点' },
  { key: 'status', label: '状态' },
  { key: 'withdraw_no', label: '注销号' },
  { key: 'withdraw_reason', label: '注销原因' },
  { key: 'withdrawn_by', label: '注销人' },
  { key: 'withdrawn_at', label: '注销时间' },
  { key: 'library_id', label: '所属图书库' },
])
const rows = ref<any[]>([])
const sel = ref<any[]>([])
const tableRef = ref<any>(null)
const { maxHeight: tableMaxH } = useTableFill(tableRef)
const libs = ref<any[]>([])
const locations = ref<string[]>([])
const libId = ref<number | undefined>(undefined)
const status = ref('active')
const q = ref('')
const loc = ref<string | undefined>(undefined)
const reason = ref<string | undefined>(undefined)
const dateFrom = ref<string | undefined>(undefined)
const dateTo = ref<string | undefined>(undefined)
const loading = ref(false)
const total = ref(0)
const page = ref(1)
const pageSize = ref(50)
// 表头排序（服务端排；键对应后端 HOLDING_SORTS / WITHDRAWN_SORTS 白名单）
const sortKey = ref('')
const sortOrder = ref('')
const editingBarcodeId = ref<number | null>(null)
const barcodeDraft = ref('')
const editingLocationId = ref<number | null>(null)
const locationDraft = ref('')
const locationSelectEl = ref<any>(null)
const batchLocationOpen = ref(false)
const batchLocationValue = ref('')

/**
 * 「停流通」对话框（单册）。四个字段对应 `POST /holdings/suspend` 的 body：
 * reason 必填，其余可选；后端把它们写进 holdings 的 suspended_* / review_batch_no /
 * approval_ref / destination 列，并进审计。
 */
const suspendOpen = ref(false)
const suspendBusy = ref(false)
const suspendRow = ref<any>(null)
const suspendForm = reactive({
  reason: '', review_batch_no: '', approval_ref: '', destination: '',
})

function clearSuspendForm() {
  suspendForm.reason = ''
  suspendForm.review_batch_no = ''
  suspendForm.approval_ref = ''
  suspendForm.destination = ''
}

/** 关闭动画走完再复位：动画期间对话框里还要显示上一册的条码。 */
function onSuspendClosed() {
  clearSuspendForm()
  suspendRow.value = null
}

/**
 * 「注销视图」= 走 `/holdings/withdrawn` 那条注销清单（多出注销号/原因/人/时间四列，
 * 批量动作是「恢复」）。
 *
 * 停流通（SUSPENDED）**不是**注销（契约 D14）：它既不在注销清单里（那边只查
 * `INACTIVE_STATUSES`），也不能走「恢复」（后端只允许 WITHDRAWN/LOST 恢复）。
 * 所以它必须留在普通视图里、按 `status=SUSPENDED` 查 `/holdings`。
 */
const isWithdrawnView = computed(
  () => status.value !== 'active' && status.value !== 'SUSPENDED')

function libName(id: number) {
  return libs.value.find(l => l.id === id)?.name ?? `库${id}`
}

function clearSelection() {
  tableRef.value?.clearSelection()
  sel.value = []
}

function startBarcodeEdit(row: any) {
  if (editingBarcodeId.value !== null) return
  editingBarcodeId.value = row.id
  barcodeDraft.value = row.barcode ?? ''
  nextTick(() => document.getElementById(`barcode-edit-${row.id}`)?.focus())
}

function cancelBarcode() {
  editingBarcodeId.value = null
  barcodeDraft.value = ''
}

async function commitBarcode(row: any) {
  if (editingBarcodeId.value !== row.id) return
  const value = barcodeDraft.value.trim()
  editingBarcodeId.value = null
  if (!value) {
    ElMessage.warning('条码不能为空')
    return
  }
  if (value === row.barcode) return
  row.barcode = value
  await saveField(row, 'barcode')
}

function bindLocationSelect(id: number, el: any) {
  if (id === editingLocationId.value) locationSelectEl.value = el
}

function startLocationEdit(row: any) {
  if (editingLocationId.value === row.id) return
  if (editingLocationId.value !== null) cancelLocation()
  editingLocationId.value = row.id
  locationDraft.value = row.location ?? ''
  // 程序化 focus 偶发不触发自动展开，显式 toggle 保证"点一下就能选"
  nextTick(() => {
    locationSelectEl.value?.toggleMenu?.()
    locationSelectEl.value?.focus?.()
  })
}

function cancelLocation() {
  editingLocationId.value = null
  locationDraft.value = ''
}

/** 下拉关闭后延后一拍再取消：change 与 visible-change 的先后顺序不稳定，
 *  先让已选中的值完成提交，再处理"点空白处退出"的情况。 */
function onLocationVisible(open: boolean) {
  if (open) return
  setTimeout(() => {
    if (editingLocationId.value !== null) cancelLocation()
  }, 0)
}

async function commitLocation(row: any) {
  if (editingLocationId.value !== row.id) return
  const value = (locationDraft.value ?? '').trim()
  editingLocationId.value = null
  if ((row.location ?? '') === value) return
  row.location = value || null
  await saveField(row, 'location')
}

function fmt(v: string | null) {
  return v ? String(v).replace('T', ' ').slice(0, 19) : '—'
}

async function loadLibs() {
  try { libs.value = await listBookLibraries() } catch { libs.value = [] }
}

async function loadLocations() {
  try {
    const d = await holdingLocations(libId.value)
    locations.value = d.locations ?? []
    if (loc.value && !locations.value.includes(loc.value)) loc.value = undefined
  } catch { locations.value = [] }
}

async function load() {
  loading.value = true
  try {
    const common = { library_id: libId.value, page: page.value, page_size: pageSize.value,
                     q: q.value.trim() || undefined,
                     sort: sortKey.value || undefined, order: sortOrder.value || undefined }
    const d = isWithdrawnView.value
      ? await listWithdrawn({ ...common, reason: reason.value,
                              date_from: dateFrom.value, date_to: dateTo.value })
      : await searchHoldings({ ...common, location: loc.value, status: status.value })
    rows.value = d.items
    total.value = d.total
    cancelBarcode()
    cancelLocation()
    clearSelection()
  } catch (e: any) {
    ElMessage.error(apiErrorText(e, '查询条码'))
  } finally { loading.value = false }
}

/** 点表头排序：必须回到第 1 页（否则看到的是"新顺序的第 N 页"，不是用户以为的从头看）。 */
function onSortChange({ prop, order }: { prop: string | null; order: string | null }) {
  sortKey.value = order && prop ? prop : ''
  sortOrder.value = order === 'descending' ? 'desc' : (order ? 'asc' : '')
  page.value = 1
  load()
}

function reload() { page.value = 1; loadLocations(); load() }

function reset() {
  libId.value = undefined; q.value = ''; loc.value = undefined
  status.value = 'active'; reason.value = undefined
  dateFrom.value = undefined; dateTo.value = undefined
  reload()
}


/** 翻页/改每页条数后：加载并把表格滚回顶部（否则新内容在上方、用户停在原地）。 */
async function loadFromPager() {
  await load()
  await nextTick()
  resetTableScroll(tableRef)   // 表体是内部滚动的，整页不动
  scrollElementToTop(tableRef.value?.$el as HTMLElement | null)
}

async function reloadFromPager() {
  // 不复用 `reload()`：它不是 async（内部 load 未 await），这里要等数据到位再滚
  page.value = 1
  await loadLocations()
  await load()
  await nextTick()
  resetTableScroll(tableRef)
  scrollElementToTop(tableRef.value?.$el as HTMLElement | null)
}

async function saveField(row: any, field: 'barcode' | 'location') {
  try {
    const d = await updateHolding(row.id, { [field]: row[field] ?? '' } as any)
    Object.assign(row, d)
    if (field === 'location') await loadLocations()
    ElMessage.success(field === 'barcode' ? '条码已更新' : '馆藏地点已更新')
  } catch (e: any) {
    ElMessage.error(apiErrorText(e, '保存'))
    load()
  }
}

async function removeOne(row: any) {
  try {
    await ElMessageBox.confirm(
      `确定删除条码 ${row.barcode}（《${row.title ?? ''}》）？删除不可恢复。\n\n` +
      `若是剔旧或损毁，请改用「注销」。`,
      '删除馆藏', { type: 'warning' })
  } catch { return }
  try {
    await deleteHolding(row.id)
    ElMessage.success('已删除')
    load()
  } catch (e: any) { ElMessage.error(apiErrorText(e, '删除')) }
}

/** 注销结果统一提示：逐条处理，成功与失败都要让馆员看到。 */
function reportOutcome(d: any, verb: string, okKey: string) {
  const ok = d[okKey] ?? 0
  const failed = d.failed ?? []
  if (ok) ElMessage.success(`已${verb} ${ok} 册`)
  if (failed.length) {
    const detail = failed.slice(0, 3)
      .map((f: any) => `${f.barcode ?? f.id}：${f.reason}`).join('；')
    ElMessage.warning(`${failed.length} 册未能${verb}——${detail}`
      + (failed.length > 3 ? ' …' : ''))
  }
  if (!ok && !failed.length) ElMessage.info('当前没有可处理的条码')
}

/**
 * 单册动作的结果提示：成功时报条码（比"已停流通 1 册"好读），失败时沿用
 * `reportOutcome` 的逐条 reason —— 后端把"外借中不能停流通"放在 `failed[].reason`
 * 里（HTTP 200），不是 400，所以必须读这个数组，不能只看 catch。
 */
function reportSingle(d: any, verb: string, okKey: string, row: any) {
  const ok = d[okKey] ?? 0
  const failed = d.failed ?? []
  if (ok && !failed.length) { ElMessage.success(`已${verb}：${row.barcode}`); return }
  reportOutcome(d, verb, okKey)
}

async function withdrawOne(row: any) {
  try {
    const r = await ElMessageBox.prompt(
      `注销条码 ${row.barcode}（《${row.title ?? ''}》）。请填写注销原因：`,
      '图书注销', { inputValue: '剔旧', inputPlaceholder: '剔旧 / 损毁 / 遗失 / 其他',
                    confirmButtonText: '确定注销', cancelButtonText: '取消' })
    if (!(r.value ?? '').trim()) { ElMessage.warning('注销原因不能为空'); return }
    const d = await withdrawHoldings([row.id], r.value.trim())
    reportOutcome(d, '注销', 'withdrawn')
    sel.value = []
    load()
  } catch (e: any) {
    if (e !== 'cancel' && e?.response) ElMessage.error(apiErrorText(e, '注销'))
  }
}

async function doWithdraw() {
  const ids = sel.value.map(r => r.id)
  try {
    const r = await ElMessageBox.prompt(
      `注销勾选的 ${ids.length} 册（共用一个注销号，并移出馆藏统计）。请填写注销原因：`,
      '批量注销', { inputValue: '剔旧', inputPlaceholder: '剔旧 / 损毁 / 遗失 / 其他',
                    confirmButtonText: '确定注销', cancelButtonText: '取消' })
    if (!(r.value ?? '').trim()) { ElMessage.warning('注销原因不能为空'); return }
    const d = await withdrawHoldings(ids, r.value.trim())
    reportOutcome(d, '注销', 'withdrawn')
    sel.value = []
    reload()
  } catch (e: any) {
    if (e !== 'cancel' && e?.response) ElMessage.error(apiErrorText(e, '批量注销'))
  }
}

async function restoreOne(row: any) {
  try {
    const d = await restoreHoldings([row.id])
    reportOutcome(d, '恢复', 'restored')
    load()
  } catch (e: any) { ElMessage.error(apiErrorText(e, '恢复')) }
}

async function doRestore() {
  const ids = sel.value.map(r => r.id)
  try {
    await ElMessageBox.confirm(
      `把勾选的 ${ids.length} 册恢复为在架？这些册将重新计入馆藏统计。`,
      '批量恢复', { type: 'warning' })
  } catch { return }
  try {
    const d = await restoreHoldings(ids)
    reportOutcome(d, '恢复', 'restored')
    sel.value = []
    reload()
  } catch (e: any) { ElMessage.error(apiErrorText(e, '批量恢复')) }
}

// ---------- 停流通 / 解除停流通（单册；契约 D14） ----------

function openSuspend(row: any) {
  clearSuspendForm()
  suspendRow.value = row
  suspendOpen.value = true
}

async function confirmSuspend() {
  const row = suspendRow.value
  if (!row) return
  const reason = suspendForm.reason.trim()
  if (!reason) { ElMessage.warning('停流通原因不能为空'); return }
  suspendBusy.value = true
  try {
    const d = await suspendHoldings([row.id], {
      reason,
      review_batch_no: suspendForm.review_batch_no.trim() || undefined,
      approval_ref: suspendForm.approval_ref.trim() || undefined,
      destination: suspendForm.destination.trim() || undefined,
    })
    suspendOpen.value = false
    reportSingle(d, '停流通', 'suspended', row)
    load()                          // 留在当前页/当前筛选，不跳页
  } catch (e: any) {
    // 走到这里是 400/500（ids 非法、无权限等）；业务拒绝是 200 + failed[]，见 reportSingle
    ElMessage.error(apiErrorText(e, '停流通'))
  } finally {
    suspendBusy.value = false
  }
}

async function releaseOne(row: any) {
  try {
    await ElMessageBox.confirm(
      `解除条码 ${row.barcode}（《${row.title ?? ''}》）的停流通，恢复为在架？` +
      `\n\n该册会重新进入 905、可外借，并按需推进预约队列。`,
      '解除停流通', { type: 'warning', confirmButtonText: '解除停流通', cancelButtonText: '取消' })
  } catch { return }
  try {
    const d = await releaseHoldings([row.id])
    reportSingle(d, '解除停流通', 'released', row)
    load()
  } catch (e: any) { ElMessage.error(apiErrorText(e, '解除停流通')) }
}

async function batchDelete() {
  const ids = sel.value.map(r => r.id)
  try {
    await ElMessageBox.confirm(
      `确定删除勾选的 ${ids.length} 个条码？\n\n` +
      `删除不可恢复；剔旧或损毁请改用「注销」。`,
      '批量删除', { type: 'warning' })
  } catch { return }
  try {
    const d = await batchDeleteHoldings(ids)
    ElMessage.success(`已删除 ${d.deleted} 个条码`)
    sel.value = []
    // 删空当前页就往前翻：否则停在 total 已变小的空页上
    const remaining = Math.max(0, total.value - d.deleted)
    const lastPage = Math.max(1, Math.ceil(remaining / pageSize.value))
    if (page.value > lastPage) page.value = lastPage
    load()
  } catch (e: any) { ElMessage.error(apiErrorText(e, '批量删除')) }
}

function batchLocation() {
  batchLocationValue.value = ''
  batchLocationOpen.value = true
}

async function confirmBatchLocation() {
  batchLocationOpen.value = false
  try {
    await batchUpdateLocation(sel.value.map(r => r.id), batchLocationValue.value.trim() || null)
    ElMessage.success('已更新馆藏地点')
    sel.value = []
    reload()
  } catch (e: any) { ElMessage.error(apiErrorText(e, '批量改地点')) }
}

function openRecord(row: any) {
  // 路由树挂在 `/admin` 下：少了这段前缀会被 catch-all 兜到工作台，
  // 用户看到的是"点了没反应"（其实是跳走了）。
  router.push(`/admin/records/${row.record_id}`)
}

onMounted(async () => {
  // 三个请求互不依赖；串行等待会让「进入页面 → 出表」白等两个往返
  await Promise.all([loadLibs(), loadLocations(), load()])
})
</script>

<style scoped>
.bc-filters { margin-bottom: 8px; }
.bc-batch-hint { margin: 0 0 10px; font-size: 13px; color: var(--muted); }
.bc-actions { min-height: 32px; margin-bottom: 12px; }
.bc-selection { font-size: 12px; color: var(--faint); }
.bc-selection.is-active { color: var(--text); font-weight: 500; }
.bc-selection strong { font-variant-numeric: tabular-nums; }
.bc-clear { margin-left: 6px; }
.bc-barcode-trigger {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  min-height: 26px;
  padding: 0 4px;
  border: 1px solid transparent;
  border-radius: 6px;
  background: transparent;
  color: var(--text);
  /* 数字对齐走 tabular-nums（设计系统口径，与图书库条码列一致）——
     原先的 ui-monospace 族使本列与全站字体脱节，已去。 */
  font-size: 13px;
  font-variant-numeric: tabular-nums;
  text-align: left;
  transition: background 0.16s var(--ease), border-color 0.16s var(--ease);
}
.bc-barcode-trigger:hover {
  border-color: var(--line);
  background: var(--surface-soft);
}
.bc-barcode-pencil {
  margin-left: auto;
  font-size: 13px;
  color: var(--faint);
  opacity: 0.55;
  transition: opacity 0.16s var(--ease), color 0.16s var(--ease);
}
.bc-barcode-trigger:hover .bc-barcode-pencil,
.bc-barcode-trigger:focus-visible .bc-barcode-pencil {
  color: var(--primary);
  opacity: 1;
}
.bc-barcode-readonly {
  padding: 0 4px;
  color: var(--text);
  font-size: 13px;
  font-variant-numeric: tabular-nums;
}
.bc-location { display: flex; align-items: center; gap: 6px; min-width: 0; }
.bc-location .el-input,
.bc-location .el-select { flex: 1; min-width: 0; }
.bc-location-trigger {
  display: flex;
  flex: 1;
  align-items: center;
  gap: 6px;
  min-width: 0;
  min-height: 26px;
  padding: 0 4px;
  border: 1px solid transparent;
  border-radius: 6px;
  background: transparent;
  color: var(--text);
  font-size: 12px;
  text-align: left;
  transition: background 0.16s var(--ease), border-color 0.16s var(--ease);
}
.bc-location-trigger:hover {
  border-color: var(--line);
  background: var(--surface-soft);
}
.bc-location-trigger > span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.bc-location-trigger .is-empty { color: var(--faint); }
.bc-location-pencil {
  margin-left: auto;
  font-size: 13px;
  color: var(--faint);
  opacity: 0.55;
  transition: opacity 0.16s var(--ease), color 0.16s var(--ease);
}
.bc-location-trigger:hover .bc-location-pencil,
.bc-location-trigger:focus-visible .bc-location-pencil {
  color: var(--primary);
  opacity: 1;
}
.bc-shelf {
  flex: none;
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
.bc-row-actions {
  display: flex;
  align-items: center;
  gap: 4px;
  white-space: nowrap;
}
.bc-row-actions :deep(.el-button) {
  margin-left: 0;
  padding: 0 2px;
}
</style>
