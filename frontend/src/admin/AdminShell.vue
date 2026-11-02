<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { RouterLink, RouterView, useRoute, useRouter } from 'vue-router'
import AppIcon from '../components/AppIcon.vue'
import { auth, can, ensureAuthReady, logout } from '../lib/auth'
import { BOARD_ORDER, NAV_GROUPS, boardHome, boardOfPath, navForPath } from '../lib/nav'
import type { Board } from '../lib/nav'
import { themeState, toggleTheme } from '../lib/theme'
import { currentLibrary, libraryState, loadLibraries, setCurrentLibrary } from '../stores/libraries'
import { dashboardState, loadDashboard, overdueCount } from '../stores/dashboard'
import { useTopbarHeight } from '../lib/useTopbarHeight'

const route = useRoute()
const router = useRouter()

const mobileOpen = ref(false)
const notificationsOpen = ref(false)
const userMenuOpen = ref(false)
const notificationsRead = ref(false)
const notifRef = ref<HTMLElement | null>(null)
const userRef = ref<HTMLElement | null>(null)

const BOARD_KEY = 'jianji.board'
const savedBoard = localStorage.getItem(BOARD_KEY)
const initialBoard: Board = boardOfPath(route.path)
  ?? (savedBoard === 'catalog' || savedBoard === 'circulation' ? savedBoard : null)
  ?? 'catalog'
const board = ref<Board>(initialBoard)

const catalogHasPages = computed(() =>
  ['records', 'barcodes', 'labels', 'resources', 'io', 'inspect', 'dictionaries', 'stats']
    .some((module) => can(module)),
)
const circHasPages = computed(() =>
  ['circulation', 'readers'].some((module) => can(module)),
)
const showBoardSwitch = computed(() => catalogHasPages.value && circHasPages.value)

const visibleGroups = computed(() =>
  NAV_GROUPS
    .filter((group) => group.board === 'shared' || group.board === board.value)
    .map((group) => ({
      ...group,
      items: group.items.filter((item) => can(item.module)),
    }))
    .filter((group) => group.items.length),
)

function switchBoard(target: Board) {
  if (target === board.value) return
  if (target === 'catalog' ? !catalogHasPages.value : !circHasPages.value) return
  board.value = target
  localStorage.setItem(BOARD_KEY, target)
  const current = boardOfPath(route.path)
  if (current && current !== target) {
    void router.push(boardHome(target, (module) => can(module)))
  }
}

watch(
  () => route.path,
  (path) => {
    const target = boardOfPath(path)
    if (target && target !== board.value) {
      board.value = target
      localStorage.setItem(BOARD_KEY, target)
    }
  },
)

// 权限异步就绪后，若当前板块一个页面都没有而另一块有，自动落到可用板块
watch([catalogHasPages, circHasPages], ([hasCatalog, hasCirc]) => {
  if (board.value === 'catalog' && !hasCatalog && hasCirc) {
    board.value = 'circulation'
  } else if (board.value === 'circulation' && !hasCirc && hasCatalog) {
    board.value = 'catalog'
  }
}, { immediate: true })

const displayName = computed(() => auth.user?.display_name || '馆员')
const roleTitle = computed(() => auth.user?.role_title || auth.user?.role_name || '馆员')
const avatarText = computed(() => {
  const name = displayName.value.trim()
  return name.length > 2 ? name.slice(-2) : name || '馆'
})
const overdue = computed(() => overdueCount())
const activeLibraryName = computed(
  () => currentLibrary()?.name || dashboardState.data?.library?.name || '全部图书库',
)

function isActive(path: string): boolean {
  return path === '/admin' ? route.path === '/admin' : route.path.startsWith(path)
}

function greeting(): string {
  const hour = new Date().getHours()
  if (hour < 6) return '凌晨好'
  if (hour < 11) return '上午好'
  if (hour < 14) return '中午好'
  if (hour < 18) return '下午好'
  return '晚上好'
}

