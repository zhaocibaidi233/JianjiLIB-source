<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import AppIcon from '../../components/AppIcon.vue'
import BaseModal from '../../components/BaseModal.vue'
import PagerBar from '../../components/PagerBar.vue'
import { can } from '../../lib/auth'
import { errorMessage, get, post } from '../../lib/api'
import { formatDate, formatNumber } from '../../lib/format'
import { toast, toastError } from '../../lib/toast'
import { libraryState, loadLibraries } from '../../stores/libraries'

interface AcqRow {
  id: number
  library_id: number
  title: string
  author: string | null
  isbn: string | null
  publisher: string | null
  source_type: string
  source_type_label: string
  source_name: string | null
  contact: string | null
  quantity: number
  note: string | null
  stage: string
  stage_label: string
  source_checked: boolean
  source_checked_at: string | null
  source_checked_by: string | null
  source_check_note: string | null
  review_note: string | null
  reviewed_by: string | null
  reviewed_at: string | null
  approval_ref: string | null
  created_by: string | null
  created_at: string | null
}

interface AcqList {
  library: { id: number; name: string }
  total: number
  page: number
  page_size: number
  counts: Record<string, number>
  stage_labels: Record<string, string>
  source_labels: Record<string, string>
  items: AcqRow[]
}

const PAGE_SIZE = 30
const STAGES = ['PROPOSED', 'REVIEWED', 'APPROVED', 'FILED', 'REJECTED'] as const
const SOURCE_TYPES = ['READER', 'TEACHER', 'DONATION', 'PURCHASE'] as const

const canWrite = computed(() => can('circulation', 'write'))
const libraryId = computed(() => libraryState.currentId)
const stage = ref<'all' | string>('all')
const sourceType = ref('')
const q = ref('')
const page = ref(1)
const data = ref<AcqList | null>(null)
const loading = ref(true)
const error = ref('')
const busy = ref(false)

const createOpen = ref(false)
const createError = ref('')
const form = reactive({
  title: '', author: '', isbn: '', publisher: '',
  source_type: 'READER', source_name: '', contact: '',
  quantity: 1, note: '',
})

const actionTarget = ref<{ row: AcqRow; kind: 'REVIEWED' | 'APPROVED' | 'FILED' | 'REJECTED' } | null>(null)
const actionNote = ref('')
const actionRef = ref('')

const checkTarget = ref<AcqRow | null>(null)
const checkNote = ref('')

const counts = computed(() => data.value?.counts || {})
const stageLabels = computed(() => data.value?.stage_labels || {})
const sourceLabels = computed(() => data.value?.source_labels || {})

function stageTone(value: string): string {
  if (value === 'APPROVED' || value === 'FILED') return 'badge-ok'
  if (value === 'REJECTED') return 'badge-neutral'
  if (value === 'REVIEWED') return 'badge-warn'
  return 'badge-neutral'
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    data.value = await get<AcqList>('/acquisitions', {
      library_id: libraryId.value || undefined,
      stage: stage.value === 'all' ? undefined : stage.value,
      source_type: sourceType.value || undefined,
      q: q.value.trim() || undefined,
      page: page.value,
      page_size: PAGE_SIZE,
    })
  } catch (e) {
    error.value = errorMessage(e)
    data.value = null
  } finally {
    loading.value = false
  }
}

function setStage(next: string) {
  if (stage.value === next) return
  stage.value = next
  page.value = 1
  void load()
}

function search() {
  page.value = 1
  void load()
}

function onPage(next: number) {
  page.value = next
  void load()
}

function openCreate() {
  Object.assign(form, {
    title: '', author: '', isbn: '', publisher: '',
    source_type: 'READER', source_name: '', contact: '',
    quantity: 1, note: '',
  })
  createError.value = ''
  createOpen.value = true
}

async function submitCreate() {
  if (!form.title.trim()) {
    createError.value = '题名不能为空'
    return
  }
  if (form.source_type === 'DONATION' && !form.source_name.trim()) {
    createError.value = '捐赠必须填写捐赠方'
    return
  }
  busy.value = true
  createError.value = ''
  try {
    await post('/acquisitions', {
      library_id: libraryId.value || undefined,
      title: form.title.trim(),
      author: form.author.trim() || undefined,
      isbn: form.isbn.trim() || undefined,
      publisher: form.publisher.trim() || undefined,
      source_type: form.source_type,
      source_name: form.source_name.trim() || undefined,
      contact: form.contact.trim() || undefined,
      quantity: form.quantity,
      note: form.note.trim() || undefined,
    })
    toast('已登记')
    createOpen.value = false
    page.value = 1
    await load()
  } catch (e) {
    createError.value = errorMessage(e)
  } finally {
    busy.value = false
  }
}

