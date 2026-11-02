<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import AppIcon from '../components/AppIcon.vue'
import BookCover from '../components/BookCover.vue'
import { errorMessage, readerGet, readerPost } from '../lib/api'
import { daysBetween, formatDate, formatMoney, formatNumber } from '../lib/format'
import { readerAuth } from '../lib/readerAuth'
import { toast, toastError } from '../lib/toast'

interface Loan {
  id: number
  record_id: number
  barcode: string | null
  title: string | null
  author?: string | null
  loaned_at: string | null
  due_on: string | null
  returned_at: string | null
  lost_at: string | null
  renew_count: number
  status: string
  status_label: string
  overdue: boolean
  days_overdue: number
  days_overdue_at_return: number
  fine_amount: number
  fine_status: string | null
  fine_status_label: string | null
  compensation_amount: number
  compensation_status_label: string | null
}

interface Reservation {
  id: number
  record_id: number
  title: string | null
  author: string | null
  status: string
  status_label: string
  reserved_at: string | null
  ready_at: string | null
  expires_at: string | null
  queue_position: number | null
  available_count: number
  seconds_left: number | null
  cancel_reason: string | null
}

interface Rules {
  loan_days: number
  max_items: number
  max_renew: number
  renew_days: number
}

interface LoansData {
  reader: { rules: Rules; fines: { enabled: boolean; unpaid: number; unpaid_count: number }; stats: { active: number; overdue: number; total: number } }
  rules: Rules
  active: Loan[]
  history: Loan[]
  summary: { active: number; overdue: number; total: number }
}

type Tab = 'active' | 'history' | 'reservations'

const route = useRoute()
const initialTab = typeof route.query.tab === 'string' && ['history', 'reservations'].includes(route.query.tab)
  ? route.query.tab as Tab
  : 'active'
const tab = ref<Tab>(initialTab)
const loading = ref(true)
const error = ref('')
const loans = ref<LoansData | null>(null)
const reservations = ref<Reservation[]>([])
const busyId = ref<number | null>(null)
const reader = computed(() => readerAuth.reader)

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [loanData, reservationData] = await Promise.all([
      readerGet<LoansData>('/portal/loans'),
      readerGet<{ items: Reservation[] }>('/portal/reservations', { status: 'all' }),
    ])
    loans.value = loanData
    reservations.value = reservationData.items
  } catch (e) {
    error.value = errorMessage(e)
  } finally {
    loading.value = false
  }
}

const activeReservations = computed(() =>
  reservations.value.filter((item) => ['PENDING', 'READY'].includes(item.status)),
)

/**
 * 「我的预约」列表只显示**还有意义**的预约。
 *
 * 接口用 `status=all` 取全量（历史 tab 要用来着），但列表原来直接把全量铺出来：
 * 读者取消一条，卡片还留在原地只把按钮收起来 —— 看起来像没取消成功，而且
 * 一个月下来列表里全是"已取消""保留期已过"的墓碑（2026-10-26 评测 P3）。
 * 终态（已取消 / 已过期 / 已完成）不再进这个列表。
 */
const visibleReservations = computed(() =>
  reservations.value.filter((item) => !['CANCELLED', 'EXPIRED', 'FULFILLED'].includes(item.status)),
)

const remainingQuota = computed(() => {
  if (!loans.value) return 0
  return Math.max(0, loans.value.rules.max_items - loans.value.summary.active)
})

const dueSoon = computed(() =>
  (loans.value?.active || []).filter((loan) => {
    const days = daysBetween(loan.due_on)
    return days >= 0 && days <= 7
  }).length,
)

function loanProgress(loan: Loan): number {
  const rule = loans.value?.rules.loan_days || 30
  const days = daysBetween(loan.due_on)
  return Math.max(0, Math.min(100, Math.round((days / rule) * 100)))
}

function dueClass(loan: Loan): string {
  if (loan.overdue) return 'is-danger'
  if (daysBetween(loan.due_on) <= 7) return 'is-soon'
  return ''
}

