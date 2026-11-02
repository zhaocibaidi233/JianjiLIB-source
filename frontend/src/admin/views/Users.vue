<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import AppIcon from '../../components/AppIcon.vue'
import BaseModal from '../../components/BaseModal.vue'
import ConfirmDialog from '../../components/ConfirmDialog.vue'
import { auth, refreshMe } from '../../lib/auth'
import { del, errorMessage, get, post, put } from '../../lib/api'
import { avatarClass, formatDateTime, formatNumber } from '../../lib/format'
import { toast, toastError } from '../../lib/toast'

interface UserRow {
  id: number
  username: string
  display_name: string
  enabled: boolean
  role_id: number
  role_name: string | null
  role_title: string | null
  is_admin: boolean
  levels: Record<string, string>
  last_login_at: string | null
  created_at: string | null
}
interface RoleRow {
  id: number
  name: string
  title: string
  permissions: Record<string, string>
  levels: Record<string, string>
  builtin: boolean
  note: string | null
  user_count: number
}
interface ModuleItem {
  key: string
  title: string
  group: string
}
interface RegistrationRow {
  id: number
  username: string
  note: string | null
  status: string
  reject_reason: string | null
  reviewed_by: string | null
  reviewed_at: string | null
  created_at: string | null
}
interface AgentToken {
  id: number
  name: string
  prefix: string
  role_id: number
  role_name: string | null
  role_title: string | null
  created_by: string | null
  note: string | null
  created_at: string | null
  last_used_at: string | null
  revoked_at: string | null
  active: boolean
  token?: string
}
interface AuditRow {
  id: number
  token_name: string
  method: string
  path: string
  status: number
  duration_ms: number
  created_at: string | null
}

type Tab = 'accounts' | 'roles' | 'registrations' | 'agents'
type PermLevel = 'none' | 'read' | 'write'

const TABS: Array<{ key: Tab; label: string; icon: string }> = [
  { key: 'accounts', label: '账号', icon: 'users' },
  { key: 'roles', label: '角色', icon: 'shield' },
  { key: 'registrations', label: '注册申请', icon: 'inbox' },
  { key: 'agents', label: '接口凭据', icon: 'key' },
]
// 接口凭据 / 调用记录的接口全部是 `require_admin`。非管理员原本也能看到这个 tab，
// 点进去是一屏 403 —— 直接不给入口，比让人逐个按钮撞墙好。
const visibleTabs = computed(() =>
  TABS.filter((item) => item.key !== 'agents' || Boolean(auth.user?.is_admin)))

const tab = ref<Tab>('accounts')
const users = ref<UserRow[]>([])
const roles = ref<RoleRow[]>([])
const modules = ref<ModuleItem[]>([])
const registrations = ref<RegistrationRow[]>([])
const regStatus = ref<'pending' | 'all'>('pending')
const pendingCount = ref(0)
const allowRegister = ref(false)
const agents = ref<AgentToken[]>([])
const audit = ref<AuditRow[]>([])
const auditTotal = ref(0)
const loading = reactive({ users: true, roles: true, registrations: true, agents: true, audit: false })
const saving = ref(false)

const activeAgentCount = computed(() => agents.value.filter((row) => row.active).length)
const enabledUserCount = computed(() => users.value.filter((row) => row.enabled).length)
const adminCount = computed(() => users.value.filter((row) => row.is_admin).length)

function roleLevelCount(role: RoleRow, level: 'write' | 'read'): number {
  return Object.values(role.levels).filter((value) => value === level).length
}
const modulesByGroup = computed(() => {
  const groups: Array<{ name: string; items: ModuleItem[] }> = []
  for (const item of modules.value) {
    const group = groups.find((entry) => entry.name === item.group)
    if (group) group.items.push(item)
    else groups.push({ name: item.group, items: [item] })
  }
  return groups
})

const REG_LABELS: Record<string, string> = {
  pending: '待审核',
  approved: '已批准',
  rejected: '已驳回',
}

/* ---------------- 加载 ---------------- */
async function loadUsers() {
  loading.users = true
  try {
    const data = await get<{ items: UserRow[] }>('/users')
    users.value = data.items
  } catch (e) {
    toastError(errorMessage(e))
  } finally {
    loading.users = false
  }
}

async function loadRoles() {
  loading.roles = true
  try {
    const data = await get<{ items: RoleRow[] }>('/roles')
    roles.value = data.items
  } catch (e) {
    toastError(errorMessage(e))
  } finally {
    loading.roles = false
  }
}

async function loadModules() {
  try {
    const data = await get<{ modules: ModuleItem[] }>('/auth/modules')
    modules.value = data.modules
  } catch (e) {
    toastError(errorMessage(e))
  }
}

async function loadRegistrations() {
  loading.registrations = true
  try {
    const data = await get<{ items: RegistrationRow[]; pending: number }>(
      '/users/registrations', { status: regStatus.value },
    )
    registrations.value = data.items
    pendingCount.value = data.pending
  } catch (e) {
    toastError(errorMessage(e))
  } finally {
    loading.registrations = false
  }
}

