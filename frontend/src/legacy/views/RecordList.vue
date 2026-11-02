<template>
  <el-card shadow="never" class="legacy-page legacy-list-page">
    <PageHeader :icon="Notebook" title="图书库"
      desc="编目成品库，按图书库管理。" />
    <!-- ① 图书库上下文 + 全局动作：本库选择靠左，「新建 / 导入导出」这类
         与勾选无关的动作靠右提上来，和下面「勾选才有意义」的批量动作分开。 -->
    <div class="rl-panel">
      <div class="rl-bar">
        <span class="rl-label">当前图书库</span>
        <el-select v-model="currentLibId" class="rl-lib" @change="onLibChange">
          <el-option v-for="l in libs" :key="l.id"
            :label="`${l.name}（${l.record_count}）`" :value="l.id" />
        </el-select>
        <el-button @click="openLibDlg">管理图书库</el-button>
        <div class="app-spacer" />
        <el-button @click="$router.push('/admin/io')">导入 / 导出</el-button>
        <el-button type="primary" @click="$router.push('/admin/records/new')">新建记录</el-button>
      </div>

      <div class="rl-query">
        <div class="rl-filters">
          <el-input v-model="f.q" placeholder="综合检索（题名 / 拼音首字母 / 条码）"
            class="rl-grow" clearable :prefix-icon="Search" @keyup.enter="search" @clear="search"
            title="支持直接扫条码定位记录" />
          <el-input v-model="f.isbn" placeholder="ISBN" class="rl-w-isbn" clearable @keyup.enter="search" />
          <el-input v-model="f.title" placeholder="题名" class="rl-w-title" clearable @keyup.enter="search" />
          <el-input v-model="f.author" placeholder="责任者" class="rl-w-author" clearable @keyup.enter="search" />
          <div class="rl-filter-actions">
            <el-button type="primary" @click="search">检索</el-button>
            <el-button @click="resetFilters">重置</el-button>
          </div>
        </div>
        <div class="rl-filters">
          <el-select v-model="f.source" placeholder="来源" class="rl-w-src" clearable @change="search">
            <el-option v-for="s in facets.sources" :key="s" :label="sourceLabel(s)" :value="s" />
          </el-select>
          <el-select v-model="f.created_by" placeholder="编目员" class="rl-w-op" clearable @change="search">
            <el-option v-for="o in facets.operators" :key="o" :label="o" :value="o" />
          </el-select>
          <el-input v-model="f.class_no" placeholder="分类号前缀，如 I247" class="rl-w-class"
            clearable @keyup.enter="search" />
          <el-date-picker v-model="dateRange" type="daterange" value-format="YYYY-MM-DD"
            start-placeholder="录入起" end-placeholder="录入止" class="rl-w-date" @change="search" />
          <div class="rl-checks">
            <el-checkbox v-model="noBarcode" @change="search"
              title="一条馆藏条码都没有的记录（多为历史遗留数据，或导入时未生成馆藏）">只看无条码</el-checkbox>
            <el-checkbox v-model="activeOnly" @change="search"
              title="默认仍可查到已全部注销的书；勾选后只看还有在架册的记录">只看在架</el-checkbox>
          </div>
        </div>
      </div>
    </div>

    <!-- ③ 批量操作：未选择时只保留状态与全选入口，选中后显示具体动作 -->
    <div class="rl-bar rl-actions">
      <span class="rl-count" :class="{ 'is-active': hasBatchSelection }">
        <template v-if="allFilterMode">已选全部 <strong>{{ total }}</strong> 条</template>
        <template v-else-if="sel.length">已勾选 <strong>{{ sel.length }}</strong> 条</template>
        <template v-else>当前条件共 <strong>{{ total }}</strong> 条</template>
      </span>
      <el-tag v-if="allFilterMode" size="small" type="warning" effect="plain">跨页</el-tag>
      <el-button v-if="allFilterMode" link size="small" @click="clearBatchSelection">取消全选</el-button>
      <el-button v-else-if="sel.length" link size="small" @click="clearBatchSelection">清除选择</el-button>
      <el-button v-else-if="total" link type="primary" size="small" @click="selectAllFiltered">
        全选筛选结果（{{ total }}）
      </el-button>
      <div class="app-spacer" />
      <ColumnPicker :columns="cols.columns" :hidden="cols.hidden" :shown="cols.shown"
        :visible-count="cols.visibleCount"
        @toggle="cols.toggle" @show-all="cols.showAll" @reset="cols.reset" />
      <template v-if="hasBatchSelection">
        <el-button :disabled="!sel.length" @click="printLabels"
          :title="allFilterMode ? '跨页全选没有具体册，无法打印书标；请逐页勾选' : ''">打印书标</el-button>
        <el-button @click="openTransfer">转库</el-button>
        <el-button type="danger" @click="batchDelete">
          批量删除{{ deleteCount ? `（${deleteCount}）` : '' }}
        </el-button>
      </template>
    </div>

    <el-alert v-if="allFilterMode" type="warning" :closable="false" show-icon style="margin-bottom: 10px"
      :title="`已选筛选结果全部 ${total} 条（跨页）`"
      :description="`转库、删除将作用于筛选结果全部 ${total} 条，而非当前页。`" />

    <el-table ref="tableRef" :max-height="tableMaxH" :data="items" v-loading="loading" border stripe @row-dblclick="(r: any) => open(r)"
      :row-class-name="() => (allFilterMode ? 'row-selected-all' : '')"
      @selection-change="(rows: any) => (sel = rows)" @sort-change="onSortChange">
      <el-table-column type="selection" width="44" />
      <el-table-column v-if="cols.isVisible('id')" prop="id" label="ID" width="64" sortable="custom" />
      <el-table-column v-if="cols.isVisible('isbn')" prop="isbn" label="ISBN" min-width="150" sortable="custom"
        label-class-name="isbn-head-th">
        <template #header>
          <span class="bc-copies-head">
            ISBN
            <button type="button" class="bc-copies bc-copies--head"
              :class="{ 'is-active': sortKey === 'copies' }"
              :title="sortKey === 'copies'
                ? (sortOrder === 'desc' ? '复本数降序，点击切换升序' : '复本数升序，点击切换降序')
                : '按复本数量排序'"
              @click.stop="toggleCopiesSort">
              ×N<span v-if="sortKey === 'copies'"
                class="bc-copies-arrow">{{ sortOrder === 'desc' ? '↓' : '↑' }}</span>
            </button>
          </span>
        </template>
        <template #default="{ row }">
          <span class="bc-isbn-cell">
            <span class="bc-isbn-text">{{ formatIsbn(row.isbn) || '—' }}</span>
            <span v-if="row.barcodes?.length > 1" class="bc-copies">×{{ row.barcodes.length }}</span>
          </span>
        </template>
      </el-table-column>
      <el-table-column v-if="cols.isVisible('title')" prop="title" label="题名" min-width="220" show-overflow-tooltip sortable="custom" />
      <el-table-column v-if="cols.isVisible('barcode')" prop="barcode" label="条码" width="200" sortable="custom">
        <template #default="{ row }">
          <el-tooltip v-if="row.barcodes?.length" :content="row.barcodes.join('、')"
            placement="top" :show-after="300">
            <span>{{ row.barcodes[0] }}</span>
          </el-tooltip>
          <el-tag v-else size="small" type="danger">无条码</el-tag>
          <!-- 在架册数 < 总册数 说明有注销/遗失；全为 0 说明整条已剔旧 -->
          <el-tag v-if="row.total_count && !row.active_count" size="small" type="info"
            style="margin-left: 4px">已全注销</el-tag>
          <el-tag v-else-if="row.active_count < row.total_count" size="small" type="warning"
            style="margin-left: 4px">已注销 {{ row.total_count - row.active_count }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column v-if="cols.isVisible('author')" prop="author" label="责任者" min-width="140" show-overflow-tooltip sortable="custom" />
      <el-table-column v-if="cols.isVisible('year')" prop="year" label="出版年" width="96" sortable="custom" />
      <!-- 索书号：`分类号/书次号[:卷册号]`（，卷册号用冒号）。
           索书号本体在馆藏册上、同种共用（README 规则 5），列表取一条在架册的值；
           该记录还没有册时退回只显示分类号 —— 不拼 `K209/:上册` 这种残缺形态。 -->
      <el-table-column v-if="cols.isVisible('call_no')" prop="call_no" label="索书号" min-width="150" show-overflow-tooltip>
        <template #default="{ row }">
          <span>{{ row.call_no || row.class_no || '—' }}</span>
        </template>
      </el-table-column>
      <el-table-column v-if="cols.isVisible('class_no')" prop="class_no" label="分类号" width="110"
        show-overflow-tooltip sortable="custom">
        <template #default="{ row }">{{ row.class_no || '—' }}</template>
      </el-table-column>
      <el-table-column v-if="cols.isVisible('shelf_no')" prop="shelf_no" label="排架号" width="110"
        show-overflow-tooltip sortable="custom">
        <template #default="{ row }">{{ row.shelf_no || '—' }}</template>
      </el-table-column>
      <el-table-column v-if="cols.isVisible('volume')" prop="volume" label="分卷号" width="110"
        show-overflow-tooltip sortable="custom">
        <template #default="{ row }">{{ row.volume || '—' }}</template>
      </el-table-column>
      <!-- 页数 / 尺寸来自 MARC 215$a、215$d，不是库里的独立列 -->
      <el-table-column v-if="cols.isVisible('pages')" prop="pages" label="页数" width="96"
        show-overflow-tooltip sortable="custom">
        <template #default="{ row }">{{ row.pages || '—' }}</template>
      </el-table-column>
      <el-table-column v-if="cols.isVisible('size')" prop="size" label="尺寸" width="96"
        show-overflow-tooltip sortable="custom">
        <template #default="{ row }">{{ row.size || '—' }}</template>
      </el-table-column>
      <el-table-column v-if="cols.isVisible('created_by')" prop="created_by" label="编目员" width="100" show-overflow-tooltip sortable="custom">
        <template #default="{ row }">
          <span v-if="row.created_by">{{ row.created_by }}</span>
          <span v-else style="color: var(--el-text-color-secondary)">—</span>
        </template>
      </el-table-column>
      <el-table-column v-if="cols.isVisible('source')" prop="source" label="来源" min-width="110" show-overflow-tooltip sortable="custom">
        <template #default="{ row }">{{ sourceLabel(row.source) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="88" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" @click="open(row)">编辑</el-button>
        </template>
      </el-table-column>
    </el-table>
    <el-pagination style="margin-top: 12px" background layout="total, prev, pager, next, sizes"
      :total="total" v-model:current-page="page" v-model:page-size="pageSize"
      :page-sizes="[20, 50, 100]" @change="loadFromPager" />

    <!-- 图书转库：选书范围沿用本页的勾选 / 按筛选整批 -->
    <TransferDialog v-model="transferDlg" :from-id="currentLibId" :from-name="currentLibName"
      :libs="libs" :ids="sel.map((r: any) => r.id)" :by-filter="allFilterMode"
      :filter="filterPayload()" :total="total" @done="afterTransfer" />

    <!-- 图书库管理对话框 -->
    <el-dialog v-model="libDlg" title="图书库管理（每个学校一个库，书次号按库独立）" width="560px">
      <el-table :data="editList" size="small" border>
        <el-table-column label="名称" min-width="180">
          <template #default="{ row }">
            <el-input v-model="row.name" size="small" />
          </template>
        </el-table-column>
        <el-table-column prop="record_count" label="记录数" width="80" />
        <!-- 排架号开关：**按图书库**粒度，落在既有的「管理图书库」对话框里
             （不新增页面/菜单，守住 README 规则 15）。拨动即存。 -->
        <el-table-column label="排架号" width="120">
          <template #default="{ row }">
            <el-switch v-model="row.shelf_no_enabled" size="small"
              active-text="开" inactive-text="关" :disabled="!row.id"
              title="该图书库的编目界面是否显示「排架号」。关闭后仅不显示，已有排架号保留。"
              @change="toggleShelfNo(row)" />
          </template>
        </el-table-column>
        <el-table-column label="操作" width="140">
          <template #default="{ row, $index }">
            <el-button size="small" link type="primary" :disabled="!row.id" @click="saveLib(row)">保存</el-button>
            <el-button size="small" link type="danger" :disabled="!row.id" @click="delLib(row, $index)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div style="display: flex; gap: 6px; margin-top: 10px">
        <el-input v-model="newLibName" placeholder="新图书库名称，如 XX小学" size="small" style="width: 220px" />
        <el-button size="small" @click="addLib">添加</el-button>
      </div>
    </el-dialog>
  </el-card>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref } from 'vue'