const pageTitle = computed(() => (route.meta.title as string) || '工作台')
const pageSubtitle = computed(() => {
  if (route.path === '/admin') {
    const due = overdue.value
    return due > 0
      ? `${greeting()}，${displayName.value}。今天有 ${due} 册图书逾期需要处理。`
      : `${greeting()}，${displayName.value}。今天没有逾期图书。`
  }
  return (route.meta.subtitle as string) || navForPath(route.path)?.description || ''
})

const dateLabel = computed(() => {
  const now = new Date()
  const week = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'][now.getDay()]
  return `${now.getMonth() + 1}月${now.getDate()}日 · ${week}`
})

interface NotificationRow {
  icon: string
  tone: string
  text: string
  meta: string
  link: string
}

const notifications = computed<NotificationRow[]>(() => {
  const data = dashboardState.data
  if (!data) return []
  const rows: NotificationRow[] = data.overdue_items.map((item) => ({
    icon: 'alert',
    tone: 'danger',
    text: `《${item.title || '未知题名'}》已逾期 ${item.days_late} 天`,
    meta: `${item.reader_name || item.reader_card_no || '读者'} · 请提醒归还`,
    link: '/admin/circulation?tab=overdue',
  }))
  for (const todo of data.todos) {
    if (todo.kind === 'overdue') continue
    rows.push({
      icon: todo.kind === 'compensation' ? 'receipt' : todo.kind === 'ready_reservation' ? 'bookmark' : 'users',
      tone: 'info',
      text: todo.label,
      meta: todo.detail,
      link: todo.link,
    })
  }
  return rows.slice(0, 4)
})

const hasUnread = computed(() => notifications.value.length > 0 && !notificationsRead.value)

function closePopovers() {
  notificationsOpen.value = false
  userMenuOpen.value = false
}

function onDocumentClick(event: MouseEvent) {
  const target = event.target as Node
  if (notifRef.value && !notifRef.value.contains(target)) notificationsOpen.value = false
  if (userRef.value && !userRef.value.contains(target)) userMenuOpen.value = false
}

function onDocumentKey(event: KeyboardEvent) {
  if (event.key === 'Escape') closePopovers()
}

function openNotifications() {
  userMenuOpen.value = false
  notificationsOpen.value = !notificationsOpen.value
}

function openUserMenu() {
  notificationsOpen.value = false
  userMenuOpen.value = !userMenuOpen.value
}

function markRead() {
  notificationsRead.value = true
  notificationsOpen.value = false
}

async function onLibraryChange(event: Event) {
  const value = (event.target as HTMLSelectElement).value
  setCurrentLibrary(value ? Number(value) : null)
}

async function signOut() {
  await logout()
  closePopovers()
  await router.push('/login')
}

watch(
  () => route.fullPath,
  () => {
    mobileOpen.value = false
    closePopovers()
  },
)

watch(
  () => libraryState.currentId,
  async (id, old) => {
    if (id !== old && id !== null) await loadDashboard(true)
  },
)

useTopbarHeight('.topbar')

onMounted(async () => {
  window.addEventListener('click', onDocumentClick)
  window.addEventListener('keydown', onDocumentKey)
  await ensureAuthReady()
  await loadLibraries()
  await loadDashboard()
})

onBeforeUnmount(() => {
  window.removeEventListener('click', onDocumentClick)
  window.removeEventListener('keydown', onDocumentKey)
})
</script>

