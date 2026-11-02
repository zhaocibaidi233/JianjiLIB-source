<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import AppIcon from '../../components/AppIcon.vue'
import BaseModal from '../../components/BaseModal.vue'
import BookCover from '../../components/BookCover.vue'
import ConfirmDialog from '../../components/ConfirmDialog.vue'
import ReaderFormModal from '../components/ReaderFormModal.vue'
import { can } from '../../lib/auth'
import { del, errorMessage, get, post, put } from '../../lib/api'
import { avatarClass, formatDate, formatDateTime, formatMoney } from '../../lib/format'
import { readerStatusLabel } from '../../lib/displayLabels'
import type { ReaderRecord } from '../../lib/readerTypes'
import { toast, toastError } from '../../lib/toast'

interface Loan {
  id: number
  record_id: number
  barcode: string | null
  title: string | null
  reader_id: number
  loaned_at: string | null
  due_on: string | null
  returned_at: string | null
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
  compensation_status: string | null
  compensation_status_label: string | null
  lost_at: string | null
  lost_note: string | null
}

interface ReaderDetail extends ReaderRecord {
  rules: { max_items: number; loan_days: number; max_renew: number }
  stats: { total_loans: number; active: number; overdue: number }
  active_loans: Loan[]
  history: Loan[]
  fines: { enabled: boolean; unpaid: number; unpaid_count: number; overdue_estimate: number }
  compensations: { lost_count: number; unpaid: number; unpaid_count: number }
  guardian_consent_at: string | null
  guardian_consent_by: string | null
  anonymized_at: string | null
}

interface PortalAccount {
  username: string
  enabled: boolean
  last_login_at: string | null
}

type DetailTab = 'loans' | 'history' | 'money'

const route = useRoute()
const readerId = computed(() => Number(route.params.id))
const canWrite = computed(() => can('readers', 'write'))

const reader = ref<ReaderDetail | null>(null)
const loading = ref(true)
const error = ref('')
const tab = ref<DetailTab>(route.query.tab === 'money' ? 'money' : route.query.tab === 'history' ? 'history' : 'loans')
const formOpen = ref(false)
const actionBusy = ref(false)

const account = ref<PortalAccount | null>(null)
const accountLoading = ref(false)
const accountModalOpen = ref(false)
const accountPassword = ref('')
const accountPassword2 = ref('')
const accountError = ref('')
const accountBusy = ref(false)
const accountDisableOpen = ref(false)

const statusTarget = ref('')
const statusBusy = ref(false)
const privacyTarget = ref<'' | 'consent' | 'revoke' | 'anonymize'>('')
const privacyBusy = ref(false)
const returnTarget = ref<Loan | null>(null)
const moneyTarget = ref<{ loan: Loan; kind: 'fine' | 'compensation'; status: 'PAID' | 'WAIVED' } | null>(null)

const fineRows = computed(() => reader.value?.history.filter((loan) => loan.fine_amount > 0) || [])
const compensationRows = computed(() => reader.value?.history.filter((loan) => loan.compensation_amount > 0) || [])

async function load() {
  if (!Number.isFinite(readerId.value)) {
    error.value = '读者编号无效'
    loading.value = false
    return
  }
  loading.value = true
  error.value = ''
  try {
    reader.value = await get<ReaderDetail>(`/readers/${readerId.value}`)
    await loadAccount()
  } catch (e) {
    reader.value = null
    error.value = errorMessage(e)
  } finally {
    loading.value = false
  }
}

async function loadAccount() {
  accountLoading.value = true
  try {
    const data = await get<{ account: PortalAccount | null }>(
      `/readers/${readerId.value}/account`,
    )
    account.value = data.account
  } catch {
    account.value = null
  } finally {
    accountLoading.value = false
  }
}

function openAccountModal() {
  accountPassword.value = ''
  accountPassword2.value = ''
  accountError.value = ''
  accountModalOpen.value = true
}

