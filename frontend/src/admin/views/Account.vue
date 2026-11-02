<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import AppIcon from '../../components/AppIcon.vue'
import { auth, logout } from '../../lib/auth'
import { errorMessage, post, setToken } from '../../lib/api'
import { avatarClass, formatDateTime } from '../../lib/format'
import { themeState, toggleTheme } from '../../lib/theme'
import { toast } from '../../lib/toast'

const router = useRouter()
const user = computed(() => auth.user)
const avatarText = computed(() => {
  const name = user.value?.display_name || '馆员'
  return name.length > 2 ? name.slice(-2) : name
})

const form = reactive({ oldPassword: '', newPassword: '', confirm: '' })
const busy = ref(false)
const error = ref('')

async function submitPassword() {
  if (busy.value) return
  error.value = ''
  if (!form.oldPassword) {
    error.value = '请输入当前密码'
    return
  }
  if (form.newPassword.length < 6) {
    error.value = '新密码至少 6 位'
    return
  }
  if (form.newPassword !== form.confirm) {
    error.value = '两次输入的新密码不一致'
    return
  }
  busy.value = true
  try {
    const result = await post<{ token: string; message: string }>('/auth/password', {
      old_password: form.oldPassword,
      new_password: form.newPassword,
    })
    setToken(result.token)
    toast(result.message)
    form.oldPassword = ''
    form.newPassword = ''
    form.confirm = ''
  } catch (e) {
    error.value = errorMessage(e)
  } finally {
    busy.value = false
  }
}

async function signOut() {
  await logout()
  await router.push('/login')
}
</script>

<template>
  <section class="view">
    <header v-if="user" class="reader-card">
      <span class="avatar" :class="avatarClass(user.id)">{{ avatarText }}</span>
      <div class="reader-card-main">
        <h2>{{ user.display_name }}</h2>
        <div class="rc-sub">{{ user.role_title || user.role_name || '馆员' }} · {{ user.username }}</div>
        <div class="reader-card-tags">
          <span class="badge badge-ok no-dot">账号正常</span>
          <span v-if="user.is_admin" class="badge badge-primary no-dot">管理员</span>
        </div>
      </div>
      <div class="reader-card-actions">
        <button class="btn btn-outline btn-sm" type="button" @click="toggleTheme">
          <AppIcon :name="themeState.mode === 'dark' ? 'sun' : 'moon'" />
          {{ themeState.mode === 'dark' ? '切换亮色' : '切换暗色' }}
        </button>
        <button class="btn btn-ghost btn-sm" type="button" @click="signOut">
          <AppIcon name="logout" />退出登录
        </button>
      </div>
    </header>

    <div class="profile-layout">
      <div class="profile-main">
        <article class="card">
          <div class="card-head">
            <div>
              <div class="card-title">修改密码</div>
              <div class="card-sub">修改后其他设备的登录状态立即失效</div>
            </div>
          </div>
          <div class="card-body">
            <form class="auth-form" @submit.prevent="submitPassword">
              <div class="field">
                <label for="ac-old">当前密码</label>
                <input id="ac-old" v-model="form.oldPassword" class="input" type="password" autocomplete="current-password" />
              </div>
              <div class="form-grid">
                <div class="field">
                  <label for="ac-new">新密码</label>
                  <input id="ac-new" v-model="form.newPassword" class="input" type="password" autocomplete="new-password" />
                  <span class="hint">至少 6 位</span>
                </div>
                <div class="field">
                  <label for="ac-confirm">确认新密码</label>
                  <input id="ac-confirm" v-model="form.confirm" class="input" type="password" autocomplete="new-password" />
                </div>
              </div>
              <p v-if="error" class="field-note is-error" role="alert">{{ error }}</p>
              <div class="page-tools" style="margin: 4px 0 0">
                <span class="spacer" />
                <button class="btn btn-primary" type="submit" :disabled="busy">
                  {{ busy ? '保存中…' : '保存新密码' }}
                </button>
              </div>
            </form>
          </div>
        </article>
      </div>

      <aside class="profile-side">
        <article class="card record-meta-card">
          <div class="card-head">
            <div class="card-title">账号信息</div>
          </div>
          <div class="card-body">
            <div class="info-line"><span>登录账号</span><strong>{{ user?.username }}</strong></div>
            <div class="info-line"><span>显示名称</span><strong>{{ user?.display_name }}</strong></div>
            <div class="info-line"><span>角色</span><strong>{{ user?.role_title || user?.role_name || '—' }}</strong></div>
            <div class="info-line"><span>上次登录</span><strong>{{ formatDateTime(user?.last_login_at) || '—' }}</strong></div>
            <div class="info-line"><span>创建时间</span><strong>{{ formatDateTime(user?.created_at) || '—' }}</strong></div>
          </div>
        </article>
      </aside>
    </div>
  </section>
</template>