function dueText(loan: Loan): string {
  if (loan.overdue) return `已逾期 ${loan.days_overdue} 天`
  const days = daysBetween(loan.due_on)
  if (days === 0) return '今天到期'
  return `还剩 ${days} 天`
}

async function renew(loan: Loan) {
  if (busyId.value) return
  busyId.value = loan.id
  try {
    const data = await readerPost<{ message: string }>(`/portal/loans/${loan.id}/renew`)
    toast(data.message)
    await load()
  } catch (e) {
    toastError(errorMessage(e))
  } finally {
    busyId.value = null
  }
}

async function cancelReservation(row: Reservation) {
  if (busyId.value) return
  busyId.value = row.id
  try {
    await readerPost(`/portal/reservations/${row.id}/cancel`)
    toast('预约已取消')
    await load()
  } catch (e) {
    toastError(errorMessage(e))
  } finally {
    busyId.value = null
  }
}

function reservationMeta(row: Reservation): string {
  if (row.status === 'READY') {
    return `请于 ${formatDate(row.expires_at)} 前到馆取书`
  }
  if (row.status === 'PENDING') {
    return `当前排队第 ${row.queue_position || 1} 位 · 到书后通知`
  }
  if (row.status === 'EXPIRED') return '保留期已过'
  if (row.status === 'CANCELLED') return row.cancel_reason || '已取消'
  if (row.status === 'FULFILLED') return '已完成借阅'
  return ''
}

onMounted(load)
</script>

