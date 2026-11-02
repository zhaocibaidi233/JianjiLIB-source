<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import AppIcon from '../../components/AppIcon.vue'
import PagerBar from '../../components/PagerBar.vue'
import { download, errorMessage, get, post } from '../../lib/api'
import { can } from '../../lib/auth'
import { sourceLabel } from '../../lib/displayLabels'
import { formatNumber } from '../../lib/format'
import { toast, toastError } from '../../lib/toast'
import { libraryState, loadLibraries } from '../../stores/libraries'

interface SummaryItem {
  key: string
  label: string
  /** 一句话解释：这两项的术语不看解释猜不出方向 */
  hint: string
  count: number
}

interface Summary {
  library_id: number | null
  record_total: number
  record_with_issue: number
  items: SummaryItem[]
}

interface RecordRow {
  id: number
  library_id: number
  title: string | null
  isbn: string | null
  author: string | null
  class_no: string | null
  year: number | null
  source: string | null
}

interface GapItem {
  library_id: number
  prefix?: string
  class_no?: string
  width?: number
  from: number
  to: number
  count: number
  label: string
}

interface RecordList {
  total: number
  page: number
  page_size: number
  items: RecordRow[]
}

interface Group {
  /** 冲突值：同书异号是 ISBN，异书同号是索书号 */
  key: string
  count: number
  records: RecordRow[]
}

interface GroupList {
  total: number
  page: number
  page_size: number
  groups: Group[]
}

interface GapList {
  library_id: number | null
  segment_count: number
  missing_count: number
  truncated: boolean
  items: GapItem[]
}

const libraryId = computed(() => libraryState.currentId)
const summary = ref<Summary | null>(null)
const scan = ref('no-barcode')
const records = reactive({ items: [] as RecordRow[], total: 0, page: 1, pageSize: 20, loading: false, error: '' })
const gaps = reactive({ data: null as GapList | null, loading: false, error: '' })
const groups = reactive({ items: [] as Group[], total: 0, loading: false, error: '' })
const loading = ref(true)
const error = ref('')

const GAP_KEYS = new Set(['barcode-gaps', 'book-no-gaps'])
/**
 * 这两项按「冲突键」分组展示。
 *
 * 平铺列表看不出问题：同书异号要对比同 ISBN 下**不同**的分类号，异书同号要对比
 * 同索书号下**不同**的书——列表里连索书号这一列都没有，只能看到一堆孤立条目。
 */
const GROUP_KEYS = new Set(['same-isbn-diff-class', 'same-callno-diff-book'])
const isGap = computed(() => GAP_KEYS.has(scan.value))
const isGrouped = computed(() => GROUP_KEYS.has(scan.value))
/** 分组时组头那个值叫什么 */
const groupKeyLabel = computed(() =>
  scan.value === 'same-isbn-diff-class' ? 'ISBN' : '索书号')
const activeItem = computed(() => summary.value?.items.find((item) => item.key === scan.value) || null)
const iconFor: Record<string, string> = {
  'no-barcode': 'barcode',
  'no-class': 'tag',
  'bad-class': 'alert',
  'same-isbn-diff-class': 'layers',
  'same-callno-diff-book': 'list',
  'barcode-gaps': 'barcode',
  'book-no-gaps': 'list',
}

async function loadSummary() {
  summary.value = await get<Summary>('/inspect/summary', {
    library_id: libraryId.value || undefined,
  })
}

