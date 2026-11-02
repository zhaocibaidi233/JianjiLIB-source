<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import AppIcon from '../../components/AppIcon.vue'
import BaseModal from '../../components/BaseModal.vue'
import { can } from '../../lib/auth'
import { errorMessage, get, post } from '../../lib/api'
import { formatDate, formatDateTime, formatNumber } from '../../lib/format'
import { toast } from '../../lib/toast'
import { libraryState, loadLibraries } from '../../stores/libraries'

interface InvStats {
  expected: number
  found: number
  missing: number
  on_loan: number
  unknown: number
  other_library: number
  withdrawn: number
  on_loan_scanned: number
  duplicate: number
  problems: number
  scans: number
  progress: number
}

interface InvSession {
  id: number
  library_id: number
  name: string
  status: 'OPEN' | 'CLOSED'
  status_label: string
  expected_count: number
  started_at: string | null
  started_by: string | null
  closed_at: string | null
  closed_by: string | null
  note: string | null
  stats: InvStats
}

const router = useRouter()
const canWrite = computed(() => can('circulation', 'write'))
const libraryId = computed(() => libraryState.currentId)
const sessions = ref<InvSession[]>([])
const loading = ref(true)
const error = ref('')

const createOpen = ref(false)
const createForm = reactive({ name: '', note: '' })
const creating = ref(false)
const createError = ref('')

const openSession = computed(() => sessions.value.find((item) => item.status === 'OPEN') || null)
const closedCount = computed(() => sessions.value.filter((item) => item.status === 'CLOSED').length)
const lastClosed = computed(() => sessions.value.find((item) => item.status === 'CLOSED') || null)

async function load() {
  loading.value = true
  error.value = ''
  try {
    const data = await get<{ items: InvSession[] }>('/inventory/sessions', {
      library_id: libraryId.value || undefined,
      limit: 100,
    })
    sessions.value = data.items
  } catch (e) {
    error.value = errorMessage(e)
    sessions.value = []
  } finally {
    loading.value = false
  }
}

function openCreate() {
  createForm.name = ''
  createForm.note = ''
  createError.value = ''
  createOpen.value = true
}

async function createSession() {
  if (creating.value) return
  creating.value = true
  createError.value = ''
  try {
    const data = await post<{ session: InvSession; message: string }>('/inventory/sessions', {
      library_id: libraryId.value || undefined,
      name: createForm.name.trim() || undefined,
      note: createForm.note.trim() || undefined,
    })
    toast(data.message)
    createOpen.value = false
    await router.push(`/admin/inventory/${data.session.id}`)
  } catch (e) {
    createError.value = errorMessage(e)
  } finally {
    creating.value = false
  }
}

watch(libraryId, () => void load())
onMounted(async () => {
  await loadLibraries()
  await load()
})
</script>