function startAction(row: AcqRow, kind: 'REVIEWED' | 'APPROVED' | 'FILED' | 'REJECTED') {
  actionTarget.value = { row, kind }
  actionNote.value = ''
  actionRef.value = ''
}

async function submitAction() {
  const target = actionTarget.value
  if (!target) return
  if (target.kind === 'REJECTED' && !actionNote.value.trim()) {
    toastError('不予采纳必须填写审议意见')
    return
  }
  if (target.kind === 'FILED' && !actionRef.value.trim()) {
    toastError('备案必须填写审批/备案文号')
    return
  }
  busy.value = true
  try {
    await post(`/acquisitions/${target.row.id}/stage`, {
      stage: target.kind,
      note: actionNote.value.trim() || undefined,
      approval_ref: actionRef.value.trim() || undefined,
    })
    toast('阶段已更新')
    actionTarget.value = null
    await load()
  } catch (e) {
    toastError(errorMessage(e))
  } finally {
    busy.value = false
  }
}

function startCheck(row: AcqRow) {
  checkTarget.value = row
  checkNote.value = row.source_check_note || ''
}

async function submitCheck() {
  const row = checkTarget.value
  if (!row) return
  busy.value = true
  try {
    await post(`/acquisitions/${row.id}/source-check`, {
      checked: true, note: checkNote.value.trim() || undefined,
    })
    toast('来源审核已记录')
    checkTarget.value = null
    await load()
  } catch (e) {
    toastError(errorMessage(e))
  } finally {
    busy.value = false
  }
}

watch(libraryId, () => {
  page.value = 1
  void load()
})

onMounted(async () => {
  await loadLibraries()
  await load()
})
</script>