<template>
  <div class="reader-view">
    <header class="reader-page-head">
      <div>
        <h1>我的借阅</h1>
        <p>{{ reader?.name }} · {{ reader?.card_no }} · 借期 {{ loans?.rules.loan_days || 30 }} 天</p>
      </div>
      <RouterLink class="btn btn-outline btn-sm" to="/reader">
        <AppIcon name="compass" />继续发现
      </RouterLink>
    </header>

    <div v-if="loading" class="reader-loading"><span class="spinner" />正在读取借阅记录…</div>
    <div v-else-if="error" class="reader-empty">
      <AppIcon name="alert" /><strong>借阅记录读取失败</strong><p>{{ error }}</p>
      <button class="btn btn-outline btn-sm" type="button" @click="load">重新加载</button>
    </div>
    <template v-else-if="loans">
      <div class="reader-metrics">
        <div class="reader-metric"><span>当前外借</span><strong>{{ formatNumber(loans.summary.active) }}<small>册</small></strong><em>上限 {{ loans.rules.max_items }} 册</em></div>
        <div class="reader-metric" :class="{ 'is-danger': loans.summary.overdue }"><span>逾期未还</span><strong>{{ formatNumber(loans.summary.overdue) }}<small>册</small></strong><em>{{ loans.summary.overdue ? '请尽快归还' : '全部按期' }}</em></div>
        <div class="reader-metric"><span>7 天内到期</span><strong>{{ formatNumber(dueSoon) }}<small>册</small></strong><em>可续借 {{ loans.rules.max_renew }} 次</em></div>
        <div class="reader-metric"><span>累计借阅</span><strong>{{ formatNumber(loans.summary.total) }}<small>册</small></strong><em>剩余额度 {{ remainingQuota }} 册</em></div>
      </div>

      <div v-if="loans.reader.fines.enabled && loans.reader.fines.unpaid_count" class="reader-note-card" style="margin-bottom: 14px">
        <strong>未缴逾期罚款 {{ formatMoney(loans.reader.fines.unpaid) }}</strong> · 请到图书馆服务台办理。
      </div>

      <div class="reader-tabs" role="tablist">
        <button class="reader-tab" type="button" :class="{ 'is-active': tab === 'active' }" @click="tab = 'active'">外借中 <span class="count">{{ loans.active.length }}</span></button>
        <button class="reader-tab" type="button" :class="{ 'is-active': tab === 'history' }" @click="tab = 'history'">借阅历史 <span class="count">{{ loans.history.length }}</span></button>
        <button class="reader-tab" type="button" :class="{ 'is-active': tab === 'reservations' }" @click="tab = 'reservations'">我的预约 <span class="count">{{ activeReservations.length }}</span></button>
      </div>

      <template v-if="tab === 'active'">
        <div v-if="loans.active.length" class="reader-loan-list">
          <div v-for="loan in loans.active" :key="loan.id" class="reader-loan-card">
            <BookCover :title="loan.title || '未知题名'" />
            <div class="reader-loan-main">
              <h3>{{ loan.title || '未知题名' }}</h3>
              <p>{{ loan.barcode }} · 外借 {{ formatDate(loan.loaned_at) }} · 续借 {{ loan.renew_count }} 次</p>
              <div class="reader-progress" :class="dueClass(loan)"><span :style="{ width: `${loanProgress(loan)}%` }" /></div>
            </div>
            <div class="reader-due">
              <strong :class="dueClass(loan)">{{ loan.due_on }}</strong>
              <span>{{ dueText(loan) }}</span>
            </div>
            <div class="reader-loan-actions">
              <button class="btn btn-outline btn-sm" type="button" :disabled="busyId === loan.id || loan.overdue || loan.renew_count >= loans.rules.max_renew" @click="renew(loan)">
                <AppIcon name="refresh" />{{ busyId === loan.id ? '续借中…' : '续借' }}
              </button>
            </div>
          </div>
        </div>
        <div v-else class="reader-empty">
          <AppIcon name="books" />
          <strong>当前没有外借图书</strong>
          <p>可在发现页查找想读的图书。</p>
          <RouterLink class="btn btn-primary btn-sm" to="/reader">浏览馆藏</RouterLink>
        </div>
        <div class="reader-note-card" style="margin-top: 14px">
          <strong>借阅规则</strong> · 借期 {{ loans.rules.loan_days }} 天，最多 {{ loans.rules.max_items }} 册；在未逾期时可以续借 {{ loans.rules.max_renew }} 次，每次 {{ loans.rules.renew_days }} 天。
        </div>
      </template>

      <template v-else-if="tab === 'history'">
        <div v-if="loans.history.length" class="reader-panel" style="padding: 6px 18px">
          <div v-for="loan in loans.history" :key="loan.id" class="reader-history-row">
            <BookCover :title="loan.title || '未知题名'" />
            <div class="reader-history-info">
              <strong>{{ loan.title || '未知题名' }}</strong>
              <span>外借 {{ formatDate(loan.loaned_at) }} · 归还 {{ formatDate(loan.returned_at || loan.lost_at) || '—' }}</span>
            </div>
            <div class="reader-history-status">
              {{ loan.status_label }}
              <span v-if="loan.fine_amount > 0" class="reader-history-status" style="color: var(--danger)"> · 罚款 {{ formatMoney(loan.fine_amount) }}</span>
            </div>
          </div>
        </div>
        <div v-else class="reader-empty"><AppIcon name="receipt" /><strong>当前没有借阅历史</strong><p>结束的借阅会保留在这里。</p></div>
      </template>

      <template v-else>
        <div v-if="visibleReservations.length" class="reader-loan-list">
          <div v-for="row in visibleReservations" :key="row.id" class="reader-reservation-card">
            <BookCover :title="row.title || '未知题名'" />
            <div class="reader-reservation-info">
              <h3>{{ row.title || '未知题名' }}</h3>
              <p>{{ row.author || '佚名' }} · 预约于 {{ formatDate(row.reserved_at) }}</p>
            </div>
            <div class="reader-reservation-state">
              <strong>{{ row.status_label }}</strong>
              <span>{{ reservationMeta(row) }}</span>
            </div>
            <button
              v-if="['PENDING', 'READY'].includes(row.status)"
              class="btn btn-ghost btn-sm"
              type="button"
              :disabled="busyId === row.id"
              @click="cancelReservation(row)"
            >取消</button>
          </div>
        </div>
        <div v-else class="reader-empty"><AppIcon name="bookmark" /><strong>当前没有预约</strong><p>馆藏全部外借时，可在图书详情中预约。</p></div>
      </template>
    </template>
  </div>
</template>