async function submitAccount() {
  if (!reader.value) return
  if (accountPassword.value.length < 6) {
    accountError.value = '密码至少 6 位'
    return
  }
  if (accountPassword.value !== accountPassword2.value) {
    accountError.value = '两次输入的密码不一致'
    return
  }
  accountBusy.value = true
  accountError.value = ''
  try {
    const data = await post<{ account: PortalAccount; message: string }>(
      `/readers/${reader.value.id}/account`,
      { password: accountPassword.value },
    )
    account.value = data.account
    accountModalOpen.value = false
    toast(data.message || '读者账号已保存')
  } catch (e) {
    accountError.value = errorMessage(e)
  } finally {
    accountBusy.value = false
  }
}

async function disableAccount() {
  if (!reader.value) return
  accountBusy.value = true
  try {
    const data = await del<{ message: string }>(`/readers/${reader.value.id}/account`)
    toast(data.message || '读者账号已停用')
    accountDisableOpen.value = false
    await loadAccount()
  } catch (e) {
    toastError(errorMessage(e))
  } finally {
    accountBusy.value = false
  }
}

async function onSaved() {
  await load()
}

function askStatus(status: string) {
  statusTarget.value = status
}

async function submitStatus() {
  if (!reader.value || !statusTarget.value) return
  statusBusy.value = true
  try {
    await put(`/readers/${reader.value.id}`, { status: statusTarget.value })
    toast('读者状态已更新')
    statusTarget.value = ''
    await load()
  } catch (e) {
    toastError(errorMessage(e))
  } finally {
    statusBusy.value = false
  }
}

async function submitPrivacy() {
  const target = privacyTarget.value
  if (!reader.value || !target) return
  privacyBusy.value = true
  try {
    if (target === 'anonymize') {
      const data = await post<{ card_no: string; loans_anonymized: number }>(
        `/readers/${reader.value.id}/anonymize`, {})
      toast(`已匿名化（${data.card_no}），同步匿名借阅记录 ${data.loans_anonymized} 条`)
    } else {
      await post(`/readers/${reader.value.id}/consent`, { consent: target === 'consent' })
      toast(target === 'consent' ? '已记录监护人同意' : '已撤销监护人同意记录')
    }
    privacyTarget.value = ''
    await load()
  } catch (e) {
    toastError(errorMessage(e))
  } finally {
    privacyBusy.value = false
  }
}

async function renew(loan: Loan) {
  if (actionBusy.value) return
  actionBusy.value = true
  try {
    const result = await post<{ message: string }>('/circulation/renew', { loan_id: loan.id })
    toast(result.message)
    await load()
  } catch (e) {
    toastError(errorMessage(e))
  } finally {
    actionBusy.value = false
  }
}

async function doReturn() {
  const loan = returnTarget.value
  returnTarget.value = null
  if (!loan?.barcode || !reader.value) return
  actionBusy.value = true
  try {
    const result = await post<{ message: string }>('/circulation/return', {
      library_id: reader.value.library_id,
      barcode: loan.barcode,
    })
    toast(result.message)
    await load()
  } catch (e) {
    toastError(errorMessage(e))
  } finally {
    actionBusy.value = false
  }
}

async function settleMoney() {
  const target = moneyTarget.value
  if (!target) return
  actionBusy.value = true
  try {
    const url =
      target.kind === 'fine'
        ? `/circulation/fines/${target.loan.id}/settle`
        : `/circulation/loans/${target.loan.id}/compensation/settle`
    const result = await post<{ message: string }>(url, { status: target.status })
    toast(result.message)
    moneyTarget.value = null
    await load()
  } catch (e) {
    toastError(errorMessage(e))
  } finally {
    actionBusy.value = false
  }
}

const statusLabel = computed(() => readerStatusLabel(reader.value?.status))

watch(
  () => route.params.id,
  () => void load(),
)

onMounted(load)
</script>