import { Notebook, Search } from '@element-plus/icons-vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import PageHeader from '../components/PageHeader.vue'
import ColumnPicker from '../components/ColumnPicker.vue'
import TransferDialog from '../components/TransferDialog.vue'
import { useColumnPrefs } from '../composables/useColumnPrefs'
import { sourceLabel } from '../../lib/displayLabels'
import { scrollElementToTop } from '../../lib/scroll'
import { resetTableScroll, useTableFill } from '../composables/useTableFill'
import { formatIsbn } from '../../lib/format'
import { errorMessage } from '../../lib/api'
import { setCurrentLibrary } from '../../stores/libraries'
import { apiErrorText, batchDeleteRecords, getShelfNoSetting, http,
         listRecords, putShelfNoSetting, recordFacets } from '../api'

const router = useRouter()

/**
 * 可隐藏的列。`key` 必须稳定：它同时是 localStorage 里偏好的键，
 * 改 key 等于把所有人的设置清空一次；改 `label` 只影响下拉里的文案。
 * 勾选列与「操作」列不在其中——那两列没有「要不要看」的余地。
 */
const cols = useColumnPrefs('records', [
  { key: 'id', label: 'ID' },
  { key: 'isbn', label: 'ISBN' },
  { key: 'title', label: '题名' },
  { key: 'barcode', label: '条码' },
  { key: 'author', label: '责任者' },
  { key: 'year', label: '出版年' },
  { key: 'call_no', label: '索书号' },
  // 下面这些是"偶尔才要瞄一眼"的字段：默认隐藏，需要时在「列」里勾出来。
  // 默认隐藏而不是删掉，是因为排架、分卷、页数/尺寸都属于查架核对时要看的，
  // 常驻又太挤（13328 条一屏本来就塞不下）。
  { key: 'class_no', label: '分类号', defaultHidden: true },
  { key: 'shelf_no', label: '排架号', defaultHidden: true },
  { key: 'volume', label: '分卷号', defaultHidden: true },
  { key: 'pages', label: '页数', defaultHidden: true },
  { key: 'size', label: '尺寸', defaultHidden: true },
  { key: 'created_by', label: '编目员' },
  { key: 'source', label: '来源' },
])
const f = reactive({ q: '', isbn: '', title: '', author: '',
                     source: '', created_by: '', class_no: '' })
