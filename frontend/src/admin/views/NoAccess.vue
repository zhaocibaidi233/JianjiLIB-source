<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import AppIcon from '../../components/AppIcon.vue'
import { auth, logout } from '../../lib/auth'

const router = useRouter()
const who = computed(() => auth.user?.display_name || auth.user?.username || '当前账号')
const role = computed(() => auth.user?.role_title || auth.user?.role_name || '馆员')

async function switchAccount() {
  await logout()
  await router.replace('/login')
}
</script>

<template>
  <section class="view no-access">
    <div class="no-access-card">
      <span class="no-access-glyph"><AppIcon name="shield" :size="34" /></span>
      <h2>此处需要更高的访问权限</h2>
      <p>当前账号无此模块访问权限。可返回工作台，或由管理员调整角色权限。</p>
      <div class="no-access-meta">
        <span class="avatar avatar-sm">{{ who.slice(-2) }}</span>
        <span>{{ who }} · {{ role }}</span>
      </div>
      <div class="no-access-actions">
        <RouterLink class="btn btn-primary" to="/admin">返回工作台</RouterLink>
        <button v-if="auth.enabled" class="btn btn-outline" type="button" @click="switchAccount">切换账号</button>
      </div>
    </div>
  </section>
</template>
