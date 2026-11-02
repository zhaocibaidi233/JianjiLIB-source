<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import AppIcon from '../../components/AppIcon.vue'
import BaseModal from '../../components/BaseModal.vue'
import PagerBar from '../../components/PagerBar.vue'
import TableState from '../../components/TableState.vue'
import ThSort from '../../components/ThSort.vue'
import { auth } from '../../lib/auth'
import { del, errorMessage, get } from '../../lib/api'
import { usePagedList } from '../../lib/usePagedList'
import { formatDateTime, formatNumber } from '../../lib/format'
import { toast } from '../../lib/toast'

interface LogRow {
  id: number
  action: string
  action_label: string
  operator: string
  library_id: number | null
  record_id: number | null
  title: string | null
  barcode: string | null
  copies: number
  note: string | null
  created_at: string
}

interface LogList {
  items: LogRow[]
  total: number
}

interface Options {
  actions: Array<{ value: string; label: string }>
  operators: string[]
}

const { list, load: loadList, resetAndLoad: resetPageAndLoad, setPage, setSort, onSearchInput } =
  usePagedList<LogRow>({
    fetch: ({ page, pageSize, sort, order }) => get<LogList>('/record-logs', {
      q: q.value.trim() || undefined,
      action: action.value || undefined,
      operator: operator.value || undefined,
      date_from: dateFrom.value || undefined,
      date_to: dateTo.value || undefined,
      sort: sort || undefined,
      order: order || undefined,
      limit: pageSize,
      offset: (page - 1) * pageSize,
    }),
  })
const options = ref<Options>({ actions: [], operators: [] })
const q = ref('')
const action = ref('')
const operator = ref('')
const dateFrom = ref('')
const dateTo = ref('')
const detail = ref<LogRow | null>(null)
const clearOpen = ref(false)
const clearBefore = ref('')
const clearing = ref(false)
const clearError = ref('')
const isAdmin = computed(() => Boolean(auth.user?.is_admin))

async function loadOptions() {
  try {
    options.value = await get<Options>('/record-logs/options')
  } catch {
    options.value = { actions: [], operators: [] }
  }
}

function actionTone(value: string): string {
  if (value === 'delete') return 'danger'
  if (value === 'create' || value === 'import') return 'ok'
  if (value === 'settings') return 'warn'
  return 'muted'
}

function actionIcon(value: string): string {
  if (value === 'create') return 'plus'
  if (value === 'delete') return 'trash'
  if (value === 'import') return 'upload'
  if (value === 'settings') return 'settings'
  return 'edit'
}

function openClear() {
  const date = new Date()
  date.setDate(date.getDate() - 90)
  clearBefore.value = date.toISOString().slice(0, 10)
  clearError.value = ''
  clearOpen.value = true
}

async function submitClear() {
  if (!clearBefore.value) {
    clearError.value = '请选择清理截止日期'
    return
  }
  clearing.value = true
  try {
    const data = await del<{ deleted: number }>(`/record-logs?before=${encodeURIComponent(clearBefore.value)}`)
    toast(`已清理 ${data.deleted} 条日志`)
    clearOpen.value = false
    list.page = 1
    await loadList()
  } catch (e) {
    clearError.value = errorMessage(e)
  } finally {
    clearing.value = false
  }
}

watch([action, operator, dateFrom, dateTo], resetPageAndLoad)

onMounted(async () => {
  await Promise.all([loadOptions(), loadList()])
})
</script>

