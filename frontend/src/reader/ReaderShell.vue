<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { RouterLink, RouterView, useRoute, useRouter } from 'vue-router'
import AppIcon from '../components/AppIcon.vue'
import { avatarClass } from '../lib/format'
import { readerAuth, ensureReaderReady, readerLogout } from '../lib/readerAuth'
import { themeState, toggleTheme } from '../lib/theme'
import { useTopbarHeight } from '../lib/useTopbarHeight'

const route = useRoute()
const router = useRouter()
const userOpen = ref(false)
const userRef = ref<HTMLElement | null>(null)
const reader = computed(() => readerAuth.reader)
const avatarText = computed(() => {
  const name = reader.value?.name || '读'
  return name.length > 2 ? name.slice(-2) : name
})

const NAV = [
  { to: '/reader', label: '发现', icon: 'compass' },
  { to: '/reader/catalog', label: '馆藏', icon: 'search' },
  { to: '/reader/loans', label: '我的借阅', icon: 'books' },
  { to: '/reader/seats', label: '座位预约', icon: 'seat' },
  { to: '/reader/account', label: '我的账号', icon: 'user' },
]

function isActive(path: string): boolean {
  return path === '/reader' ? route.path === '/reader' : route.path.startsWith(path)
}

function onDocumentClick(event: MouseEvent) {
  if (userRef.value && !userRef.value.contains(event.target as Node)) {
    userOpen.value = false
  }
}

watch(
  () => route.fullPath,
  () => {
    userOpen.value = false
  },
)

async function signOut() {
  await readerLogout()
  await router.push({ path: '/login', query: { portal: 'reader' } })
}

useTopbarHeight('.reader-topbar')

onMounted(async () => {
  window.addEventListener('click', onDocumentClick)
  await ensureReaderReady()
})

onBeforeUnmount(() => window.removeEventListener('click', onDocumentClick))
</script>

<template>
  <div class="reader-app">
    <header class="reader-topbar">
      <div class="reader-topbar-inner">
        <RouterLink class="reader-brand brand" to="/reader" title="简籍读者门户">
          <svg class="brand-mark" viewBox="0 0 34 34" fill="none" aria-hidden="true">
            <rect width="34" height="34" rx="10" fill="#175E54" />
            <path d="M17 10.6c-2.1-1.4-4.7-2-7.8-2v14.8c3.1 0 5.7.6 7.8 2 2.1-1.4 4.7-2 7.8-2V8.6c-3.1 0-5.7.6-7.8 2Z" stroke="#F7F5EC" stroke-width="1.4" stroke-linejoin="round" />
            <path d="M17 10.6v14.8" stroke="#F7F5EC" stroke-width="1.4" stroke-linecap="round" />
            <path d="M13 14.6c1-.2 1.9-.2 2.8 0M13 18c1-.2 1.9-.2 2.8 0" stroke="#E4A23F" stroke-width="1.2" stroke-linecap="round" />
          </svg>
          <span class="brand-text">
            <span class="brand-name">简籍</span>
            <span class="brand-en">JIANJI READER</span>
          </span>
        </RouterLink>

        <nav class="reader-nav" aria-label="读者导航">
          <RouterLink
            v-for="item in NAV"
            :key="item.to"
            :to="item.to"
            :class="{ 'is-active': isActive(item.to) }"
          >{{ item.label }}</RouterLink>
        </nav>

        <div class="reader-actions">
          <button class="icon-btn" type="button" :aria-label="themeState.mode === 'dark' ? '切换亮色' : '切换暗色'" @click="toggleTheme">
            <AppIcon :name="themeState.mode === 'dark' ? 'sun' : 'moon'" :size="18" />
          </button>
          <div ref="userRef" class="reader-avatar-wrap">
            <button class="reader-avatar-btn" type="button" :aria-expanded="userOpen" @click.stop="userOpen = !userOpen">
              <span class="avatar" :class="avatarClass(reader?.id || 0)">{{ avatarText }}</span>
              <span class="reader-avatar-name">{{ reader?.name || '读者' }}</span>
              <AppIcon name="chevron-down" :size="14" />
            </button>
            <div v-if="userOpen" class="reader-user-pop">
              <div class="reader-user-head">
                <strong>{{ reader?.name }}</strong>
                <span>{{ reader?.card_no }} · {{ reader?.status_label }}</span>
              </div>
              <RouterLink class="reader-user-item" to="/reader/loans" @click="userOpen = false">
                <AppIcon name="books" />我的借阅
              </RouterLink>
              <RouterLink class="reader-user-item" to="/reader/seats" @click="userOpen = false">
                <AppIcon name="seat" />座位预约
              </RouterLink>
              <RouterLink class="reader-user-item" to="/reader/account" @click="userOpen = false">
                <AppIcon name="user" />我的账号
              </RouterLink>
              <button class="reader-user-item" type="button" @click="toggleTheme">
                <AppIcon :name="themeState.mode === 'dark' ? 'sun' : 'moon'" />
                {{ themeState.mode === 'dark' ? '切换亮色' : '切换暗色' }}
              </button>
              <button class="reader-user-item danger" type="button" @click="signOut">
                <AppIcon name="logout" />退出登录
              </button>
            </div>
          </div>
        </div>
      </div>
    </header>

    <main class="reader-main" :class="{ 'is-wide': route.path === '/reader' || route.path === '/reader/catalog' }">
      <div v-if="reader && reader.status !== 'ACTIVE'" class="reader-status-banner">
        <AppIcon name="alert" />
        当前读者状态：{{ reader.status_label }}。仍可查询借阅历史与预约记录，如需恢复请联系图书馆。
      </div>
      <RouterView />
    </main>

    <nav class="reader-mobile-nav" aria-label="读者移动导航">
      <RouterLink
        v-for="item in NAV"
        :key="item.to"
        :to="item.to"
        :class="{ 'is-active': isActive(item.to) }"
      >
        <AppIcon :name="item.icon" />
        {{ item.label }}
      </RouterLink>
    </nav>
  </div>
</template>
