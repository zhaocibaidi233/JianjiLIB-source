<script setup lang="ts">
import { nextTick, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AppIcon from '../components/AppIcon.vue'
import { errorMessage } from '../lib/api'
import {
  auth, defaultRoute, ensureAuthReady, login, register, setupAdmin,
} from '../lib/auth'
import { readerAuth, ensureReaderReady, readerLibraries, readerLogin } from '../lib/readerAuth'
import { themeState, toggleTheme } from '../lib/theme'
import { toast } from '../lib/toast'

const route = useRoute()
const router = useRouter()

const REMEMBER_KEY = 'jianji.login.account'
const READER_REMEMBER_KEY = 'jianji.reader.card'
const remembered = localStorage.getItem(REMEMBER_KEY) || ''
const rememberedCard = localStorage.getItem(READER_REMEMBER_KEY) || ''

const portal = ref<'staff' | 'reader'>(route.query.portal === 'reader' ? 'reader' : 'staff')
const busy = ref(false)
const error = ref('')
const showPassword = ref(false)
const remember = ref(Boolean(remembered))
const readerRemember = ref(Boolean(rememberedCard))
const cardRef = ref<HTMLElement | null>(null)

const form = reactive({ username: remembered, password: '' })
const readerForm = reactive({ cardNo: rememberedCard, password: '' })
const setupForm = reactive({ username: '', displayName: '', password: '', password2: '' })

// 借书证号只在**图书库内**唯一：两个库各自从 0001 编起时，同一个证号对应两个
// 读者。后端遇到这种情形会回「请指定图书库」，这里据此把库选择器亮出来，让读者
// 选一次再登。单库学校永远看不到这个控件。
const readerLibrariesList = ref<{ id: number; name: string }[]>([])
const readerLibraryId = ref<number | null>(null)
const needLibrary = ref(false)

function onCardInput() {
  resetError()
  needLibrary.value = false
  readerLibraryId.value = null
}

// 「申请开通」不是第二个登录入口，是登录卡片内的一次模式切换：
// 申请成功不会登录（账号要管理员批准后才存在），所以提示完就退回登录。
const mode = ref<'login' | 'register'>('login')
const regForm = reactive({ username: '', password: '', password2: '', note: '' })
const regSent = ref('')

function target(): string {
  const redirect = route.query.redirect
  if (typeof redirect === 'string' && redirect.startsWith('/admin')) return redirect
  return defaultRoute()
}

function readerTarget(): string {
  const redirect = route.query.redirect
  if (typeof redirect === 'string' && redirect.startsWith('/reader')) return redirect
  return '/reader'
}

function switchPortal(next: 'staff' | 'reader') {
  if (portal.value === next) return
  portal.value = next
  resetError()
  const query = { ...route.query }
  if (next === 'reader') query.portal = 'reader'
  else delete query.portal
  void router.replace({ path: '/login', query })
  void nextTick(focusFirstInput)
}

function resetError() {
  error.value = ''
}

function focusFirstInput() {
  cardRef.value?.querySelector<HTMLInputElement>('input:not([type="checkbox"])')?.focus()
}

async function doLogin() {
  if (busy.value) return
  const username = form.username.trim()
  if (!username || !form.password) {
    error.value = '请填写账号与密码'
    return
  }
  resetError()
  busy.value = true
  try {
    await login(username, form.password)
    if (remember.value) localStorage.setItem(REMEMBER_KEY, username)
    else localStorage.removeItem(REMEMBER_KEY)
    toast('登录成功', 'success')
    await router.replace(target())
  } catch (err) {
    error.value = errorMessage(err)
  } finally {
    busy.value = false
  }
}

async function doReaderLogin() {
  if (busy.value) return
  const cardNo = readerForm.cardNo.trim()
  if (!cardNo || !readerForm.password) {
    error.value = '请填写借书证号与密码'
    return
  }
  if (needLibrary.value && !readerLibraryId.value) {
    error.value = '该借书证号在多个图书库中都存在，请先选择图书库'
    return
  }
  resetError()
  busy.value = true
  try {
    await readerLogin(cardNo, readerForm.password, readerLibraryId.value)
    if (readerRemember.value) localStorage.setItem(READER_REMEMBER_KEY, cardNo)
    else localStorage.removeItem(READER_REMEMBER_KEY)
    toast('登录成功', 'success')
    await router.replace(readerTarget())
  } catch (err) {
    const message = errorMessage(err)
    error.value = message
    // 后端要求消歧时才去拉图书库列表：单库学校不该为多库的边界情况多看一眼界面。
    if (message.includes('指定图书库') && readerLibrariesList.value.length === 0) {
      try {
        readerLibrariesList.value = await readerLibraries()
        needLibrary.value = true
      } catch {
        // 拉不到列表就维持后端原提示，不要用一个空下拉把读者困住
      }
    }
  } finally {
    busy.value = false
  }
}

async function doSetup() {
  if (busy.value) return
  const username = setupForm.username.trim()
  if (!username) {
    error.value = '请填写管理员账号'
    return
  }
  if (/\s/.test(username)) {
    error.value = '账号不能包含空格'
    return
  }
  if (setupForm.password.length < 6) {
    error.value = '密码至少 6 位'
    return
  }
  if (setupForm.password !== setupForm.password2) {
    error.value = '两次输入的密码不一致'
    return
  }
  resetError()
  busy.value = true
  try {
    await setupAdmin(username, setupForm.password, setupForm.displayName.trim())
    toast('管理员账号已创建，登录功能已开启', 'success')
    await router.replace(target())
  } catch (err) {
    error.value = errorMessage(err)
    await ensureAuthReady(true)
  } finally {
    busy.value = false
  }
}

async function doRegister() {
  if (busy.value) return
  const username = regForm.username.trim()
  if (!username) {
    error.value = '请填写要开通的账号名'
    return
  }
  if (/\s/.test(username)) {
    error.value = '账号不能包含空格'
    return
  }
  if (regForm.password.length < 6) {
    error.value = '密码至少 6 位'
    return
  }
  if (regForm.password !== regForm.password2) {
    error.value = '两次输入的密码不一致'
    return
  }
  resetError()
  busy.value = true
  try {
    await register(username, regForm.password, regForm.note)
    regSent.value = '申请已提交，待管理员审核。'
    // 申请单已落库，但账号还不存在：清掉口令，别把它留在表单里
    regForm.password = ''
    regForm.password2 = ''
  } catch (err) {
    error.value = errorMessage(err)
    // 开关可能在页面打开之后被关掉；重新取一次状态，让入口自己消失
    if (err instanceof Error && 'status' in err && (err as { status?: number }).status === 403) {
      await ensureAuthReady(true)
    }
  } finally {
    busy.value = false
  }
}

function startRegister() {
  resetError()
  regSent.value = ''
  mode.value = 'register'
  void nextTick(() => focusFirstInput())
}

function backToLogin() {
  resetError()
  regSent.value = ''
  mode.value = 'login'
  void nextTick(() => focusFirstInput())
}

async function retry() {
  error.value = ''
  await ensureAuthReady(true)
  if (auth.enabled && auth.authenticated) await router.replace(target())
  else await nextTick()
}

async function enterAnyway() {
  await router.replace('/admin')
}

onMounted(async () => {
  await ensureAuthReady()
  if (portal.value === 'reader') {
    await ensureReaderReady()
    if (readerAuth.authenticated) {
      await router.replace(readerTarget())
      return
    }
  } else if (auth.enabled && auth.authenticated) {
    await router.replace(target())
    return
  }
  await nextTick()
  focusFirstInput()
})
</script>

<template>
  <div class="auth">
    <aside class="auth-aside">
      <div class="brand brand--light">
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
      </div>

      <div class="auth-quote">
        <span class="auth-eyebrow">中小学图书馆工作系统</span>
        <h2>籍有<em>归处</em><br />管有章法。</h2>
        <p>覆盖编目上架、借还预约、馆藏盘点全流程，规范图书流通台账，数据清晰、运行稳定。</p>
      </div>
    </aside>

    <main class="auth-main">
      <div class="auth-mobile-brand">
        <span class="amb-name">简籍</span>
        <span class="amb-en">JIANJI LIBRARY</span>
      </div>
      <button class="icon-btn auth-theme" type="button" :title="themeState.mode === 'dark' ? '切换到亮色' : '切换到暗色'" @click="toggleTheme">
        <AppIcon :name="themeState.mode === 'dark' ? 'sun' : 'moon'" :size="18" />
      </button>

      <div ref="cardRef" class="auth-card">
        <div v-if="!auth.ready" class="auth-loading">
          <span class="spinner" />
          <span>正在连接馆藏服务…</span>
        </div>

        <template v-else>
          <div class="auth-switch" role="tablist" aria-label="登录入口">
            <button
              type="button"
              role="tab"
              :aria-selected="portal === 'staff'"
              :class="{ 'is-active': portal === 'staff' }"
              @click="switchPortal('staff')"
            >
              <AppIcon name="shield" />馆员工作台
            </button>
            <button
              type="button"
              role="tab"
              :aria-selected="portal === 'reader'"
              :class="{ 'is-active': portal === 'reader' }"
              @click="switchPortal('reader')"
            >
              <AppIcon name="book-open" />读者门户
            </button>
          </div>

          <header class="auth-card-head">
            <span class="auth-kicker">{{ portal === 'reader' ? 'READER PORTAL' : auth.needsSetup ? 'FIRST RUN' : mode === 'register' ? 'SELF SIGN-UP' : 'LIBRARY CONSOLE' }}</span>
            <h1>{{ portal === 'reader' ? '读者登录' : auth.needsSetup ? '创建管理员' : mode === 'register' ? '申请开通账号' : '馆员登录' }}</h1>
            <p v-if="portal === 'reader'">使用借书证号查询馆藏、查看借阅并预约座位。</p>
            <p v-else-if="auth.needsSetup">当前还没有馆员账号。创建管理员后，登录与模块权限开始生效。</p>
            <p v-else-if="mode === 'register'">提交账号申请，经管理员审核后开通。</p>
            <p v-else>登录馆员工作台，管理馆藏与流通业务。</p>
          </header>

          <div v-if="error && (portal === 'reader' || !auth.needsSetup)" class="auth-error" role="alert">
            <AppIcon name="alert" />
            <span>{{ error }}</span>
          </div>

          <template v-if="portal === 'reader'">
            <form class="auth-form" @submit.prevent="doReaderLogin">
              <div class="field">
                <label for="reader-card">借书证号</label>
                <div class="input-icon">
                  <input id="reader-card" v-model="readerForm.cardNo" class="input" placeholder="请输入借书证号" autocomplete="username" @input="onCardInput" />
                  <AppIcon name="key" />
                </div>
              </div>
              <div v-if="needLibrary" class="field">
                <label for="reader-library">图书库</label>
                <div class="input-icon">
                  <select id="reader-library" v-model="readerLibraryId" class="input">
                    <option :value="null">请选择图书库</option>
                    <option v-for="lib in readerLibrariesList" :key="lib.id" :value="lib.id">
                      {{ lib.name }}
                    </option>
                  </select>
                  <AppIcon name="books" />
                </div>
              </div>
              <div class="field">
                <label for="reader-password">密码</label>
                <div class="input-icon">
                  <input id="reader-password" v-model="readerForm.password" class="input" :type="showPassword ? 'text' : 'password'" placeholder="登录密码" autocomplete="current-password" @input="resetError" />
                  <AppIcon name="lock" />
                  <button class="auth-eye" type="button" :aria-label="showPassword ? '隐藏密码' : '显示密码'" @click="showPassword = !showPassword">
                    <AppIcon :name="showPassword ? 'eye-off' : 'eye'" />
                  </button>
                </div>
              </div>
              <div class="auth-options">
                <label class="auth-check">
                  <input v-model="readerRemember" type="checkbox" />
                  记住借书证号
                </label>
                <span>忘记密码请联系图书馆</span>
              </div>
              <button class="btn btn-primary btn-block auth-submit" type="submit" :disabled="busy">
                <span v-if="busy" class="spinner-light" />
                {{ busy ? '正在登录…' : '进入读者门户' }}
                <AppIcon v-if="!busy" name="arrow-right" />
              </button>
            </form>
            <div class="auth-foot">
              读者账号由图书馆开通；如需开通或重置密码，请联系图书馆员。<br />
              <span>JIANJI · 读者门户</span>
            </div>
          </template>

          <template v-else-if="auth.needsSetup">
            <div v-if="error" class="auth-error" role="alert">
              <AppIcon name="alert" />
              <span>{{ error }}</span>
            </div>
            <form class="auth-form" @submit.prevent="doSetup">
              <div class="field">
                <label for="setup-username">管理员账号</label>
                <div class="input-icon">
                  <input id="setup-username" v-model="setupForm.username" class="input" placeholder="英文或数字，登录时使用" autocomplete="username" @input="resetError" />
                  <AppIcon name="users" />
                </div>
              </div>
              <div class="field">
                <label for="setup-display">显示名称</label>
                <div class="input-icon">
                  <input id="setup-display" v-model="setupForm.displayName" class="input" placeholder="留空则使用账号名" autocomplete="name" />
                  <AppIcon name="user-plus" />
                </div>
              </div>
              <div class="field">
                <label for="setup-password">密码</label>
                <div class="input-icon">
                  <input id="setup-password" v-model="setupForm.password" class="input" :type="showPassword ? 'text' : 'password'" placeholder="至少 6 位" autocomplete="new-password" @input="resetError" />
                  <AppIcon name="lock" />
                  <button class="auth-eye" type="button" :aria-label="showPassword ? '隐藏密码' : '显示密码'" @click="showPassword = !showPassword">
                    <AppIcon :name="showPassword ? 'eye-off' : 'eye'" />
                  </button>
                </div>
              </div>
              <div class="field">
                <label for="setup-password2">确认密码</label>
                <div class="input-icon">
                  <input id="setup-password2" v-model="setupForm.password2" class="input" :type="showPassword ? 'text' : 'password'" placeholder="再输入一次" autocomplete="new-password" @input="resetError" />
                  <AppIcon name="lock" />
                </div>
              </div>
              <button class="btn btn-primary btn-block auth-submit" type="submit" :disabled="busy">
                <span v-if="busy" class="spinner-light" />
                {{ busy ? '正在创建…' : '创建管理员并进入' }}
                <AppIcon v-if="!busy" name="arrow-right" />
              </button>
            </form>
            <div class="auth-foot">创建后启用登录验证。账号与权限在「用户与权限」中管理。</div>
          </template>

          <template v-else-if="!auth.enabled">
            <div class="auth-disabled">
              <span class="auth-disabled-icon"><AppIcon name="alert" :size="26" /></span>
              <strong>无法连接馆藏服务</strong>
              <p>{{ auth.error || '请确认服务已启动，或联系管理员后重试。' }}</p>
              <div class="auth-disabled-actions">
                <button class="btn btn-primary btn-sm" type="button" @click="retry">重新连接</button>
                <button class="btn btn-ghost btn-sm" type="button" @click="enterAnyway">仍然进入</button>
              </div>
            </div>
          </template>

          <template v-else-if="mode === 'register'">
            <div v-if="regSent" class="auth-sent">
              <span class="auth-disabled-icon"><AppIcon name="check-circle" :size="26" /></span>
              <strong>申请已提交</strong>
              <p>{{ regSent }}</p>
              <div class="auth-disabled-actions">
                <button class="btn btn-primary btn-sm" type="button" @click="backToLogin">返回登录</button>
              </div>
            </div>
            <template v-else>
              <form class="auth-form" @submit.prevent="doRegister">
                <div class="field">
                  <label for="reg-username">要开通的账号</label>
                  <div class="input-icon">
                    <input id="reg-username" v-model="regForm.username" class="input" placeholder="英文或数字，登录时使用" autocomplete="username" @input="resetError" />
                    <AppIcon name="users" />
                  </div>
                </div>
                <div class="field">
                  <label for="reg-password">密码</label>
                  <div class="input-icon">
                    <input id="reg-password" v-model="regForm.password" class="input" :type="showPassword ? 'text' : 'password'" placeholder="至少 6 位" autocomplete="new-password" @input="resetError" />
                    <AppIcon name="lock" />
                    <button class="auth-eye" type="button" :aria-label="showPassword ? '隐藏密码' : '显示密码'" @click="showPassword = !showPassword">
                      <AppIcon :name="showPassword ? 'eye-off' : 'eye'" />
                    </button>
                  </div>
                </div>
                <div class="field">
                  <label for="reg-password2">确认密码</label>
                  <div class="input-icon">
                    <input id="reg-password2" v-model="regForm.password2" class="input" :type="showPassword ? 'text' : 'password'" placeholder="再输入一次" autocomplete="new-password" @input="resetError" />
                    <AppIcon name="lock" />
                  </div>
                </div>
                <div class="field">
                  <label for="reg-note">备注<span class="field-hint">选填</span></label>
                  <div class="input-icon">
                    <input id="reg-note" v-model="regForm.note" class="input" maxlength="256" placeholder="如：初一(3)班 语文老师" />
                    <AppIcon name="edit" />
                  </div>
                </div>
                <button class="btn btn-primary btn-block auth-submit" type="submit" :disabled="busy">
                  <span v-if="busy" class="spinner-light" />
                  {{ busy ? '正在提交…' : '提交申请' }}
                  <AppIcon v-if="!busy" name="arrow-right" />
                </button>
              </form>
              <div class="auth-foot">
                账号经管理员审核通过后生效。<br />
                <button class="auth-link" type="button" @click="backToLogin">返回登录</button>
              </div>
            </template>
          </template>

          <template v-else>
            <form class="auth-form" @submit.prevent="doLogin">
              <div class="field">
                <label for="login-username">账号</label>
                <div class="input-icon">
                  <input id="login-username" v-model="form.username" class="input" placeholder="馆员账号" autocomplete="username" @input="resetError" />
                  <AppIcon name="users" />
                </div>
              </div>
              <div class="field">
                <label for="login-password">密码</label>
                <div class="input-icon">
                  <input id="login-password" v-model="form.password" class="input" :type="showPassword ? 'text' : 'password'" placeholder="登录密码" autocomplete="current-password" @input="resetError" />
                  <AppIcon name="lock" />
                  <button class="auth-eye" type="button" :aria-label="showPassword ? '隐藏密码' : '显示密码'" @click="showPassword = !showPassword">
                    <AppIcon :name="showPassword ? 'eye-off' : 'eye'" />
                  </button>
                </div>
              </div>
              <div class="auth-options">
                <label class="auth-check">
                  <input v-model="remember" type="checkbox" />
                  记住账号
                </label>
                <span>忘记密码请联系管理员</span>
              </div>
              <button class="btn btn-primary btn-block auth-submit" type="submit" :disabled="busy">
                <span v-if="busy" class="spinner-light" />
                {{ busy ? '正在登录…' : '进入工作台' }}
                <AppIcon v-if="!busy" name="arrow-right" />
              </button>
            </form>
            <div v-if="auth.registrationOpen" class="auth-register">
              尚未开通账号？
              <button class="auth-link" type="button" @click="startRegister">申请开通</button>
            </div>
            <div class="auth-foot">
              公用电脑使用结束后，请退出登录。<br />
              <span>JIANJI · 中小学图书馆工作系统</span>
            </div>
          </template>
        </template>
      </div>
    </main>
  </div>
</template>