const items = ref<any[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
// 表头排序（服务端排 —— 分页列表只排"当前这一页"会骗人）。
// 键与后端 `RECORD_SORTS` 白名单一一对应；空 = 用后端默认（id desc）。
const sortKey = ref('')
const sortOrder = ref('')
const loading = ref(false)
const sel = ref<any[]>([])
const tableRef = ref<any>(null)
const { maxHeight: tableMaxH } = useTableFill(tableRef)
// 剔旧后整条记录都已注销的书默认仍可查到（编目记录要留底），
// 勾上「只看在架」才过滤掉，避免静默改变检索结果。
const activeOnly = ref(false)
// 常用筛选（2026-10-07 用户要求）：来源 / 编目员 / 分类号前缀 / 录入时间 / 无条码
const noBarcode = ref(false)
const dateRange = ref<[string, string] | null>(null)
const facets = ref<{ sources: string[]; operators: string[] }>({ sources: [], operators: [] })
// 「选中当前筛选的全部 N 条」：跨页整批选中，删除走 filter 分支（带 expected_total）
const allFilterMode = ref(false)
const hasBatchSelection = computed(() => allFilterMode.value || sel.value.length > 0)
const deleteCount = computed(() => (allFilterMode.value ? total.value : sel.value.length))

// 图书库
const libs = ref<any[]>([])
const currentLibId = ref<number | null>(Number(localStorage.getItem('jianji.library')) || null)
const libDlg = ref(false)
const editList = ref<any[]>([])
const newLibName = ref('')
// 图书转库
const transferDlg = ref(false)
const currentLibName = computed(
  () => libs.value.find((l: any) => l.id === currentLibId.value)?.name ?? '—')

function openTransfer() {
  if (!currentLibId.value) return ElMessage.warning('请先选择一个图书库')
  transferDlg.value = true
}
/** 转完要回到本库重新看：记录可能已被删、册数已变。 */
function afterTransfer() {
  allFilterMode.value = false
  load()
  loadLibs()
}

async function loadLibs() {
  libs.value = (await http.get('/book-libraries')).data
  if (!currentLibId.value || !libs.value.some(l => l.id === currentLibId.value)) {
    currentLibId.value = libs.value[0]?.id ?? null
  }
  localStorage.setItem('jianji.library', String(currentLibId.value ?? ''))
  setCurrentLibrary(currentLibId.value)
}

/** 对话框里的行是副本（改名字不直接动列表数据），每行自带排架号开关的初值。 */
function cloneLibs(): any[] {
  return libs.value.map((l: any) => ({ ...l, shelf_no_enabled: false }))
}

/** 打开「管理图书库」：先铺副本，再逐库拉一次排架号开关（失败静默降级 false）。 */
async function openLibDlg() {
  editList.value = cloneLibs()
  libDlg.value = true
  await loadShelfNoSettings()
}

async function loadShelfNoSettings() {
  for (const row of editList.value) {
    if (!row.id) continue
    try {
      row.shelf_no_enabled = !!(await getShelfNoSetting(row.id)).enabled
    } catch { row.shelf_no_enabled = false }
  }
}

/** 拨动即存（与对话框里「保存」改名并列）；失败把开关拨回去，别留下假状态。 */
async function toggleShelfNo(row: any) {
  if (!row.id) { row.shelf_no_enabled = false; return }
  try {
    await putShelfNoSetting(row.id, !!row.shelf_no_enabled)
  } catch (e: any) {
    row.shelf_no_enabled = !row.shelf_no_enabled
    ElMessage.error(apiErrorText(e, '保存排架号开关'))
  }
}

function onLibChange() {
  localStorage.setItem('jianji.library', String(currentLibId.value ?? ''))
  setCurrentLibrary(currentLibId.value)
  search()
}

/** 把界面上的筛选条件整理成后端参数（列表与「按筛选删」共用同一份）。 */
function filterPayload() {
  return {
    library_id: currentLibId.value || undefined,
    q: f.q || undefined, isbn: f.isbn || undefined, title: f.title || undefined,
    author: f.author || undefined, source: f.source || undefined,
    created_by: f.created_by || undefined, class_no: f.class_no || undefined,
    no_barcode: noBarcode.value || undefined,
    active_only: activeOnly.value || undefined,
    created_from: dateRange.value?.[0] || undefined,
    created_to: dateRange.value?.[1] || undefined,
  }
}

/** 改检索条件后必须回到第 1 页：否则在第 5 页搜一个只有 1 页的关键词会得到空表。 */
function search() {
  page.value = 1
  allFilterMode.value = false       // 条件变了，"全选"就不成立了
  load()
}

function resetFilters() {
  f.q = ''; f.isbn = ''; f.title = ''; f.author = ''
  f.source = ''; f.created_by = ''; f.class_no = ''
  noBarcode.value = false; activeOnly.value = false; dateRange.value = null
  search()
}

async function load() {
  loading.value = true
  sel.value = []            // 重新加载后清空勾选，避免删到上一屏/别的书库的记录
  try {
    const d = await listRecords({
      ...filterPayload(), page: page.value, page_size: pageSize.value,
      sort: sortKey.value || undefined, order: sortOrder.value || undefined,
    })
    items.value = d.items
    total.value = d.total
  } catch (e: any) {
    ElMessage.error(apiErrorText(e, '加载记录'))
  } finally {
    loading.value = false
  }
}

/** 翻页/改每页条数后：加载并把列表滚回顶部（否则新内容在上方、用户停在原地）。 */
async function loadFromPager() {
  await load()
  await nextTick()
  resetTableScroll(tableRef)   // 表体是内部滚动的，整页不动
  scrollElementToTop(tableRef.value?.$el as HTMLElement | null)
}

/**
 * 点表头排序。Element Plus 的 `order` 是 `'ascending' | 'descending' | null`
 * （再点一次会取消 → `null`，此时回退后端默认排序）。
 * ⚠️ 排序变了**必须回到第 1 页** —— 否则在第 5 页按题名升序，看到的是"新顺序的第 5 页"，
 * 和用户以为的"从头看"完全不是一回事。
 */
function onSortChange({ prop, order }: { prop: string | null; order: string | null }) {
  sortKey.value = order && prop ? prop : ''
  sortOrder.value = order === 'descending' ? 'desc' : (order ? 'asc' : '')
  page.value = 1
  load()
}

/** 复本数不在表头列上：点行内 ×N 胶囊切换排序，同时清掉表头残留的排序箭头。 */
function toggleCopiesSort() {
  tableRef.value?.clearSort()
  if (sortKey.value === 'copies') {
    sortOrder.value = sortOrder.value === 'desc' ? 'asc' : 'desc'
  } else {
    sortKey.value = 'copies'
    sortOrder.value = 'desc'
  }
  page.value = 1
  load()
}

async function loadFacets() {
  try {
    facets.value = await recordFacets(currentLibId.value)
  } catch { facets.value = { sources: [], operators: [] } }
}

function selectAllFiltered() {
  tableRef.value?.clearSelection()
  sel.value = []
  allFilterMode.value = true
}

function clearBatchSelection() {
  allFilterMode.value = false
  tableRef.value?.clearSelection()
  sel.value = []
}

// 批量删除：图书库以条码为核心，删记录会连带删掉其馆藏条码，必须明确告知
async function batchDelete() {
  const byFilter = allFilterMode.value
  const ids = sel.value.map((r: any) => r.id)
  const n = byFilter ? total.value : ids.length
  if (!n) return
  const sample = byFilter
    ? '（按当前筛选条件整批处理，跨页生效）'
    : sel.value.slice(0, 3).map((r: any) => `《${r.title ?? '—'}》`).join('、')
      + (ids.length > 3 ? ` 等 ${ids.length} 条` : '')
  try {
    await ElMessageBox.confirm(
      `确定删除 ${byFilter ? '筛选出的' : '勾选的'} ${n} 条记录？\n\n` +
      `${sample}\n\n记录下的馆藏条码会一并删除，不可恢复。`,
      '批量删除', { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' })
  } catch { return }        // 用户取消
  try {
    const d = byFilter
      ? await batchDeleteRecords({ filter: filterPayload(), expected_total: total.value })
      : await batchDeleteRecords({ ids })
    ElMessage.success(`已删除 ${d.deleted} 条记录`)
    allFilterMode.value = false
    // 删空当前页就往前翻：否则停在 total 已变小的空页上
    const remaining = Math.max(0, total.value - d.deleted)
    const lastPage = Math.max(1, Math.ceil(remaining / pageSize.value))
    if (page.value > lastPage) page.value = lastPage
    load()
    loadFacets()
  } catch (e: any) {
    ElMessage.error(apiErrorText(e, '批量删除'))
  }
}

function open(row: any) { router.push(`/admin/records/${row.id}/edit`) }

// 书标按"册"打，必须拿到具体 id —— 跨页全选没有 id 集合，所以该模式下禁用（提示见按钮 disabled）
function printLabels() {
  if (!sel.value.length) return
  router.push(`/admin/labels?ids=${sel.value.map(r => r.id).join(',')}`)
}

// 管理对话框
async function addLib() {
  const name = (newLibName.value || '').trim()
  if (!name) return ElMessage.warning('请输入图书库名称')
  try {
    await http.post('/book-libraries', { name })
    newLibName.value = ''
    await loadLibs()
    editList.value = cloneLibs()
    ElMessage.success(`图书库「${name}」已创建`)
  } catch (e: any) {
    ElMessage.error(errorMessage(e) || '创建失败')
  }
}
async function saveLib(row: any) {
  try {
    await http.put(`/book-libraries/${row.id}`, { name: row.name, note: row.note })
    ElMessage.success('已保存')
    await loadLibs()
  } catch (e: any) {
    ElMessage.error(errorMessage(e) || '保存失败')
  }
}
async function delLib(row: any, index: number) {
  // 删除图书库不可逆；后端在该库仍有记录时会拒绝（400），文案据此说明，避免误导。
  try {
    await ElMessageBox.confirm(
      `确定删除图书库「${row.name}」？删除后不可恢复。` +
      `该库下仍有记录（当前 ${row.record_count ?? 0} 条）时无法删除，请先删除或迁移这些记录。`,
      '删除图书库', { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' })
  } catch { return }        // 用户取消
  try {
    await http.delete(`/book-libraries/${row.id}`)
    editList.value.splice(index, 1)
    await loadLibs()
    ElMessage.success('已删除')
  } catch (e: any) {
    ElMessage.error(errorMessage(e) || '删除失败')
  }
}

onMounted(async () => {
  await loadLibs()
  await loadFacets()
  await load()
})
</script>

<style scoped>
/* 列表页工具栏：库上下文 / 筛选 / 批量动作三档，宽度统一收在这里 */
.rl-bar { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.rl-filters { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.rl-actions { min-height: 32px; }
.rl-label { font-size: 13px; color: var(--app-text-muted); }
.rl-count { font-size: 12px; color: var(--app-text-muted); }
.rl-count.is-active { color: var(--app-text); font-weight: 500; }
.rl-count strong { font-variant-numeric: tabular-nums; }

.rl-grow { flex: 1 1 300px; min-width: 200px; }
.rl-w-isbn { width: 160px; }
.rl-w-title { width: 160px; }
.rl-w-author { width: 140px; }
.rl-w-src { width: 140px; }
.rl-w-op { width: 130px; }
.rl-w-class { width: 170px; }
.rl-w-date { width: 260px; }
.bc-isbn-cell {
  display: flex;
  align-items: center;
  gap: 8px;
}
.bc-isbn-text { white-space: nowrap; }
.bc-copies {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  margin-left: auto;
  padding: 1px 6px;
  border: 0;
  border-radius: 999px;
  background: color-mix(in srgb, var(--app-accent) 14%, transparent);
  font-family: inherit;
  font-size: 12px;
  font-weight: 500;
  line-height: 1.5;
  color: color-mix(in srgb, var(--app-accent-deep) 85%, transparent);
}
.bc-copies-arrow { font-size: 10px; line-height: 1; }
.bc-copies-head { display: inline-flex; align-items: center; }
.bc-copies--head {
  position: absolute;
  right: 10px;
  top: 50%;
  transform: translateY(-50%);
  margin-left: 0;
  padding: 0 5px;
  font-size: 11px;
  cursor: pointer;
  transition: background 0.16s var(--ease), color 0.16s var(--ease);
}
.bc-copies--head:hover,
.bc-copies--head.is-active {
  background: color-mix(in srgb, var(--app-accent) 24%, transparent);
  color: var(--app-accent-deep);
}
:deep(.isbn-head-th) { position: relative; }

/* 窄屏：主检索独占一行，条件与操作各自成行，避免按钮被挤到空行右侧 */
@media (max-width: 1360px) {
  .rl-grow { flex: 1 1 100%; }
  .rl-checks { flex: 1 1 100%; }
}

/* 跨页全选时给行加淡底：否则用户看不出"选中的是筛选结果"而不是当前页 */
:deep(.row-selected-all) { background: var(--el-color-warning-light-9); }
</style>