async function loadScan() {
  if (isGap.value) {
    gaps.loading = true
    gaps.error = ''
    try {
      gaps.data = await get<GapList>(`/inspect/${scan.value}`, {
        library_id: libraryId.value || undefined,
        max_segments: 500,
      })
    } catch (e) {
      gaps.data = null
      gaps.error = errorMessage(e)
    } finally {
      gaps.loading = false
    }
    return
  }
  if (isGrouped.value) {
    groups.loading = true
    groups.error = ''
    try {
      const data = await get<GroupList>(`/inspect/${scan.value}`, {
        library_id: libraryId.value || undefined,
        page: records.page,
        page_size: records.pageSize,
        grouped: true,
      })
      groups.items = data.groups
      groups.total = data.total
    } catch (e) {
      groups.error = errorMessage(e)
      groups.items = []
      groups.total = 0
    } finally {
      groups.loading = false
    }
    return
  }
  records.loading = true
  records.error = ''
  try {
    const data = await get<RecordList>(`/inspect/${scan.value}`, {
      library_id: libraryId.value || undefined,
      page: records.page,
      page_size: records.pageSize,
    })
    records.items = data.items
    records.total = data.total
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
    await loadSummary()
    await loadScan()
  } catch (e) {
    error.value = errorMessage(e)
  } finally {
    loading.value = false
  }
}

function chooseScan(key: string) {
  if (key === scan.value) return
  scan.value = key
  records.page = 1
  void loadScan()
}

// —— 维护工具：书次号计数器重算 ——
// 计数器是独立表，直接改库/历史遗留会让它低于馆藏真实最大号，之后自动取号撞号。
// 后端按馆藏扫描、只升不降（POST /api/cataloging/callno-resync，records write 档）；
// 这里只负责触发与播报结果，扫描项照常刷新（重算不改变馆藏本身，但保险起见）。
const resyncing = ref(false)
const canResync = computed(() => can('records', 'write'))

async function resyncCallno() {
  if (resyncing.value) return
  resyncing.value = true
  try {
    const d = await post<{ keys: number; raised: { key: string; from: number; to: number }[];
      already_ok: number }>('/cataloging/callno-resync')
    if (!d.raised.length) {
      toast(`书次号计数器全部正常（核对 ${d.keys} 个键），无需修正`)
    } else {
      toast(`已抬升 ${d.raised.length} 个计数器：` +
        d.raised.slice(0, 3).map((r) => `${r.key} ${r.from}→${r.to}`).join('、') +
        (d.raised.length > 3 ? ` 等 ${d.raised.length} 个` : ''))
      void loadSummary()
    }
  } catch (e) {
    toastError(errorMessage(e))
  } finally {
    resyncing.value = false
  }
}

function setPage(page: number) {
  records.page = page
  void loadScan()
}

async function exportCurrent() {
  try {
    await download('/inspect/export', {
      scan: scan.value,
      library_id: libraryId.value || undefined,
    }, `数据体检-${activeItem.value?.label || scan.value}.xlsx`)
  } catch (e) {
    toastError(errorMessage(e))
  }
}

watch(libraryId, () => {
  records.page = 1
  void load()
})

onMounted(async () => {
  await loadLibraries()
  await load()
})
</script>