<template>
  <div class="app">
    <div v-if="mobileOpen" class="sidebar-scrim" @click="mobileOpen = false" />

    <aside class="sidebar" :class="{ 'is-open': mobileOpen }">
      <button
        class="icon-btn sidebar-theme-btn"
        type="button"
        :title="themeState.mode === 'dark' ? '切换到亮色' : '切换到暗色'"
        aria-label="切换主题"
        @click="toggleTheme"
      >
        <AppIcon :name="themeState.mode === 'dark' ? 'sun' : 'moon'" :size="16" />
      </button>
      <RouterLink class="brand" to="/admin" title="简籍工作台">
        <svg class="brand-mark" viewBox="0 0 34 34" fill="none" aria-hidden="true">
          <rect width="34" height="34" rx="10" fill="#175E54" />
          <path d="M17 10.6c-2.1-1.4-4.7-2-7.8-2v14.8c3.1 0 5.7.6 7.8 2 2.1-1.4 4.7-2 7.8-2V8.6c-3.1 0-5.7.6-7.8 2Z" stroke="#F7F5EC" stroke-width="1.4" stroke-linejoin="round" />
          <path d="M17 10.6v14.8" stroke="#F7F5EC" stroke-width="1.4" stroke-linecap="round" />
          <path d="M13 14.6c1-.2 1.9-.2 2.8 0M13 18c1-.2 1.9-.2 2.8 0" stroke="#E4A23F" stroke-width="1.2" stroke-linecap="round" />
        </svg>
        <span class="brand-text">
          <span class="brand-name">简籍</span>
          <span class="brand-en">JIANJI LIBRARY</span>
        </span>
      </RouterLink>

      <div v-if="showBoardSwitch" class="board-switch" role="group" aria-label="工作板块">
        <button
          v-for="item in BOARD_ORDER"
          :key="item.key"
          type="button"
          :class="{ 'is-active': board === item.key }"
          :aria-pressed="board === item.key"
          :title="item.key === 'circulation' ? '借还、读者与流通' : '采编、馆藏与统计'"
          @click="switchBoard(item.key)"
        >
          <AppIcon :name="item.icon" :size="15" />
          <span>{{ item.label }}</span>
        </button>
      </div>

      <nav class="sidebar-nav" aria-label="工作台导航">
        <template v-for="group in visibleGroups" :key="group.label">
          <p class="nav-label">{{ group.label }}</p>
          <RouterLink
            v-for="item in group.items"
            :key="item.to"
            :to="item.to"
            class="nav-item"
            :class="{ 'is-active': isActive(item.to) }"
            :title="item.label"
            :aria-label="item.label"
          >
            <AppIcon :name="item.icon" />
            <span class="nav-text">{{ item.label }}</span>
            <span
              v-if="item.badge === 'overdue' && overdue > 0"
              class="nav-badge"
            >{{ overdue }}</span>
          </RouterLink>
        </template>
      </nav>

      <div class="sidebar-foot">
        <div class="user-chip" @click.stop="openUserMenu">
          <span class="avatar av-3">{{ avatarText }}</span>
          <div class="user-meta">
            <strong>{{ displayName }}</strong>
            <span>{{ roleTitle }}</span>
          </div>
          <button
            v-if="auth.enabled"
            class="icon-btn logout"
            type="button"
            title="退出登录"
            aria-label="退出登录"
            @click.stop="signOut"
          >
            <AppIcon name="logout" :size="17" />
          </button>
        </div>
      </div>
    </aside>

    <div class="main">
      <header class="topbar" :class="{ 'topbar--legacy': route.meta.legacy, 'topbar--wide': route.meta.wide }">
        <button class="icon-btn mobile-menu-btn" type="button" aria-label="打开导航" @click="mobileOpen = true">
          <AppIcon name="menu" :size="20" />
        </button>
        <div class="topbar-title">
          <h1>{{ pageTitle }}</h1>
          <p class="topbar-sub">{{ pageSubtitle }}</p>
        </div>

        <div class="topbar-actions">
          <RouterLink
            v-if="board === 'catalog' && can('records', 'write')"
            class="btn btn-primary btn-sm quick-circulation"
            to="/admin/records/new"
            title="套录编目"
            aria-label="套录编目"
          >
            <AppIcon name="plus" :size="16" />
            <span>套录编目</span>
          </RouterLink>
          <RouterLink
            v-else-if="board === 'circulation' && can('circulation')"
            class="btn btn-primary btn-sm quick-circulation"
            to="/admin/circulation"
            title="快速借还"
            aria-label="快速借还"
          >
            <AppIcon name="swap" :size="16" />
            <span>快速借还</span>
          </RouterLink>

          <div v-if="libraryState.items.length > 1" class="library-switch">
            <AppIcon name="building" :size="15" />
            <select :value="libraryState.currentId ?? ''" aria-label="切换图书库" @change="onLibraryChange">
              <option v-for="lib in libraryState.items" :key="lib.id" :value="lib.id">
                {{ lib.name }}
              </option>
            </select>
          </div>
          <div v-else class="library-chip" :title="activeLibraryName">
            <AppIcon name="building" :size="15" />
            <span>{{ activeLibraryName }}</span>
          </div>

          <div class="date-chip">
            <AppIcon name="calendar" :size="15" />
            <span>{{ dateLabel }}</span>
          </div>

          <div
            class="ops-status-dot"
            :class="{ danger: !!dashboardState.error }"
            role="status"
            :title="dashboardState.error ? '数据连接异常' : '系统运行正常'"
          >
            <span class="dot" />
            <span class="sr-only">{{ dashboardState.error ? '数据连接异常' : '系统运行正常' }}</span>
          </div>

          <div ref="notifRef" class="notif-wrap">
            <button
              class="icon-btn"
              type="button"
              aria-label="通知"
              :aria-expanded="notificationsOpen"
              @click.stop="openNotifications"
            >
              <AppIcon name="bell" :size="19" />
              <span v-if="hasUnread" class="dot-indicator" />
            </button>
            <div v-if="notificationsOpen" class="notif-pop">
              <div class="notif-head">
                <strong>通知</strong>
                <button type="button" @click="markRead">全部已读</button>
              </div>
              <template v-if="notifications.length">
                <RouterLink
                  v-for="(item, index) in notifications"
                  :key="`${item.text}-${index}`"
                  class="notif-item"
                  :to="item.link"
                  @click="closePopovers"
                >
                  <span class="notif-icon" :class="item.tone">
                    <AppIcon :name="item.icon" :size="17" />
                  </span>
                  <div class="notif-text">
                    <p>{{ item.text }}</p>
                    <span>{{ item.meta }}</span>
                  </div>
                </RouterLink>
              </template>
              <div v-else class="notif-empty">当前没有新的提醒</div>
              <div class="notif-foot">
                <RouterLink to="/admin/circulation?tab=overdue" @click="closePopovers">
                  前往借还台处理
                </RouterLink>
              </div>
            </div>
          </div>

          <div ref="userRef" class="avatar-wrap">
            <button class="topbar-avatar" type="button" :title="`${displayName} · ${roleTitle}`" @click.stop="openUserMenu">
              {{ avatarText }}
            </button>
            <div v-if="userMenuOpen" class="user-pop">
              <div class="user-pop-head">
                <strong>{{ displayName }}</strong>
                <span>{{ roleTitle }}<template v-if="auth.enabled"> · {{ auth.user?.username }}</template></span>
              </div>
              <RouterLink to="/admin/account" class="user-pop-item" @click="closePopovers">
                <AppIcon name="users" :size="16" /> 我的账号
              </RouterLink>
              <button type="button" class="user-pop-item" @click="toggleTheme">
                <AppIcon :name="themeState.mode === 'dark' ? 'sun' : 'moon'" :size="16" />
                {{ themeState.mode === 'dark' ? '切换到亮色' : '切换到暗色' }}
              </button>
              <button v-if="auth.enabled" type="button" class="user-pop-item danger" @click="signOut">
                <AppIcon name="logout" :size="16" /> 退出登录
              </button>
            </div>
          </div>
        </div>
      </header>

      <main class="content" :class="{ 'content--legacy': route.meta.legacy, 'content--wide': route.meta.wide }">
        <RouterView />
      </main>
    </div>
  </div>
</template>