<template>
  <section class="view">
    <div class="metric-grid">
      <div v-for="key in ['PROPOSED', 'REVIEWED', 'APPROVED', 'FILED']" :key="key" class="metric-card">
        <span class="metric-icon" :class="key === 'FILED' ? 'neutral' : key === 'APPROVED' ? 'blue' : key === 'REVIEWED' ? 'amber' : ''">
          <AppIcon :name="key === 'FILED' ? 'archive' : key === 'APPROVED' ? 'check' : key === 'REVIEWED' ? 'filter' : 'bookmark'" />
        </span>
        <div class="metric-body">
          <div class="metric-label">{{ stageLabels[key] || key }}</div>
          <div class="metric-value">{{ formatNumber(counts[key] || 0) }}<small>条</small></div>
          <div class="metric-meta">
            {{ key === 'PROPOSED' ? '登记待审议' : key === 'REVIEWED' ? '审议待确认' : key === 'APPROVED' ? '确认待备案' : '已完成流程' }}
          </div>
        </div>
      </div>
    </div>

    <article class="data-card">
      <div class="toolbar">
        <div class="input-icon search-field">
          <input v-model="q" class="input" placeholder="搜索题名、责任者、ISBN 或来源" aria-label="搜索" @keydown.enter.prevent="search">
          <AppIcon name="search" />
        </div>
        <select v-model="sourceType" class="select" aria-label="来源" @change="search">
          <option value="">全部来源</option>
          <option v-for="key in SOURCE_TYPES" :key="key" :value="key">{{ sourceLabels[key] || key }}</option>
        </select>
        <button class="btn btn-primary btn-sm" type="button" @click="search">查询</button>
        <span class="toolbar-spacer" />
        <button v-if="canWrite" class="btn btn-primary btn-sm" type="button" @click="openCreate">
          <AppIcon name="plus" />登记荐购 / 受赠
        </button>
      </div>

      <div class="tabs card-tabs" role="tablist">
        <button class="tab" type="button" :class="{ 'is-active': stage === 'all' }" @click="setStage('all')">
          全部 <span class="count">{{ formatNumber(data?.total || 0) }}</span>
        </button>
        <button v-for="key in STAGES" :key="key" class="tab" type="button"
          :class="{ 'is-active': stage === key }" @click="setStage(key)">
          {{ stageLabels[key] || key }} <span class="count">{{ counts[key] || 0 }}</span>
        </button>
      </div>

      <div v-if="error" class="dash-banner" style="margin: 14px 18px" role="alert">
        <AppIcon name="alert" />
        <span>{{ error }}</span>
        <button type="button" @click="load">重试</button>
      </div>

      <PagerBar v-if="data?.total" :page="page" :page-size="PAGE_SIZE" :total="data.total" noun="条" @update:page="onPage" />
      <div class="table-wrap">
        <table class="table">
          <thead>
            <tr>
              <th>题名 / 责任者</th>
              <th>来源</th>
              <th class="num">册数</th>
              <th>阶段</th>
              <th>审核 / 备案</th>
              <th>登记</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="loading"><td colspan="7" class="table-state"><span class="spinner" />正在读取…</td></tr>
            <tr v-else-if="!data?.items.length"><td colspan="7" class="table-state">当前条件下没有荐购/受赠记录</td></tr>
            <template v-else>
              <tr v-for="row in data.items" :key="row.id">
                <td>
                  <div class="cell-main">{{ row.title }}</div>
                  <div class="cell-sub">{{ row.author || '—' }}<template v-if="row.isbn"> · {{ row.isbn }}</template></div>
                  <div v-if="row.note" class="cell-sub">{{ row.note }}</div>
                </td>
                <td>
                  <div class="cell-main" style="font-size: 12.5px">{{ row.source_type_label }}</div>
                  <div class="cell-sub">{{ row.source_name || '—' }}</div>
                </td>
                <td class="num">{{ row.quantity }}</td>
                <td>
                  <span class="badge no-dot" :class="stageTone(row.stage)">{{ row.stage_label }}</span>
                  <div v-if="row.review_note" class="cell-sub" style="margin-top: 4px">{{ row.review_note }}</div>
                </td>
                <td>
                  <template v-if="row.source_type === 'DONATION'">
                    <span class="status-text" :class="row.source_checked ? 'ok' : 'danger'">
                      <span class="dot" />{{ row.source_checked ? '来源已审核' : '来源未审核' }}
                    </span>
                    <div v-if="row.source_checked_at" class="cell-sub">{{ formatDate(row.source_checked_at) }} · {{ row.source_checked_by || '—' }}</div>
                  </template>
                  <div v-else class="cell-sub">非捐赠</div>
                  <div v-if="row.approval_ref" class="cell-sub">文号：{{ row.approval_ref }}</div>
                  <div v-if="row.reviewed_at" class="cell-sub">{{ formatDate(row.reviewed_at) }} · {{ row.reviewed_by || '—' }}</div>
                </td>
                <td>
                  <div class="cell-sub">{{ formatDate(row.created_at) || '—' }}</div>
                  <div class="cell-sub">{{ row.created_by || '—' }}</div>
                </td>
                <td>
                  <div v-if="canWrite" class="row-actions">
                    <button v-if="row.source_type === 'DONATION' && !row.source_checked && row.stage !== 'FILED' && row.stage !== 'REJECTED'"
                      class="text-link" type="button" :disabled="busy" @click="startCheck(row)">来源审核</button>
                    <button v-if="row.stage === 'PROPOSED'" class="text-link" type="button" :disabled="busy"
                      @click="startAction(row, 'REVIEWED')">送审</button>
                    <button v-if="row.stage === 'REVIEWED'" class="text-link" type="button" :disabled="busy"
                      @click="startAction(row, 'APPROVED')">确认</button>
                    <button v-if="row.stage === 'APPROVED'" class="text-link" type="button" :disabled="busy"
                      @click="startAction(row, 'FILED')">备案</button>
                    <button v-if="row.stage === 'PROPOSED' || row.stage === 'REVIEWED'" class="text-link danger"
                      type="button" :disabled="busy" @click="startAction(row, 'REJECTED')">不予采纳</button>
                  </div>
                  <span v-else class="cell-sub">只读</span>
                </td>
              </tr>
            </template>
          </tbody>
        </table>
      </div>

      <p class="field-note" style="margin: 12px 18px 16px">
        荐购/受赠登记不是采购单，也不产生书目或馆藏。捐赠须先通过来源审核才能确认；不得冒用官方推荐或指定名义。
      </p>
    </article>

    <BaseModal :open="createOpen" title="登记荐购 / 受赠" subtitle="来源与流程记录会进入台账" @close="createOpen = false">
      <div class="auth-form" style="gap: 14px">
        <div class="field">
          <label for="acq-title">题名</label>
          <input id="acq-title" v-model="form.title" class="input" placeholder="必填" @input="createError = ''">
        </div>
        <div class="form-grid">
          <div class="field">
            <label for="acq-author">责任者</label>
            <input id="acq-author" v-model="form.author" class="input">
          </div>
          <div class="field">
            <label for="acq-isbn">ISBN</label>
            <input id="acq-isbn" v-model="form.isbn" class="input">
          </div>
        </div>
        <div class="form-grid">
          <div class="field">
            <label for="acq-publisher">出版者</label>
            <input id="acq-publisher" v-model="form.publisher" class="input">
          </div>
          <div class="field">
            <label for="acq-quantity">册数</label>
            <input id="acq-quantity" v-model.number="form.quantity" class="input" type="number" min="1" max="999">
          </div>
        </div>
        <div class="form-grid">
          <div class="field">
            <label for="acq-source">来源</label>
            <select id="acq-source" v-model="form.source_type" class="select">
              <option v-for="key in SOURCE_TYPES" :key="key" :value="key">{{ sourceLabels[key] || key }}</option>
            </select>
          </div>
          <div class="field">
            <label for="acq-source-name">{{ form.source_type === 'DONATION' ? '捐赠方' : '推荐人 / 来源' }}</label>
            <input id="acq-source-name" v-model="form.source_name" class="input" :placeholder="form.source_type === 'DONATION' ? '必填' : '选填'">
          </div>
        </div>
        <div class="field">
          <label for="acq-note">说明</label>
          <textarea id="acq-note" v-model="form.note" class="input" rows="3" />
        </div>
        <p v-if="createError" class="field-note is-error">{{ createError }}</p>
      </div>
      <template #footer>
        <button class="btn btn-ghost" type="button" @click="createOpen = false">取消</button>
        <button class="btn btn-primary" type="button" :disabled="busy" @click="submitCreate">
          {{ busy ? '保存中…' : '登记' }}
        </button>
      </template>
    </BaseModal>

    <BaseModal
      :open="Boolean(actionTarget)"
      :title="actionTarget?.kind === 'REJECTED' ? '不予采纳' : actionTarget?.kind === 'FILED' ? '备案' : actionTarget?.kind === 'APPROVED' ? '确认荐购' : '送交审议'"
      :subtitle="actionTarget?.row.title || ''"
      size="sm"
      @close="actionTarget = null"
    >
      <div class="auth-form" style="gap: 14px">
        <div v-if="actionTarget?.kind === 'FILED'" class="field">
          <label for="acq-ref">审批 / 备案文号</label>
          <input id="acq-ref" v-model="actionRef" class="input" placeholder="例：校图〔2026〕3号">
        </div>
        <div class="field">
          <label for="acq-action-note">{{ actionTarget?.kind === 'REJECTED' ? '审议意见' : '备注' }}</label>
          <textarea id="acq-action-note" v-model="actionNote" class="input" rows="3"
            :placeholder="actionTarget?.kind === 'REJECTED' ? '必填' : '选填'" />
        </div>
      </div>
      <template #footer>
        <button class="btn btn-ghost" type="button" @click="actionTarget = null">取消</button>
        <button class="btn btn-primary" type="button" :disabled="busy" @click="submitAction">
          {{ busy ? '处理中…' : '确认' }}
        </button>
      </template>
    </BaseModal>

    <BaseModal
      :open="Boolean(checkTarget)"
      title="捐赠来源审核"
      :subtitle="checkTarget ? `${checkTarget.title} · ${checkTarget.source_name || '未填捐赠方'}` : ''"
      size="sm"
      @close="checkTarget = null"
    >
      <div class="auth-form" style="gap: 14px">
        <p class="field-note">确认来源正规、未发现负面清单问题；审核通过后该捐赠才能进入确认/备案。</p>
        <div class="field">
          <label for="acq-check-note">审核说明</label>
          <textarea id="acq-check-note" v-model="checkNote" class="input" rows="3" placeholder="选填：来源渠道、审核依据" />
        </div>
      </div>
      <template #footer>
        <button class="btn btn-ghost" type="button" @click="checkTarget = null">取消</button>
        <button class="btn btn-primary" type="button" :disabled="busy" @click="submitCheck">
          {{ busy ? '保存中…' : '确认审核通过' }}
        </button>
      </template>
    </BaseModal>
  </section>
</template>

<style scoped>
.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
@media (max-width: 640px) { .form-grid { grid-template-columns: 1fr; } }
.table th.num, .table td.num { text-align: right; }
</style>