<template>
  <section class="view">
    <div v-if="loading" class="card" aria-busy="true">
      <div class="skeleton" style="height: 100px; margin: 16px" />
      <div class="skeleton" style="height: 300px; margin: 16px" />
    </div>

    <div v-else-if="error" class="card dash-error">
      <span class="error-icon"><AppIcon name="alert" /></span>
      <strong>无法读取体检结果</strong>
      <p>{{ error }}</p>
      <button class="btn btn-primary btn-sm" type="button" @click="load">重新加载</button>
    </div>

    <template v-else-if="summary">
      <div class="page-tools">
        <span class="page-tools-note">
          <AppIcon name="shield" />
          已扫描 {{ formatNumber(summary.record_total) }} 种书目，{{ formatNumber(summary.record_with_issue) }} 种存在质量问题。
        </span>
        <span class="spacer" />
        <button
          v-if="canResync"
          class="btn btn-sm"
          type="button"
          :disabled="resyncing"
          title="按馆藏索书号重算书次号计数器（只升不降）。计数器低于实际最大号时使用。"
          @click="resyncCallno"
        >
          <AppIcon name="rotate" />
          {{ resyncing ? '重算中…' : '重算书次号计数器' }}
        </button>
        <span class="page-tools-note">断号项以缺口段计数，其他项以书目条数计数</span>
      </div>

      <div class="scan-grid">
        <button
          v-for="item in summary.items"
          :key="item.key"
          class="scan-card"
          :class="{ 'is-active': scan === item.key, 'has-issue': item.count > 0 }"
          type="button"
          @click="chooseScan(item.key)"
        >
          <span class="metric-icon" :class="{ amber: item.count > 0 }"><AppIcon :name="iconFor[item.key] || 'alert'" /></span>
          <span class="scan-card-main">
            <span class="scan-card-label">{{ item.label }}</span>
            <span class="scan-card-hint">{{ item.hint }}</span>
            <span class="scan-card-value">{{ formatNumber(item.count) }}</span>
          </span>
          <span class="scan-card-mark">{{ scan === item.key ? '查看中' : '查看' }}<AppIcon name="chevron-right" /></span>
        </button>
      </div>

      <article class="data-card">
        <div class="card-head">
          <div>
            <div class="card-title">{{ activeItem?.label || '体检明细' }}</div>
            <div class="card-sub">
              <template v-if="isGap && gaps.data">
                共 {{ formatNumber(gaps.data.segment_count) }} 段，缺号 {{ formatNumber(gaps.data.missing_count) }} 个
                <span v-if="gaps.data.truncated"> · 仅显示前 500 段</span>
              </template>
              <template v-else-if="isGrouped">
          <PagerBar :page="records.page" :page-size="records.pageSize" :total="groups.total" noun="组" @update:page="setPage" />
                共 {{ formatNumber(groups.total) }} 组，涉及 {{ formatNumber(activeItem?.count || 0) }} 条书目
                · 每组就是同一个{{ groupKeyLabel }}下的冲突
              </template>
              <template v-else-if="!isGap">共 {{ formatNumber(records.total) }} 条书目 · 点击题名进入编目修改</template>
            </div>
          </div>
          <button class="btn btn-outline btn-sm" type="button" @click="exportCurrent">
            <AppIcon name="download" />导出当前项
          </button>
        </div>

        <template v-if="isGap">
          <div v-if="gaps.loading" class="table-state"><span class="spinner" />正在计算断号…</div>
          <div v-else-if="gaps.error" class="table-state">{{ gaps.error }}</div>
          <div v-else-if="!gaps.data?.items.length" class="empty-state">
            <AppIcon name="check-circle" />
            <strong>没有发现断号</strong>
            <p>该序列在当前图书库内连续完整。</p>
          </div>
          <div v-else class="gap-list">
            <div v-for="(item, index) in gaps.data.items" :key="`${item.label}-${index}`" class="gap-row">
              <span class="gap-index">{{ index + 1 }}</span>
              <div class="gap-main">
                <strong>{{ item.label }}</strong>
                <span>{{ item.prefix !== undefined ? `条码前缀 ${item.prefix || '（无前缀）'} · ${item.width} 位` : `分类号 ${item.class_no}` }}</span>
              </div>
              <span class="gap-count">{{ formatNumber(item.count) }}<small>个缺号</small></span>
            </div>
          </div>
        </template>

        <template v-else-if="isGrouped">
          <div v-if="groups.loading" class="table-state"><span class="spinner" />正在读取…</div>
          <div v-else-if="groups.error" class="table-state">{{ groups.error }}</div>
          <div v-else-if="!groups.items.length" class="empty-state">
            <AppIcon name="check-circle" />
            <strong>没有发现冲突</strong>
            <p>同一{{ groupKeyLabel }}下没有互相矛盾的书目。</p>
          </div>
          <div v-else class="group-list">
            <div v-for="group in groups.items" :key="group.key" class="group-card">
              <div class="group-head">
                <span class="group-key-label">{{ groupKeyLabel }}</span>
                <strong class="group-key">{{ group.key }}</strong>
                <span class="group-count">{{ group.count }} 条</span>
              </div>
              <div v-for="row in group.records" :key="row.id" class="group-row">
                <RouterLink class="group-title" :to="`/admin/records/${row.id}/edit`">
                  {{ row.title || '未知题名' }}
                </RouterLink>
                <span class="group-meta">
                  <span class="code-chip">{{ row.isbn || '无 ISBN' }}</span>
                  <span class="group-class">分类号 {{ row.class_no || '—' }}</span>
                  <span class="group-author">{{ row.author || '—' }}</span>
                </span>
                <RouterLink class="text-link" :to="`/admin/records/${row.id}/edit`">编目定位</RouterLink>
              </div>
            </div>
          </div>
        </template>

        <template v-else>
          <PagerBar :page="records.page" :page-size="records.pageSize" :total="records.total" noun="条书目" @update:page="setPage" />
          <div class="table-wrap">
            <table class="table">
              <thead>
                <tr><th>书目</th><th class="col-hide-md">ISBN</th><th>责任者</th><th>分类号</th><th class="col-hide-md">来源</th><th>操作</th></tr>
              </thead>
              <tbody>
                <tr v-if="records.loading"><td colspan="6" class="table-state"><span class="spinner" />正在读取体检结果…</td></tr>
                <tr v-else-if="records.error"><td colspan="6" class="table-state">{{ records.error }}</td></tr>
                <tr v-else-if="!records.items.length"><td colspan="6" class="table-state">该项没有需要处理的记录</td></tr>
                <template v-else>
                  <tr v-for="row in records.items" :key="row.id">
                    <td>
                      <RouterLink class="cell-main" :to="`/admin/records/${row.id}/edit`">{{ row.title || '未知题名' }}</RouterLink>
                      <div class="cell-sub">记录 #{{ row.id }}<template v-if="row.year"> · {{ row.year }}</template></div>
                    </td>
                    <td class="col-hide-md"><span class="code-chip">{{ row.isbn || '无 ISBN' }}</span></td>
                    <td>{{ row.author || '—' }}</td>
                    <td>{{ row.class_no || '—' }}</td>
                    <td class="col-hide-md"><span class="badge badge-neutral no-dot">{{ sourceLabel(row.source) }}</span></td>
                    <td><RouterLink class="text-link" :to="`/admin/records/${row.id}/edit`">编目定位</RouterLink></td>
                  </tr>
                </template>
              </tbody>
            </table>
          </div>
        </template>
      </article>
    </template>
  </section>