<template>
  <section class="view">
    <div class="page-tools">
      <span class="page-tools-note">
        <AppIcon name="receipt" />记录书目新增、修改、删除与批量导入操作，保留最近 20,000 条。
      </span>
      <span class="spacer" />
      <button v-if="isAdmin" class="btn btn-ghost btn-sm" type="button" @click="openClear">
        <AppIcon name="trash" />清理历史日志
      </button>
    </div>

    <article class="data-card">
      <div class="toolbar">
        <div class="input-icon search-field">
          <input
            v-model="q"
            class="input"
            placeholder="搜索题名、条码或变更摘要"
            autocomplete="off"
            @input="onSearchInput"
            @keydown.enter.prevent="resetPageAndLoad"
          >
          <AppIcon name="search" />
        </div>
        <select v-model="action" class="select" aria-label="操作类型">
          <option value="">全部操作</option>
          <option v-for="item in options.actions" :key="item.value" :value="item.value">{{ item.label }}</option>
        </select>
        <select v-model="operator" class="select" aria-label="操作人">
          <option value="">全部操作人</option>
          <option v-for="item in options.operators" :key="item" :value="item">{{ item }}</option>
        </select>
        <input v-model="dateFrom" type="date" class="input is-compact" aria-label="开始日期">
        <span class="faint">至</span>
        <input v-model="dateTo" type="date" class="input is-compact" aria-label="结束日期">
        <span class="toolbar-spacer" />
        <span class="toolbar-count">共 <strong>{{ formatNumber(list.total) }}</strong> 条</span>
      </div>

      <div class="table-scroll-hint">左右滑动查看完整表格与操作</div>
      <PagerBar :page="list.page" :page-size="list.pageSize" :total="list.total" noun="条日志" @update:page="setPage" />
      <div class="table-wrap">
        <table class="table is-striped">
          <thead>
            <tr>
              <ThSort label="时间" sort-key="created_at" :current="list.sortKey" :order="list.sortOrder" @sort="setSort" />
              <ThSort label="操作" sort-key="action" :current="list.sortKey" :order="list.sortOrder" @sort="setSort" />
              <ThSort label="操作人" sort-key="operator" :current="list.sortKey" :order="list.sortOrder" @sort="setSort" />
              <th>记录</th><th>变更摘要</th><th>册数</th><th>操作</th>
            </tr>
          </thead>
          <tbody>
            <TableState v-if="list.loading || list.error || !list.items.length"
                        :colspan="7" :list="list" loading-text="正在读取日志…"
                        empty="当前筛选条件下没有操作日志" :retry="loadList" />
            <template v-else>
              <tr v-for="row in list.items" :key="row.id">
                <td class="cell-date">{{ formatDateTime(row.created_at) }}</td>
                <td>
                  <span class="status-text" :class="actionTone(row.action)">
                    <AppIcon :name="actionIcon(row.action)" :size="13" />{{ row.action_label }}
                  </span>
                </td>
                <td>{{ row.operator }}</td>
                <td>
                  <div class="cell-main">{{ row.title || '—' }}</div>
                  <div class="cell-sub">{{ row.barcode || (row.record_id ? `记录 #${row.record_id}` : '—') }}</div>
                </td>
                <td class="log-note">{{ row.note || '—' }}</td>
                <td class="num">{{ row.copies }}</td>
                <td>
                  <div class="row-actions">
                    <button class="text-link" type="button" @click="detail = row">详情</button>
                    <RouterLink v-if="row.record_id" class="text-link" :to="`/admin/records/${row.record_id}`">书目</RouterLink>
                  </div>
                </td>
              </tr>
            </template>
          </tbody>
        </table>
      </div>
    </article>

    <BaseModal :open="Boolean(detail)" title="日志详情" :subtitle="detail ? `日志 #${detail.id}` : ''" size="sm" @close="detail = null">
      <template v-if="detail">
        <div class="info-row"><span>操作时间</span><strong>{{ formatDateTime(detail.created_at) }}</strong></div>
        <div class="info-row"><span>操作类型</span><strong>{{ detail.action_label }}</strong></div>
        <div class="info-row"><span>操作人</span><strong>{{ detail.operator }}</strong></div>
        <div class="info-row"><span>关联记录</span><strong>{{ detail.title || '—' }}<template v-if="detail.barcode"> · {{ detail.barcode }}</template></strong></div>
        <div class="info-row"><span>影响册数</span><strong>{{ detail.copies }}</strong></div>
        <div class="info-row" style="align-items: flex-start"><span>变更摘要</span><strong style="text-align: right; max-width: 70%">{{ detail.note || '—' }}</strong></div>
      </template>
      <template #footer>
        <button class="btn btn-primary" type="button" @click="detail = null">关闭</button>
      </template>
    </BaseModal>

    <BaseModal :open="clearOpen" title="清理历史日志" subtitle="仅管理员可执行" size="sm" @close="clearOpen = false">
      <div class="notice-strip">
        <AppIcon name="alert" />
        <span>将删除所选日期之前的全部操作日志，删除后不可恢复。书目与馆藏数据不受影响。</span>
      </div>
      <div class="field">
        <label for="clear-before">清理截止日期</label>
        <input id="clear-before" v-model="clearBefore" class="input" type="date">
        <span class="hint">该日期及之前的日志将被删除</span>
      </div>
      <p v-if="clearError" class="field-note is-error" style="margin-top: 10px" role="alert">{{ clearError }}</p>
      <template #footer>
        <button class="btn btn-ghost" type="button" :disabled="clearing" @click="clearOpen = false">取消</button>
        <button class="btn btn-danger" type="button" :disabled="clearing" @click="submitClear">
          {{ clearing ? '清理中…' : '确认清理' }}
        </button>
      </template>
    </BaseModal>
  </section>
</template>