async function loadRegisterSetting() {
  try {
    const data = await get<{ allow_register: boolean }>('/users/registrations/settings')
    allowRegister.value = data.allow_register
  } catch {
    allowRegister.value = false
  }
}

async function loadAgents() {
  loading.agents = true
  try {
    const data = await get<{ items: AgentToken[] }>('/agent-tokens')
    agents.value = data.items
  } catch (e) {
    toastError(errorMessage(e))
  } finally {
    loading.agents = false
  }
}

async function loadAudit() {
  loading.audit = true
  try {
    const data = await get<{ total: number; items: AuditRow[] }>('/agent-audit', { limit: 20 })
    audit.value = data.items
    auditTotal.value = data.total
  } catch (e) {
    toastError(errorMessage(e))
  } finally {
    loading.audit = false
  }
}

async function chooseTab(key: Tab) {
  tab.value = key
  if (key === 'registrations') {
    void loadRegistrations()
    void loadRegisterSetting()
  } else if (key === 'agents') {
    void loadAgents()
    void loadAudit()
  } else if (key === 'roles') {
    void loadRoles()
  } else if (key === 'accounts') {
    void loadUsers()
  }
}

/* ---------------- 账号 ---------------- */
const userModalOpen = ref(false)
const editingUser = ref<UserRow | null>(null)
const userForm = reactive({
  username: '', displayName: '', password: '', roleId: null as number | null, enabled: true,
})
const userDeleteTarget = ref<UserRow | null>(null)
const isSelf = computed(() => editingUser.value?.id === auth.user?.id)

function openUserModal(row: UserRow | null) {
  editingUser.value = row
  userForm.username = row?.username || ''
  userForm.displayName = row?.display_name || ''
  userForm.password = ''
  userForm.roleId = row?.role_id || roles.value[0]?.id || null
  userForm.enabled = row?.enabled ?? true
  userModalOpen.value = true
}

async function saveUser() {
  if (saving.value) return
  if (!userForm.username.trim() && !editingUser.value) {
    toastError('登录名不能为空')
    return
  }
  if (!editingUser.value && userForm.password.length < 6) {
    toastError('密码至少 6 位')
    return
  }
  if (editingUser.value && userForm.password && userForm.password.length < 6) {
    toastError('新密码至少 6 位')
    return
  }
  if (!userForm.roleId) {
    toastError('请选择角色')
    return
  }
  saving.value = true
  try {
    if (editingUser.value) {
      const body: Record<string, unknown> = {
        // ⚠️ 这里**不能**写 `.trim() || null`：后端把 `null` 当作"这项不改"，
        // 于是"把已有的显示名称清空"就永远不生效（用户 2026-10-24 报的就是这个）。
        // 传空串才会走后端 `'' or username` 的回退，变回显示登录名。
        display_name: userForm.displayName.trim(),
        role_id: userForm.roleId,
        enabled: userForm.enabled,
      }
      if (userForm.password) body.password = userForm.password
      const editingSelf = editingUser.value.id === auth.user?.id
      await put(`/users/${editingUser.value.id}`, body)
      // 改的是自己 → 侧栏底部/头像菜单上的显示名与角色要立刻变。
      // 只重拉用户列表是不够的：那些地方读的是登录态里的 `auth.user`。
      if (editingSelf) await refreshMe()
      toast('账号已保存')
    } else {
      await post('/users', {
        username: userForm.username.trim(),
        // 同上传空串：新建时后端也会回退成登录名，两边口径一致
        display_name: userForm.displayName.trim(),
        password: userForm.password,
        role_id: userForm.roleId,
        enabled: userForm.enabled,
      })
      toast('账号已创建')
    }
    userModalOpen.value = false
    await Promise.all([loadUsers(), loadRoles()])
  } catch (e) {
    toastError(errorMessage(e))
  } finally {
    saving.value = false
  }
}

async function kickUser(row: UserRow) {
  try {
    const data = await post<{ removed: number }>(`/users/${row.id}/kick`)
    toast(`已清除 ${data.removed} 个登录会话`)
    await loadUsers()
  } catch (e) {
    toastError(errorMessage(e))
  }
}

async function deleteUser() {
  const target = userDeleteTarget.value
  if (!target) return
  saving.value = true
  try {
    await del(`/users/${target.id}`)
    toast('账号已删除')
    userDeleteTarget.value = null
    await Promise.all([loadUsers(), loadRoles()])
  } catch (e) {
    toastError(errorMessage(e))
  } finally {
    saving.value = false
  }
}

/* ---------------- 角色 ---------------- */
const roleModalOpen = ref(false)
const editingRole = ref<RoleRow | null>(null)
const roleForm = reactive({ name: '', title: '', note: '' })
const roleLevels = reactive<Record<string, PermLevel>>({})
const roleDeleteTarget = ref<RoleRow | null>(null)
const isAdminRole = computed(() => editingRole.value?.name === 'admin')