</template>

<style scoped>
.scan-card-hint {
  font-size: 11px;
  line-height: 1.4;
  color: var(--text-tertiary);
}
.group-list { display: flex; flex-direction: column; gap: 10px; padding: 4px 18px 14px; }
.group-card { border: 1px solid var(--line); border-radius: var(--radius-sm); overflow: hidden; }
.group-head {
  display: flex;
  align-items: baseline;
  gap: 8px;
  padding: 8px 12px;
  background: var(--surface-soft);
  border-bottom: 1px solid var(--line);
}
.group-key-label { font-size: 11px; color: var(--text-tertiary); }
.group-key { font-size: 13px; font-weight: 600; color: var(--text); }
.group-count { margin-left: auto; font-size: 12px; color: var(--text-secondary); }
.group-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px 12px;
  border-top: 1px solid var(--line-lighter, #f0f2ee);
}
.group-row:first-of-type { border-top: 0; }
.group-title { flex: 1 1 auto; min-width: 0; font-size: 13px; font-weight: 500; color: var(--text); }
.group-title:hover { color: var(--primary); }
.group-meta {
  display: flex; align-items: center; gap: 10px; flex: 0 0 auto;
  font-size: 12px; color: var(--text-secondary);
}
.group-class { font-variant-numeric: tabular-nums; }
.group-author { max-width: 140px; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
</style>