<template>
  <section class="view">
    <div class="page-tools">
      <RouterLink class="text-link" to="/admin/readers">
        <AppIcon name="arrow-left" />读者列表
      </RouterLink>
      <span class="spacer" />
      <template v-if="reader && canWrite">
        <button class="btn btn-outline btn-sm" type="button" @click="formOpen = true">
          <AppIcon name="edit" />编辑资料
        </button>
        <button
          v-if="reader.status === 'ACTIVE'"
          class="btn btn-outline btn-sm"
          type="button"
          @click="askStatus('SUSPENDED')"
        >暂停借阅</button>
        <button
          v-else-if="reader.status === 'SUSPENDED'"
          class="btn btn-outline btn-sm"
          type="button"
          @click="askStatus('ACTIVE')"
        >恢复借阅</button>
        <button
          v-if="reader.status !== 'INACTIVE'"
          class="btn btn-ghost btn-sm"
          type="button"
          @click="askStatus('INACTIVE')"
        >注销读者</button>
      </template>
    </div>

    <div v-if="loading" class="card" aria-busy="true">
      <div class="skeleton" style="height: 120px; margin: 16px" />
      <div class="skeleton" style="height: 260px; margin: 16px" />
    </div>

    <div v-else-if="error" class="card dash-error">
      <span class="error-icon"><AppIcon name="alert" /></span>
      <strong>无法读取读者资料</strong>
      <p>{{ error }}</p>
      <button class="btn btn-primary btn-sm" type="button" @click="load">重新加载</button>
    </div>

    <template v-else-if="reader">
      <header class="reader-card">
        <span class="avatar" :class="avatarClass(reader.id)">{{ reader.name.slice(-2) }}</span>
        <div class="reader-card-main">
          <h2>{{ reader.name }}</h2>
          <div class="rc-sub">{{ reader.card_no }} · {{ reader.reader_type_label }}<template v-if="reader.class_name"> · {{ reader.class_name }}</template><template v-else-if="reader.grade"> · {{ reader.grade }}</template></div>
          <div class="reader-card-tags">
            <span class="badge" :class="reader.status === 'ACTIVE' ? 'badge-ok' : reader.status === 'SUSPENDED' ? 'badge-warn' : 'badge-neutral'">
              {{ reader.status_label }}
            </span>
            <span v-if="reader.stats.overdue" class="badge badge-danger no-dot">逾期 {{ reader.stats.overdue }} 册</span>
            <span v-if="reader.fines.enabled && reader.fines.unpaid_count" class="badge badge-warn no-dot">未缴罚款 {{ formatMoney(reader.fines.unpaid) }}</span>
            <span v-if="reader.compensations.unpaid_count" class="badge badge-warn no-dot">待收赔偿 {{ formatMoney(reader.compensations.unpaid) }}</span>
          </div>
        </div>
        <div class="reader-card-actions">
          <RouterLink class="btn btn-outline btn-sm" :to="{ path: '/admin/circulation', query: { mode: 'borrow', reader: reader.card_no } }">
            <AppIcon name="swap" />借还台
          </RouterLink>
          <RouterLink class="btn btn-soft btn-sm" :to="{ path: '/admin/reservations', query: { q: reader.card_no } }">
            <AppIcon name="bookmark" />预约记录
          </RouterLink>
        </div>
      </header>

      <div class="profile-layout">
        <div class="profile-main">
          <article class="data-card">
            <div class="tabs card-tabs" role="tablist">
              <button class="tab" type="button" :class="{ 'is-active': tab === 'loans' }" @click="tab = 'loans'">
                外借中 <span class="count">{{ reader.stats.active }}</span>
              </button>
              <button class="tab" type="button" :class="{ 'is-active': tab === 'history' }" @click="tab = 'history'">
                借阅历史 <span class="count">{{ reader.history.length }}</span>
              </button>
              <button class="tab" type="button" :class="{ 'is-active': tab === 'money' }" @click="tab = 'money'">
                罚款与赔偿
              </button>
            </div>

            <div v-if="tab === 'loans'" style="padding: 18px 20px 20px">
              <div v-if="reader.active_loans.length" class="loan-card-list">
                <div v-for="loan in reader.active_loans" :key="loan.id" class="loan-card">
                  <BookCover :title="loan.title || '未知题名'" />
                  <div class="loan-card-info">
                    <RouterLink class="cell-main" :to="`/admin/records/${loan.record_id}`">{{ loan.title || '未知题名' }}</RouterLink>
                    <span>{{ loan.barcode }} · 外借 {{ formatDate(loan.loaned_at) }} · 续借 {{ loan.renew_count }} 次</span>
                  </div>
                  <div class="loan-card-meta">
                    <strong :class="{ 'is-danger': loan.overdue }">{{ loan.due_on }}</strong>
                    <span>{{ loan.overdue ? `逾期 ${loan.days_overdue} 天` : '应还日期' }}</span>
                  </div>
                  <div v-if="canWrite" class="row-actions">
                    <button
                      class="text-link"
                      type="button"
                      :disabled="actionBusy || loan.overdue"
                      @click="renew(loan)"
                    >续借</button>
                    <button class="text-link" type="button" :disabled="actionBusy" @click="returnTarget = loan">归还</button>
                  </div>
                </div>
              </div>
              <div v-else class="empty-card">
                <AppIcon name="books" />
                当前没有外借图书
              </div>
            </div>

            <div v-else-if="tab === 'history'" class="table-wrap">
              <table class="table">
                <thead>
                  <tr>
                    <th>图书</th>
                    <th>外借</th>
                    <th>归还</th>
                    <th>结果</th>
                    <th v-if="reader.fines.enabled">罚款</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-if="!reader.history.length">
                    <td :colspan="reader.fines.enabled ? 5 : 4" class="table-state">当前没有已结束的借阅</td>
                  </tr>
                  <template v-else>
                    <tr v-for="loan in reader.history" :key="loan.id">
                    <td>
                      <div class="record-book-cell">
                        <BookCover :title="loan.title || '未知题名'" />
                        <div class="record-book-text">
                          <div class="cell-main">{{ loan.title || '未知题名' }}</div>
                          <div class="cell-sub">{{ loan.barcode }}</div>
                        </div>
                      </div>
                    </td>
                    <td class="cell-date">{{ formatDate(loan.loaned_at) }}</td>
                    <td class="cell-date">{{ formatDate(loan.returned_at || loan.lost_at) || '—' }}</td>
                    <td>
                      <span class="status-text" :class="loan.status === 'LOST' ? 'danger' : 'ok'">
                        <span class="dot" />{{ loan.status_label }}
                      </span>
                      <div v-if="loan.days_overdue_at_return" class="cell-sub">归还时逾期 {{ loan.days_overdue_at_return }} 天</div>
                    </td>
                    <td v-if="reader.fines.enabled" class="cell-date">
                      <template v-if="loan.fine_amount > 0">
                        {{ formatMoney(loan.fine_amount) }}
                        <div class="cell-sub">{{ loan.fine_status_label || '未缴' }}</div>
                      </template>
                      <template v-else>—</template>
                    </td>
                  </tr>
                  </template>
                </tbody>
              </table>
            </div>

            <div v-else style="padding: 18px 20px 20px">
              <template v-if="reader.fines.enabled">
                <div class="drawer-section-title">逾期罚款</div>
                <div v-if="fineRows.length" class="table-wrap" style="margin-bottom: 22px">
                  <table class="table">
                    <thead><tr><th>图书</th><th>罚款</th><th>状态</th><th>操作</th></tr></thead>
                    <tbody>
                      <tr v-for="loan in fineRows" :key="`fine-${loan.id}`">
                        <td>
                          <div class="cell-main">{{ loan.title }}</div>
                          <div class="cell-sub">{{ loan.barcode }} · {{ formatDate(loan.returned_at) }}</div>
                        </td>
                        <td>{{ formatMoney(loan.fine_amount) }}</td>
                        <td><span class="status-text" :class="loan.fine_status === 'UNPAID' ? 'danger' : loan.fine_status === 'PAID' ? 'ok' : 'muted'"><span class="dot" />{{ loan.fine_status_label || '未缴' }}</span></td>
                        <td>
                          <div v-if="canWrite && loan.fine_status === 'UNPAID'" class="row-actions">
                            <button class="text-link" type="button" :disabled="actionBusy" @click="moneyTarget = { loan, kind: 'fine', status: 'PAID' }">收款</button>
                            <button class="text-link" type="button" :disabled="actionBusy" @click="moneyTarget = { loan, kind: 'fine', status: 'WAIVED' }">免除</button>
                          </div>
                        </td>
                      </tr>
                    </tbody>
                  </table>
                </div>
                <div v-else class="empty-card" style="margin-bottom: 22px">当前没有逾期罚款记录</div>
              </template>

              <div class="drawer-section-title">遗失赔偿</div>
              <div v-if="compensationRows.length" class="table-wrap">
                <table class="table">
                  <thead><tr><th>图书</th><th>赔偿</th><th>状态</th><th>操作</th></tr></thead>
                  <tbody>
                    <tr v-for="loan in compensationRows" :key="`comp-${loan.id}`">
                      <td>
                        <div class="cell-main">{{ loan.title }}</div>
                        <div class="cell-sub">{{ loan.barcode }} · {{ formatDate(loan.lost_at) }} 登记遗失</div>
                      </td>
                      <td>{{ formatMoney(loan.compensation_amount) }}</td>
                      <td><span class="status-text" :class="loan.compensation_status === 'UNPAID' ? 'danger' : 'ok'"><span class="dot" />{{ loan.compensation_status_label || '未缴' }}</span></td>
                      <td>
                        <div v-if="canWrite && loan.compensation_status === 'UNPAID'" class="row-actions">
                          <button class="text-link" type="button" :disabled="actionBusy" @click="moneyTarget = { loan, kind: 'compensation', status: 'PAID' }">收款</button>
                          <button class="text-link" type="button" :disabled="actionBusy" @click="moneyTarget = { loan, kind: 'compensation', status: 'WAIVED' }">免除</button>
                        </div>
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <div v-else class="empty-card">当前没有遗失赔偿记录</div>
            </div>
          </article>
        </div>

        <aside class="profile-side">
          <article class="card">
            <div class="card-head">
              <div>
                <div class="card-title">借阅概况</div>
                <div class="card-sub">按读者类型适用的规则</div>
              </div>
            </div>
            <div class="card-body">
              <div class="profile-stat-grid">
                <div class="profile-stat"><strong>{{ reader.stats.active }}</strong><span>当前外借</span></div>
                <div class="profile-stat" :class="{ 'is-danger': reader.stats.overdue }"><strong>{{ reader.stats.overdue }}</strong><span>逾期</span></div>
                <div class="profile-stat"><strong>{{ reader.stats.total_loans }}</strong><span>累计借阅</span></div>
              </div>
              <div class="info-line" style="margin-top: 14px"><span>借期</span><strong>{{ reader.rules.loan_days }} 天</strong></div>
              <div class="info-line"><span>借阅上限</span><strong>{{ reader.rules.max_items }} 册</strong></div>
              <div class="info-line"><span>续借次数</span><strong>{{ reader.rules.max_renew }} 次</strong></div>
              <div class="info-line"><span>逾期罚款</span><strong>{{ reader.fines.enabled ? formatMoney(reader.fines.unpaid) : '未启用' }}</strong></div>
              <div class="info-line"><span>未缴赔偿</span><strong>{{ formatMoney(reader.compensations.unpaid) }}</strong></div>
            </div>
          </article>

          <article class="card">
            <div class="card-head">
              <div>
                <div class="card-title">联系与档案</div>
              </div>
            </div>
            <div class="card-body">
              <div class="info-line"><span>借书证号</span><strong>{{ reader.card_no }}</strong></div>
              <div class="info-line"><span>联系电话</span><strong>{{ reader.phone || '—' }}</strong></div>
              <div class="info-line"><span>年级</span><strong>{{ reader.grade || '—' }}</strong></div>
              <div class="info-line"><span>班级 / 部门</span><strong>{{ reader.class_name || '—' }}</strong></div>
              <div class="info-line"><span>注册时间</span><strong>{{ formatDate(reader.created_at) || '—' }}</strong></div>
              <div class="info-line"><span>档案状态</span><strong>{{ statusLabel }}</strong></div>
              <div v-if="reader.note" class="profile-note" style="margin-top: 12px">{{ reader.note }}</div>
            </div>
          </article>

          <article class="card">
            <div class="card-head">
              <div>
                <div class="card-title">隐私与监护人同意</div>
              </div>
            </div>
            <div class="card-body">
              <div class="info-line">
                <span>监护人同意</span>
                <strong>{{ reader.guardian_consent_at ? '已记录' : '未记录' }}</strong>
              </div>
              <div v-if="reader.guardian_consent_at" class="info-line">
                <span>记录时间</span><strong>{{ formatDateTime(reader.guardian_consent_at) }}</strong>
              </div>
              <div v-if="reader.guardian_consent_by" class="info-line">
                <span>记录人</span><strong>{{ reader.guardian_consent_by }}</strong>
              </div>
              <div v-if="reader.anonymized_at" class="info-line">
                <span>匿名化时间</span><strong>{{ formatDateTime(reader.anonymized_at) }}</strong>
              </div>
              <p class="field-note">
                不满 14 周岁读者处理个人信息应取得监护人同意；系统只记录同意时间与记录人，不推断年龄。
              </p>
              <div v-if="canWrite && !reader.anonymized_at" class="row-actions" style="margin-top: 10px">
                <button
                  v-if="!reader.guardian_consent_at"
                  class="btn btn-outline btn-sm"
                  type="button"
                  @click="privacyTarget = 'consent'"
                >记录监护人同意</button>
                <button
                  v-else
                  class="btn btn-ghost btn-sm"
                  type="button"
                  @click="privacyTarget = 'revoke'"
                >撤销同意记录</button>
                <button
                  v-if="reader.status === 'INACTIVE'"
                  class="btn btn-ghost btn-sm"
                  type="button"
                  @click="privacyTarget = 'anonymize'"
                >匿名化档案</button>
              </div>
              <p v-else-if="canWrite && reader.status !== 'INACTIVE'" class="field-note">
                读者注销且无未还借阅、活动预约后，可在此匿名化档案。
              </p>
            </div>
          </article>

          <article class="card">
            <div class="card-head">
              <div>
                <div class="card-title">读者门户</div>
                <div class="card-sub">馆藏检索、借阅与座位预约</div>
              </div>
              <span v-if="account" class="badge" :class="account.enabled ? 'badge-ok' : 'badge-neutral'">
                {{ account.enabled ? '已开通' : '已停用' }}
              </span>
              <span v-else class="badge badge-neutral">未开通</span>
            </div>
            <div class="card-body">
              <div v-if="accountLoading" class="skeleton" style="height: 52px" />
              <template v-else-if="account">
                <div class="info-line"><span>登录名</span><strong>{{ account.username }}</strong></div>
                <div class="info-line"><span>最近登录</span><strong>{{ formatDateTime(account.last_login_at) || '—' }}</strong></div>
                <div class="info-line"><span>账号状态</span><strong>{{ account.enabled ? '启用' : '已停用' }}</strong></div>
                <div v-if="canWrite" class="row-actions" style="margin-top: 12px">
                  <button class="btn btn-outline btn-sm" type="button" @click="openAccountModal">
                    {{ account.enabled ? '重置密码' : '重新开通' }}
                  </button>
                  <button v-if="account.enabled" class="btn btn-ghost btn-sm" type="button" @click="accountDisableOpen = true">停用</button>
                </div>
              </template>
              <template v-else>
                <p class="field-note">未开通门户账号。开通后读者以借书证号登录。</p>
                <button v-if="canWrite" class="btn btn-primary btn-sm btn-block" type="button" style="margin-top: 10px" @click="openAccountModal">
                  <AppIcon name="key" />开通账号
                </button>
              </template>
            </div>
          </article>
        </aside>
      </div>

      <ReaderFormModal
        :open="formOpen"
        :reader="reader"
        :library-id="reader.library_id"
        @close="formOpen = false"
        @saved="onSaved"
      />

      <ConfirmDialog
        :open="Boolean(statusTarget)"
        :title="statusTarget === 'ACTIVE' ? '恢复读者' : statusTarget === 'SUSPENDED' ? '暂停借阅' : '注销读者'"
        :message="statusTarget === 'ACTIVE'
          ? '确认恢复该读者的借阅资格？'
          : statusTarget === 'SUSPENDED'
            ? '确认暂停该读者的借阅资格？其进行中的预约将同时取消，仍可归还图书。'
            : '确认注销该读者？档案与借阅历史保留。'"
        :confirm-text="statusTarget === 'ACTIVE' ? '恢复' : statusTarget === 'SUSPENDED' ? '暂停借阅' : '注销'"
        :danger="statusTarget !== 'ACTIVE'"
        :busy="statusBusy"
        @cancel="statusTarget = ''"
        @confirm="submitStatus"
      />

      <ConfirmDialog
        :open="Boolean(returnTarget)"
        title="确认归还"
        :message="returnTarget ? `确认归还《${returnTarget.title || returnTarget.barcode || '未知题名'}》？\n条码：${returnTarget.barcode || '—'}` : ''"
        confirm-text="确认归还"
        :busy="actionBusy"
        @cancel="returnTarget = null"
        @confirm="doReturn"
      />

      <ConfirmDialog
        :open="Boolean(moneyTarget)"
        :title="moneyTarget?.kind === 'fine' ? '罚款处理' : '赔偿处理'"
        :message="moneyTarget
          ? `${moneyTarget.loan.title || '未知题名'}\n金额 ${formatMoney(moneyTarget.kind === 'fine' ? moneyTarget.loan.fine_amount : moneyTarget.loan.compensation_amount)}，确认${moneyTarget.status === 'PAID' ? '收款' : '免除'}？`
          : ''"
        :confirm-text="moneyTarget?.status === 'PAID' ? '确认收款' : '确认免除'"
        :busy="actionBusy"
        @cancel="moneyTarget = null"
        @confirm="settleMoney"
      />

      <BaseModal
        :open="accountModalOpen"
        :title="account ? '重置读者密码' : '开通读者账号'"
        :subtitle="reader ? `登录名：${reader.card_no.toLowerCase()}` : ''"
        size="sm"
        @close="accountModalOpen = false"
      >
        <div class="auth-form" style="gap: 14px">
          <div class="field">
            <label for="portal-password">新密码</label>
            <input id="portal-password" v-model="accountPassword" class="input" type="password" autocomplete="new-password" placeholder="至少 6 位" @input="accountError = ''" />
          </div>
          <div class="field">
            <label for="portal-password2">确认新密码</label>
            <input id="portal-password2" v-model="accountPassword2" class="input" type="password" autocomplete="new-password" placeholder="再输入一次" @input="accountError = ''" />
          </div>
          <p v-if="accountError" class="field-note is-error">{{ accountError }}</p>
          <p v-else class="field-note">{{ account ? '保存后该读者已登录的会话全部失效。' : '读者使用借书证号与初始密码登录读者门户。' }}</p>
        </div>
        <template #footer>
          <button class="btn btn-ghost" type="button" @click="accountModalOpen = false">取消</button>
          <button class="btn btn-primary" type="button" :disabled="accountBusy" @click="submitAccount">
            {{ accountBusy ? '保存中…' : '保存' }}
          </button>
        </template>
      </BaseModal>

      <ConfirmDialog
        :open="Boolean(privacyTarget)"
        :title="privacyTarget === 'anonymize' ? '匿名化读者档案'
          : privacyTarget === 'revoke' ? '撤销监护人同意记录' : '记录监护人同意'"
        :message="privacyTarget === 'anonymize'
          ? '匿名化不可逆：姓名、电话、班级将清空，门户账号停用，借阅台账中的姓名同步改为「匿名读者」。\n确认继续？'
          : privacyTarget === 'revoke'
            ? '确认撤销该读者的监护人同意记录？撤销后同意时间与记录人将被清空。'
            : '请确认已取得监护人的有效同意。系统会记录操作人与时间，作为处理不满 14 周岁读者信息的依据。'"
        :confirm-text="privacyTarget === 'anonymize' ? '匿名化' : privacyTarget === 'revoke' ? '撤销' : '记录同意'"
        :danger="privacyTarget !== 'consent'"
        :busy="privacyBusy"
        @cancel="privacyTarget = ''"
        @confirm="submitPrivacy"
      />

      <ConfirmDialog
        :open="accountDisableOpen"
        title="停用读者账号"
        message="停用后该读者无法登录读者门户，已登录的会话立即失效。读者档案与借阅记录不受影响。"
        confirm-text="停用账号"
        danger
        :busy="accountBusy"
        @cancel="accountDisableOpen = false"
        @confirm="disableAccount"
      />
    </template>
  </section>
</template>