function initLevels(role: RoleRow | null) {
  for (const key of Object.keys(roleLevels)) delete roleLevels[key]
  for (const module of modules.value) {
    const level = role?.levels?.[module.key]
    roleLevels[module.key] = level === 'write' ? 'write' : level === 'read' ? 'read' : 'none'
  }
}

function openRoleModal(role: RoleRow | null) {
  editingRole.value = role
  roleForm.name = role?.name || ''
  roleForm.title = role?.title || ''
  roleForm.note = role?.note || ''
  initLevels(role)
  roleModalOpen.value = true
}

function setLevel(key: string, level: PermLevel) {
  if (isAdminRole.value) return
  roleLevels[key] = level
}

function countLevel(level: PermLevel): number {
  return Object.values(roleLevels).filter((value) => value === level).length
}

async function saveRole() {
  if (saving.value) return
  if (!roleForm.title.trim()) {
    toastError('角色名称不能为空')
    return
  }
  if (!editingRole.value && !roleForm.name.trim()) {
    toastError('角色标识不能为空')
    return
  }
  saving.value = true
  try {
    const permissions: Record<string, string> = {}
    if (!isAdminRole.value) {
      for (const [key, level] of Object.entries(roleLevels)) {
        if (level !== 'none') permissions[key] = level
      }
    }
    if (editingRole.value) {
      const body: Record<string, unknown> = {
        title: roleForm.title.trim(),
        note: roleForm.note.trim() || null,
      }
      if (!isAdminRole.value) body.permissions = permissions
      await put(`/roles/${editingRole.value.id}`, body)
      toast('角色已保存')
    } else {
      await post('/roles', {
        name: roleForm.name.trim(),
        title: roleForm.title.trim(),
        permissions,
        note: roleForm.note.trim() || null,
      })
      toast('角色已创建')
    }
    roleModalOpen.value = false
    await Promise.all([loadRoles(), loadUsers()])
  } catch (e) {
    toastError(errorMessage(e))
  } finally {
    saving.value = false
  }
}

async function deleteRole() {
  const target = roleDeleteTarget.value
  if (!target) return
  saving.value = true
  try {
    await del(`/roles/${target.id}`)
    toast('角色已删除')
    roleDeleteTarget.value = null
    await loadRoles()
  } catch (e) {
    toastError(errorMessage(e))
  } finally {
    saving.value = false
  }
}

/* ---------------- 注册申请 ---------------- */
const approveTarget = ref<RegistrationRow | null>(null)
const approveForm = reactive({ roleId: null as number | null, displayName: '' })
const rejectTarget = ref<RegistrationRow | null>(null)
const rejectReason = ref('')

function onRegisterToggle(event: Event) {
  void toggleRegister((event.target as HTMLInputElement).checked)
}

async function toggleRegister(value: boolean) {
  try {
    const data = await put<{ allow_register: boolean; message: string }>(
      '/users/registrations/settings', { allow_register: value },
    )
    allowRegister.value = data.allow_register
    toast(data.message)
  } catch (e) {
    toastError(errorMessage(e))
  }
}

function openApprove(row: RegistrationRow) {
  approveTarget.value = row
  approveForm.roleId = roles.value.find((role) => role.name !== 'admin')?.id || roles.value[0]?.id || null
  approveForm.displayName = row.username
}

async function submitApprove() {
  if (!approveTarget.value || !approveForm.roleId) return
  saving.value = true
  try {
    const data = await post<{ message: string }>(
      `/users/registrations/${approveTarget.value.id}/approve`,
      { role_id: approveForm.roleId, display_name: approveForm.displayName.trim() || null },
    )
    toast(data.message)
    approveTarget.value = null
    await Promise.all([loadRegistrations(), loadUsers(), loadRoles()])
  } catch (e) {
    toastError(errorMessage(e))
  } finally {
    saving.value = false
  }
}

function openReject(row: RegistrationRow) {
  rejectTarget.value = row
  rejectReason.value = ''
}

async function submitReject() {
  if (!rejectTarget.value) return
  saving.value = true
  try {
    await post(`/users/registrations/${rejectTarget.value.id}/reject`, {
      reason: rejectReason.value.trim() || null,
    })
    toast('申请已驳回')
    rejectTarget.value = null
    await loadRegistrations()
  } catch (e) {
    toastError(errorMessage(e))
  } finally {
    saving.value = false
  }
}

/* ---------------- 接口凭据 ---------------- */
const agentModalOpen = ref(false)
const agentForm = reactive({ name: '', roleId: null as number | null, note: '' })
const agentDeleteTarget = ref<AgentToken | null>(null)
const auditClearOpen = ref(false)
const newSecret = ref('')
const secretModalOpen = ref(false)

function openAgentModal() {
  agentForm.name = ''
  agentForm.roleId = roles.value.find((role) => role.name !== 'admin')?.id || roles.value[0]?.id || null
  agentForm.note = ''
  agentModalOpen.value = true
}