<template>
  <section class="view">
    <div class="metric-grid">
      <div class="metric-card">
        <span class="metric-icon"><AppIcon name="list" /></span>
        <div class="metric-body">
          <div class="metric-label">进行中盘点</div>
          <div class="metric-value">{{ openSession ? 1 : 0 }}<small>次</small></div>
          <div class="metric-meta">{{ openSession ? openSession.name : '当前没有进行中的盘点' }}</div>
        </div>
      </div>
      <div class="metric-card">
        <span class="metric-icon blue"><AppIcon name="check-circle" /></span>
        <div class="metric-body">
          <div class="metric-label">已完成盘点</div>
          <div class="metric-value">{{ formatNumber(closedCount) }}<small>次</small></div>
          <div class="metric-meta">历史记录保留扫描快照</div>
        </div>
      </div>
      <div class="metric-card">
        <span class="metric-icon amber"><AppIcon name="layers" /></span>
        <div class="metric-body">
          <div class="metric-label">最近一次应盘</div>
          <div class="metric-value">{{ formatNumber(lastClosed?.stats.expected) }}<small>册</small></div>
          <div class="metric-meta">{{ lastClosed ? formatDate(lastClosed.closed_at) : '暂无记录' }}</div>
        </div>
      </div>
      <div class="metric-card">
        <span class="metric-icon rose"><AppIcon name="alert" /></span>
        <div class="metric-body">
          <div class="metric-label">最近一次漏盘</div>
          <div class="metric-value">{{ formatNumber(lastClosed?.stats.missing) }}<small>册</small></div>
          <div class="metric-meta">结束盘点不自动注销馆藏</div>
        </div>
      </div>
    </div>

    <div class="page-tools">
      <span class="page-tools-note">
        <AppIcon name="info" />盘点仅生成清单，不修改馆藏状态。漏盘图书需人工核对后再处理。
      </span>
      <span class="spacer" />
      <button v-if="canWrite && !openSession" class="btn btn-primary btn-sm" type="button" @click="openCreate">
        <AppIcon name="plus" />新建盘点
      </button>
      <RouterLink v-else-if="openSession" class="btn btn-primary btn-sm" :to="`/admin/inventory/${openSession.id}`">
        <AppIcon name="scan" />继续盘点
      </RouterLink>
    </div>

    <article class="data-card">
      <div class="table-scroll-hint">左右滑动查看完整表格与操作</div>
      <div class="table-wrap">
        <table class="table is-striped">
          <thead>
            <tr>
              <th>盘点名称</th>
              <th>状态</th>
              <th>应盘</th>
              <th>找到</th>
              <th>漏盘</th>
              <th>外借未归</th>
              <th class="col-hide-md">开始时间</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="loading">
              <td colspan="8" class="table-state"><span class="spinner" />正在读取盘点记录…</td>
            </tr>
            <tr v-else-if="error">
              <td colspan="8" class="table-state">
                <AppIcon name="alert" />{{ error }}
                <div style="margin-top: 10px"><button class="btn btn-outline btn-sm" type="button" @click="load">重新加载</button></div>
              </td>
            </tr>
            <tr v-else-if="!sessions.length">
              <td colspan="8" class="table-state">当前没有盘点记录。新建盘点后逐册扫描即可开始对账。</td>
            </tr>
            <template v-else>
              <tr v-for="session in sessions" :key="session.id">
                <td>
                  <RouterLink class="cell-main" :to="`/admin/inventory/${session.id}`">{{ session.name }}</RouterLink>
                  <div class="cell-sub">{{ session.note || (session.started_by || '馆员') + ' 发起' }}</div>
                </td>
                <td>
                  <span class="status-text" :class="session.status === 'OPEN' ? 'warn' : 'ok'">
                    <span class="dot" />{{ session.status_label }}
                  </span>
                </td>
                <td class="num">{{ formatNumber(session.stats.expected) }}</td>
                <td class="num">{{ formatNumber(session.stats.found) }}</td>
                <td class="num">{{ formatNumber(session.stats.missing) }}</td>
                <td class="num">{{ formatNumber(session.stats.on_loan) }}</td>
                <td class="col-hide-md cell-date">{{ formatDateTime(session.started_at) }}</td>
                <td>
                  <div class="row-actions">
                    <RouterLink class="text-link" :to="`/admin/inventory/${session.id}`">
                      {{ session.status === 'OPEN' ? '继续盘点' : '查看结果' }}
                    </RouterLink>
                  </div>
                </td>
              </tr>
            </template>
          </tbody>
        </table>
      </div>
    </article>

    <BaseModal :open="createOpen" title="新建盘点" subtitle="盘点范围为当前图书库的全部在架馆藏" size="sm" @close="createOpen = false">
      <div class="notice-strip">
        <AppIcon name="info" />
        <span>盘点开始时会记录应盘册数。盘点期间新编目的馆藏不计入本次范围。</span>
      </div>
      <div class="field">
        <label for="inv-name">盘点名称</label>
        <input id="inv-name" v-model="createForm.name" class="input" maxlength="128" placeholder="留空按日期自动命名">
      </div>
      <div class="field" style="margin-top: 12px">
        <label for="inv-note">备注</label>
        <textarea id="inv-note" v-model="createForm.note" class="textarea" rows="3" maxlength="256" placeholder="如：2026 秋季书库盘点" />
      </div>
      <p v-if="createError" class="field-note is-error" style="margin-top: 10px" role="alert">{{ createError }}</p>
      <template #footer>
        <button class="btn btn-ghost" type="button" :disabled="creating" @click="createOpen = false">取消</button>
        <button class="btn btn-primary" type="button" :disabled="creating" @click="createSession">
          {{ creating ? '创建中…' : '开始盘点' }}
        </button>
      </template>
    </BaseModal>
  </section>
</template>