async function createAgent() {
  if (!agentForm.name.trim() || !agentForm.roleId) {
    toastError('请填写凭据名称并选择角色')
    return
  }
  saving.value = true
  try {
    const data = await post<AgentToken>('/agent-tokens', {
      name: agentForm.name.trim(),
      role_id: agentForm.roleId,
      note: agentForm.note.trim() || null,
    })
    newSecret.value = data.token || ''
    agentModalOpen.value = false
    secretModalOpen.value = true
    await loadAgents()
  } catch (e) {
    toastError(errorMessage(e))
  } finally {
    saving.value = false
  }
}

async function copySecret() {
  try {
    await navigator.clipboard.writeText(newSecret.value)
    toast('凭据已复制到剪贴板')
  } catch {
    toastError('浏览器未允许剪贴板访问，请手动复制')
  }
}

/**
 * 清空调用记录（`DELETE /agent-audit`，不给 token_id 就是全清）。
 *
 * 后端一直有这条接口，前端只有"刷新"没有"清空" —— 审计表虽有 `AUDIT_KEEP`
 * 上限兜底，但"换人接手时想从干净台账开始"这件事没法做。
 * 凭据本身不受影响：清的只是历史调用记录。
 */
async function clearAudit() {
  saving.value = true
  try {
    const r = await del<{ deleted: number }>('/agent-audit')
    toast(`已清空 ${formatNumber(r.deleted)} 条调用记录`)
    auditClearOpen.value = false
    await loadAudit()
  } catch (e) {
    toastError(errorMessage(e))
  } finally {
    saving.value = false
  }
}

async function revokeAgent() {
  const target = agentDeleteTarget.value
  if (!target) return
  saving.value = true
  try {
    await del(`/agent-tokens/${target.id}`)
    toast('凭据已吊销，立即生效')
    agentDeleteTarget.value = null
    await loadAgents()
  } catch (e) {
    toastError(errorMessage(e))
  } finally {
    saving.value = false
  }
}

onMounted(() => {
  void loadModules()
  void loadUsers()
  void loadRoles()
  void loadRegistrations()
  void loadRegisterSetting()
  void loadAgents()
})
</script>

<template>
  <section class="view users-view">
    <div class="users-toolbar">
      <div class="users-tabs" role="tablist" aria-label="用户与权限">
        <button
          v-for="item in visibleTabs"
          :key="item.key"
          class="users-tab"
          :class="{ 'is-active': tab === item.key }"
          type="button"
          role="tab"
          :aria-selected="tab === item.key"
          @click="chooseTab(item.key)"
        >
          <AppIcon :name="item.icon" :size="16" />
          {{ item.label }}
          <span v-if="item.key === 'accounts'" class="count">{{ users.length }}</span>
          <span v-else-if="item.key === 'roles'" class="count">{{ roles.length }}</span>
          <span v-else-if="item.key === 'registrations' && pendingCount" class="count is-alert">{{ pendingCount }}</span>
          <span v-else-if="item.key === 'agents'" class="count">{{ activeAgentCount }}</span>
        </button>
      </div>
      <div class="users-toolbar-actions">
        <button v-if="tab === 'accounts'" class="btn btn-primary btn-sm" type="button" @click="openUserModal(null)">
          <AppIcon name="user-plus" />新增账号
        </button>
        <button v-else-if="tab === 'roles'" class="btn btn-primary btn-sm" type="button" @click="openRoleModal(null)">
          <AppIcon name="plus" />新增角色
        </button>
        <button v-else-if="tab === 'agents'" class="btn btn-primary btn-sm" type="button" @click="openAgentModal">
          <AppIcon name="key" />新建凭据
        </button>
        <button v-else class="btn btn-outline btn-sm" type="button" :disabled="loading.registrations" @click="loadRegistrations">
          <AppIcon name="refresh" />刷新队列
        </button>
      </div>
    </div>

    <!-- 账号 -->
    <template v-if="tab === 'accounts'">
      <div class="users-metrics">
        <div class="users-metric">
          <div class="users-metric-head"><span class="users-metric-icon"><AppIcon name="users" /></span><span>账号总数</span></div>
          <strong>{{ formatNumber(users.length) }}</strong><em>含停用账号</em>
        </div>
        <div class="users-metric">
          <div class="users-metric-head"><span class="users-metric-icon"><AppIcon name="check-circle" /></span><span>启用中</span></div>
          <strong>{{ formatNumber(enabledUserCount) }}</strong><em>可登录工作台</em>
        </div>
        <div class="users-metric">
          <div class="users-metric-head"><span class="users-metric-icon"><AppIcon name="shield" /></span><span>管理员</span></div>
          <strong>{{ formatNumber(adminCount) }}</strong><em>拥有用户管理权限</em>
        </div>
        <div class="users-metric">
          <div class="users-metric-head"><span class="users-metric-icon"><AppIcon name="layers" /></span><span>角色</span></div>
          <strong>{{ formatNumber(roles.length) }}</strong><em>按模块授权</em>
        </div>
      </div>

      <article class="card">
        <div class="card-head">
          <div>
            <div class="card-title">馆员账号</div>
            <div class="card-sub">账号仅用于馆员工作台。读者门户账号在读者档案中单独管理。</div>
          </div>
        </div>
        <div v-if="loading.users" class="settings-loading"><span class="spinner" />正在读取账号…</div>
        <div v-else class="table-wrap">
          <table class="table is-striped">
            <thead><tr><th>账号</th><th>角色</th><th>状态</th><th>最近登录</th><th>操作</th></tr></thead>
            <tbody>
              <tr v-for="row in users" :key="row.id">
                <td>
                  <div class="users-account-cell">
                    <span class="avatar" :class="avatarClass(row.id)">{{ row.display_name.slice(-2) }}</span>
                    <div>
                      <div class="cell-main">{{ row.display_name }}<span v-if="row.id === auth.user?.id" class="users-self">本人</span></div>
                      <div class="cell-sub">{{ row.username }}</div>
                    </div>
                  </div>
                </td>
                <td>
                  <span class="badge no-dot" :class="row.is_admin ? 'badge-primary' : 'badge-neutral'">{{ row.role_title || row.role_name || '未分配' }}</span>
                </td>
                <td>
                  <span class="status-text" :class="row.enabled ? 'ok' : 'muted'"><span class="dot" />{{ row.enabled ? '启用' : '停用' }}</span>
                </td>
                <td class="cell-date">{{ formatDateTime(row.last_login_at) || '从未登录' }}</td>
                <td>
                  <div class="row-actions">
                    <button class="text-link" type="button" @click="openUserModal(row)">编辑</button>
                    <button class="text-link" type="button" :disabled="!row.enabled || row.id === auth.user?.id" @click="kickUser(row)">踢下线</button>
                    <button
                      v-if="row.id !== auth.user?.id"
                      class="text-link danger"
                      type="button"
                      @click="userDeleteTarget = row"
                    >删除</button>
                  </div>
                </td>
              </tr>
              <tr v-if="!users.length"><td colspan="5" class="table-state">当前没有馆员账号</td></tr>
            </tbody>
          </table>
        </div>
      </article>
    </template>

    <!-- 角色 -->
    <template v-else-if="tab === 'roles'">
      <div class="users-role-grid">
        <article v-for="role in roles" :key="role.id" class="users-role-card" :class="{ 'is-builtin': role.builtin }">
          <header>
            <div class="users-role-heading">
              <span class="users-role-icon"><AppIcon name="shield" /></span>
              <div>
                <div class="users-role-title">
                  <strong>{{ role.title }}</strong>
                  <span v-if="role.builtin" class="badge badge-neutral no-dot">内置</span>
                </div>
                <span class="users-role-name">{{ role.name }}</span>
              </div>
            </div>
            <span class="users-role-count">{{ formatNumber(role.user_count) }} 人</span>
          </header>
          <p>{{ role.note || '未填写角色说明。' }}</p>
          <div class="users-role-levels">
            <div class="users-role-level-labels">
              <span><i class="is-write" />可写 {{ roleLevelCount(role, 'write') }}</span>
              <span><i class="is-read" />只读 {{ roleLevelCount(role, 'read') }}</span>
            </div>
            <div class="users-role-bar"
              :class="{ 'is-empty': !roleLevelCount(role, 'write') && !roleLevelCount(role, 'read') }">
              <span class="is-write" :style="{ flexGrow: roleLevelCount(role, 'write') }" />
              <span class="is-read" :style="{ flexGrow: roleLevelCount(role, 'read') }" />
            </div>
          </div>
          <footer>
            <button class="text-link" type="button" @click="openRoleModal(role)">编辑权限</button>
            <button
              v-if="role.name !== 'admin'"
              class="text-link danger"
              type="button"
              :disabled="role.user_count > 0"
              @click="roleDeleteTarget = role"
            >删除</button>
          </footer>
        </article>
        <button class="users-role-add" type="button" @click="openRoleModal(null)">
          <span class="users-role-add-icon"><AppIcon name="plus" :size="20" /></span>
          <strong>新建角色</strong>
          <span>按模块配置只读或读写权限</span>
        </button>
      </div>
    </template>

    <!-- 注册申请 -->
    <template v-else-if="tab === 'registrations'">
      <article class="card users-register-card">
        <div class="users-register-head">
          <div>
            <div class="card-title">自助注册</div>
            <div class="card-sub">开放后登录页显示「申请开通」。提交的申请需管理员批准。</div>
          </div>
          <label class="settings-inline-switch">
            <input :checked="allowRegister" type="checkbox" @change="onRegisterToggle" />
            {{ allowRegister ? '已开放' : '已关闭' }}
          </label>
        </div>
        <div class="users-register-filter">
          <button type="button" class="chip" :class="{ 'is-active': regStatus === 'pending' }" @click="regStatus = 'pending'; loadRegistrations()">待审核 <span class="count">{{ pendingCount }}</span></button>
          <button type="button" class="chip" :class="{ 'is-active': regStatus === 'all' }" @click="regStatus = 'all'; loadRegistrations()">全部申请</button>
        </div>
        <div v-if="loading.registrations" class="settings-loading"><span class="spinner" />正在读取申请…</div>
        <div v-else-if="!registrations.length" class="empty-card" style="margin: 18px">{{ regStatus === 'pending' ? '当前没有待审核的注册申请' : '当前没有注册申请' }}</div>
        <div v-else class="table-wrap">
          <table class="table">
            <thead><tr><th>申请人</th><th>备注</th><th>提交时间</th><th>状态</th><th>处理信息</th><th>操作</th></tr></thead>
            <tbody>
              <tr v-for="row in registrations" :key="row.id">
                <td><div class="cell-main">{{ row.username }}</div></td>
                <td class="cell-sub">{{ row.note || '—' }}</td>
                <td class="cell-date">{{ formatDateTime(row.created_at) || '—' }}</td>
                <td>
                  <span class="badge no-dot" :class="row.status === 'pending' ? 'badge-warn' : row.status === 'approved' ? 'badge-ok' : 'badge-neutral'">{{ REG_LABELS[row.status] || row.status }}</span>
                </td>
                <td class="cell-sub">
                  <template v-if="row.reviewed_at">{{ row.reviewed_by || '—' }} · {{ formatDateTime(row.reviewed_at) }}</template>
                  <template v-else>—</template>
                  <div v-if="row.reject_reason" class="users-reject-reason">{{ row.reject_reason }}</div>
                </td>
                <td>
                  <div v-if="row.status === 'pending'" class="row-actions">
                    <button class="text-link" type="button" @click="openApprove(row)">批准</button>
                    <button class="text-link danger" type="button" @click="openReject(row)">驳回</button>
                  </div>
                  <span v-else class="cell-sub">已处理</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </article>
    </template>

    <!-- 接口凭据 -->
    <template v-else>
      <article class="card">
        <div class="card-head">
          <div>
            <div class="card-title">长期凭据</div>
            <div class="card-sub">供外部程序调用接口使用。权限完全继承所绑定角色，明文仅在创建时显示一次。</div>
          </div>
          <span class="badge badge-neutral no-dot">{{ activeAgentCount }} 条有效</span>
        </div>
        <div v-if="loading.agents" class="settings-loading"><span class="spinner" />正在读取凭据…</div>
        <div v-else-if="!agents.length" class="empty-card" style="margin: 18px">当前没有凭据</div>
        <div v-else class="table-wrap">
          <table class="table">
            <thead><tr><th>凭据</th><th>角色</th><th>前缀</th><th>状态</th><th>最近使用</th><th>创建</th><th>操作</th></tr></thead>
            <tbody>
              <tr v-for="row in agents" :key="row.id">
                <td>
                  <div class="cell-main">{{ row.name }}</div>
                  <div class="cell-sub">{{ row.note || '未填写备注' }}</div>
                </td>
                <td>{{ row.role_title || row.role_name || '—' }}</td>
                <td><span class="code-chip">{{ row.prefix }}…</span></td>
                <td>
                  <span class="status-text" :class="row.active ? 'ok' : 'muted'"><span class="dot" />{{ row.active ? '有效' : '已吊销' }}</span>
                </td>
                <td class="cell-date">{{ formatDateTime(row.last_used_at) || '从未使用' }}</td>
                <td class="cell-sub">{{ row.created_by || '—' }} · {{ formatDateTime(row.created_at) || '—' }}</td>
                <td>
                  <button v-if="row.active" class="text-link danger" type="button" @click="agentDeleteTarget = row">吊销</button>
                  <span v-else class="cell-sub">—</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </article>

      <article class="card">
        <div class="card-head">
          <div>
            <div class="card-title">调用记录</div>
            <div class="card-sub">仅记录外部程序的接口请求。共 {{ formatNumber(auditTotal) }} 条，此处显示最新 20 条。</div>
          </div>
          <div class="card-head-actions">
            <button class="btn btn-ghost btn-sm" type="button" :disabled="loading.audit" @click="loadAudit">
              <AppIcon name="refresh" />刷新
            </button>
            <button class="btn btn-danger-soft btn-sm" type="button"
              :disabled="loading.audit || saving || !auditTotal"
              title="清空全部调用记录（凭据不受影响）" @click="auditClearOpen = true">
              <AppIcon name="trash" />清空
            </button>
          </div>
        </div>
        <div v-if="loading.audit" class="settings-loading"><span class="spinner" />正在读取调用记录…</div>
        <div v-else-if="!audit.length" class="empty-card" style="margin: 18px">当前没有调用记录</div>
        <div v-else class="table-wrap">
          <table class="table">
            <thead><tr><th>时间</th><th>凭据</th><th>请求</th><th class="num">状态</th><th class="num">耗时</th></tr></thead>
            <tbody>
              <tr v-for="row in audit" :key="row.id">
                <td class="cell-date">{{ formatDateTime(row.created_at) || '—' }}</td>
                <td>{{ row.token_name }}</td>
                <td><span class="users-method" :class="`is-${row.method.toLowerCase()}`">{{ row.method }}</span><span class="users-path">{{ row.path }}</span></td>
                <td class="num"><span class="status-text" :class="row.status < 400 ? 'ok' : 'danger'"><span class="dot" />{{ row.status }}</span></td>
                <td class="num">{{ row.duration_ms }} ms</td>
              </tr>
            </tbody>
          </table>
        </div>
      </article>
    </template>

    <!-- 账号弹窗 -->
    <BaseModal :busy="saving" :open="userModalOpen" :title="editingUser ? '编辑账号' : '新增账号'" size="md" @close="userModalOpen = false">
      <div class="form-grid">
        <div class="field">
          <label for="user-name">登录名</label>
          <input id="user-name" v-model="userForm.username" class="input" maxlength="64" autocomplete="off" :disabled="Boolean(editingUser)" />
        </div>
        <div class="field">
          <label for="user-display">显示名称</label>
          <input id="user-display" v-model="userForm.displayName" class="input" maxlength="64" placeholder="留空则使用登录名" />
        </div>
        <div class="field">
          <label for="user-role">角色</label>
          <select id="user-role" v-model="userForm.roleId" class="select">
            <option v-for="role in roles" :key="role.id" :value="role.id">{{ role.title }}</option>
          </select>
        </div>
        <div class="field">
          <label for="user-password">{{ editingUser ? '重置密码（留空不改）' : '初始密码' }}</label>
          <input id="user-password" v-model="userForm.password" class="input" type="password" autocomplete="new-password" placeholder="至少 6 位" />
        </div>
        <label class="switch-line span-2">
          <span>{{ isSelf ? '当前登录账号不能停用' : '账号启用' }}</span>
          <input v-model="userForm.enabled" type="checkbox" :disabled="isSelf" />
        </label>
        <p v-if="editingUser" class="field-note span-2">重置密码或停用后，该账号已登录的会话会立即失效。</p>
      </div>
      <template #footer>
        <button class="btn btn-ghost" type="button" :disabled="saving" @click="userModalOpen = false">取消</button>
        <button class="btn btn-primary" type="button" :disabled="saving" @click="saveUser">{{ saving ? '保存中…' : '保存' }}</button>
      </template>
    </BaseModal>

    <!-- 角色弹窗 -->
    <BaseModal :busy="saving" :open="roleModalOpen" :title="editingRole ? '编辑角色' : '新增角色'" :subtitle="isAdminRole ? '管理员角色固定拥有全部模块读写权限' : '按模块选择不可见、只读或读写'" size="lg" @close="roleModalOpen = false">
      <div class="form-grid">
        <div class="field">
          <label for="role-name">角色标识</label>
          <input id="role-name" v-model="roleForm.name" class="input" maxlength="64" placeholder="英文标识，如 cataloger" :disabled="Boolean(editingRole)" />
        </div>
        <div class="field">
          <label for="role-title">显示名称</label>
          <input id="role-title" v-model="roleForm.title" class="input" maxlength="64" placeholder="如 编目员" />
        </div>
        <div class="field span-2">
          <label for="role-note">说明</label>
          <input id="role-note" v-model="roleForm.note" class="input" maxlength="256" placeholder="可选" />
        </div>
      </div>
      <div class="users-perm-head">
        <strong>模块权限</strong>
        <span>可写 {{ countLevel('write') }} · 只读 {{ countLevel('read') }} · 不可见 {{ countLevel('none') }}</span>
      </div>
      <div class="users-perm-groups">
        <div v-for="group in modulesByGroup" :key="group.name" class="users-perm-group">
          <div class="users-perm-group-name">{{ group.name }}</div>
          <div v-for="module in group.items" :key="module.key" class="users-perm-row">
            <span>{{ module.title }}</span>
            <div class="users-perm-levels">
              <button type="button" :class="{ 'is-active': roleLevels[module.key] === 'none' }" :disabled="isAdminRole" @click="setLevel(module.key, 'none')">不可见</button>
              <button type="button" :class="{ 'is-active': roleLevels[module.key] === 'read' }" :disabled="isAdminRole" @click="setLevel(module.key, 'read')">只读</button>
              <button type="button" :class="{ 'is-active': roleLevels[module.key] === 'write' }" :disabled="isAdminRole" @click="setLevel(module.key, 'write')">读写</button>
            </div>
          </div>
        </div>
      </div>
      <template #footer>
        <button class="btn btn-ghost" type="button" :disabled="saving" @click="roleModalOpen = false">取消</button>
        <button class="btn btn-primary" type="button" :disabled="saving" @click="saveRole">{{ saving ? '保存中…' : '保存' }}</button>
      </template>
    </BaseModal>

    <!-- 批准注册 -->
    <BaseModal :busy="saving" :open="Boolean(approveTarget)" title="批准注册申请" :subtitle="approveTarget?.username" size="sm" @close="approveTarget = null">
      <div class="auth-form">
        <div class="field">
          <label for="approve-name">显示名称</label>
          <input id="approve-name" v-model="approveForm.displayName" class="input" maxlength="64" />
        </div>
        <div class="field">
          <label for="approve-role">分配角色</label>
          <select id="approve-role" v-model="approveForm.roleId" class="select">
            <option v-for="role in roles" :key="role.id" :value="role.id">{{ role.title }}</option>
          </select>
        </div>
        <p class="field-note">批准后账号立即生效，申请人使用提交时的密码登录。</p>
      </div>
      <template #footer>
        <button class="btn btn-ghost" type="button" :disabled="saving" @click="approveTarget = null">取消</button>
        <button class="btn btn-primary" type="button" :disabled="saving || !approveForm.roleId" @click="submitApprove">{{ saving ? '处理中…' : '批准' }}</button>
      </template>
    </BaseModal>

    <!-- 驳回注册 -->
    <BaseModal :busy="saving" :open="Boolean(rejectTarget)" title="驳回注册申请" :subtitle="rejectTarget?.username" size="sm" @close="rejectTarget = null">
      <div class="field">
        <label for="reject-reason">驳回原因</label>
        <textarea id="reject-reason" v-model="rejectReason" class="textarea" rows="3" maxlength="256" placeholder="可选，仅管理员可见" />
      </div>
      <template #footer>
        <button class="btn btn-ghost" type="button" :disabled="saving" @click="rejectTarget = null">取消</button>
        <button class="btn btn-danger" type="button" :disabled="saving" @click="submitReject">{{ saving ? '处理中…' : '确认驳回' }}</button>
      </template>
    </BaseModal>

    <!-- 新建接口凭据 -->
    <BaseModal :busy="saving" :open="agentModalOpen" title="新建接口凭据" subtitle="凭据明文只在创建后显示一次" size="sm" @close="agentModalOpen = false">
      <div class="auth-form">
        <div class="field">
          <label for="agent-name">凭据名称</label>
          <input id="agent-name" v-model="agentForm.name" class="input" maxlength="64" placeholder="如 导入机器人" />
        </div>
        <div class="field">
          <label for="agent-role">绑定角色</label>
          <select id="agent-role" v-model="agentForm.roleId" class="select">
            <option v-for="role in roles" :key="role.id" :value="role.id">{{ role.title }}</option>
          </select>
          <span class="field-note">凭据权限完全继承该角色，不会额外提权。</span>
        </div>
        <div class="field">
          <label for="agent-note">备注</label>
          <input id="agent-note" v-model="agentForm.note" class="input" maxlength="256" placeholder="可选" />
        </div>
      </div>
      <template #footer>
        <button class="btn btn-ghost" type="button" :disabled="saving" @click="agentModalOpen = false">取消</button>
        <button class="btn btn-primary" type="button" :disabled="saving" @click="createAgent">{{ saving ? '创建中…' : '创建凭据' }}</button>
      </template>
    </BaseModal>

    <!-- 一次性明文 -->
    <BaseModal :busy="saving" :open="secretModalOpen" title="凭据已创建" subtitle="关闭后无法再次查看明文" size="md" @close="secretModalOpen = false">
      <div class="users-secret">
        <p>请立即复制并写入 MCP 客户端配置。关闭后无法再次查看。</p>
        <div class="users-secret-box">
          <code>{{ newSecret }}</code>
          <button class="btn btn-outline btn-sm" type="button" @click="copySecret"><AppIcon name="file" />复制</button>
        </div>
        <p class="field-note">如果遗失，只能吊销后重新创建。</p>
      </div>
      <template #footer>
        <button class="btn btn-primary" type="button" @click="secretModalOpen = false">我已保存</button>
      </template>
    </BaseModal>

    <ConfirmDialog
      :open="Boolean(userDeleteTarget)"
      title="删除馆员账号"
      :message="userDeleteTarget ? `确认删除「${userDeleteTarget.display_name}（${userDeleteTarget.username}）」？其登录会话会立即失效。` : ''"
      confirm-text="删除账号"
      danger
      :busy="saving"
      @cancel="userDeleteTarget = null"
      @confirm="deleteUser"
    />

    <ConfirmDialog
      :open="Boolean(roleDeleteTarget)"
      title="删除角色"
      :message="roleDeleteTarget ? `确认删除角色「${roleDeleteTarget.title}」？仅当没有账号使用该角色时才能删除。` : ''"
      confirm-text="删除角色"
      danger
      :busy="saving"
      @cancel="roleDeleteTarget = null"
      @confirm="deleteRole"
    />

    <ConfirmDialog
      :open="auditClearOpen"
      title="清空调用记录"
      :message="`确认清空全部 ${formatNumber(auditTotal)} 条调用记录？凭据不受影响，已清空的记录不可恢复。`"
      confirm-text="清空记录"
      danger
      :busy="saving"
      @cancel="auditClearOpen = false"
      @confirm="clearAudit"
    />

    <ConfirmDialog
      :open="Boolean(agentDeleteTarget)"
      title="吊销接口凭据"
      :message="agentDeleteTarget ? `确认吊销「${agentDeleteTarget.name}」？吊销立即生效，且不可恢复。` : ''"
      confirm-text="吊销凭据"
      danger
      :busy="saving"
      @cancel="agentDeleteTarget = null"
      @confirm="revokeAgent"
    />
  </section>
</template>
