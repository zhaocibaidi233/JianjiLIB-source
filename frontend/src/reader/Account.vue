<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { RouterLink } from 'vue-router'
import AppIcon from '../components/AppIcon.vue'
import { errorMessage } from '../lib/api'
import { formatDate, formatMoney, formatNumber } from '../lib/format'
import { readerAuth, readerChangePassword, refreshReader } from '../lib/readerAuth'
import { toast } from '../lib/toast'

// 读者自助改密码。后端 `POST /api/portal/auth/password` 早就实现了（校验旧密码 →
// 改密 → 清掉该读者全部会话 → 换发新令牌），但一直没有入口 —— 这个页面就是那个入口。
// ⚠️ 后端会**换发令牌**，写回本地由 lib/readerAuth.ts 负责：不换的话下一个请求就 401。

const reader = computed(() => readerAuth.reader)
const busy = ref(false)
const error = ref('')
const form = reactive({ oldPassword: '', newPassword: '', confirm: '' })

onMounted(() => {
  // 资料卡上的"最近登录"会随本次登录变化，进页面时顺手取一次最新的
  void refreshReader()
})

async function submit() {
  if (busy.value) return
  const { oldPassword, newPassword, confirm } = form
  if (!oldPassword) { error.value = '请填写当前密码'; return }
  if (newPassword.length < 6) { error.value = '新密码至少 6 位'; return }
  if (newPassword.length > 128) { error.value = '新密码不能超过 128 位'; return }
  if (newPassword !== confirm) { error.value = '两次输入的新密码不一致'; return }
  if (newPassword === oldPassword) { error.value = '新密码不能与当前密码相同'; return }

  error.value = ''
  busy.value = true
  try {
    await readerChangePassword(oldPassword, newPassword)
    form.oldPassword = ''
    form.newPassword = ''
    form.confirm = ''
    toast('密码已修改，其它设备上的登录已失效', 'success')
  } catch (e) {
    error.value = errorMessage(e)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="reader-view">
    <header class="reader-page-head">
      <div>
        <h1>我的账号</h1>
        <p>{{ reader?.name }} · {{ reader?.card_no }} · {{ reader?.status_label }}</p>
      </div>
      <RouterLink class="btn btn-outline btn-sm" to="/reader/loans">
        <AppIcon name="books" />我的借阅
      </RouterLink>
    </header>

    <div class="reader-account-grid">
      <section class="card">
        <div class="card-head">
          <div>
            <div class="card-title">读者资料</div>
            <div class="card-sub">证件与借阅规则由图书馆维护，如需变更请联系馆员</div>
          </div>
        </div>
        <div class="card-body">
          <dl class="reader-facts">
            <div><dt>姓名</dt><dd>{{ reader?.name || '—' }}</dd></div>
            <div><dt>借书证号</dt><dd>{{ reader?.card_no || '—' }}</dd></div>
            <div><dt>读者类型</dt><dd>{{ reader?.reader_type || '—' }}</dd></div>
            <div><dt>班级</dt><dd>{{ reader?.class_name || reader?.grade || '—' }}</dd></div>
            <div><dt>可借册数</dt><dd>{{ formatNumber(reader?.rules.max_items || 0) }} 册</dd></div>
            <div><dt>借期 / 续借</dt><dd>{{ reader?.rules.loan_days || 0 }} 天 / {{ reader?.rules.max_renew || 0 }} 次</dd></div>
            <div><dt>当前外借</dt><dd>{{ formatNumber(reader?.stats.active || 0) }} 册</dd></div>
            <div v-if="reader?.fines.enabled">
              <dt>未缴罚款</dt>
              <dd :class="{ 'is-danger': (reader?.fines.unpaid || 0) > 0 }">
                {{ formatMoney(reader?.fines.unpaid || 0) }}
              </dd>
            </div>
            <div><dt>最近登录</dt><dd>{{ formatDate(reader?.account?.last_login_at) || '—' }}</dd></div>
          </dl>
        </div>
      </section>

      <section class="card">
        <div class="card-head">
          <div>
            <div class="card-title">修改密码</div>
            <div class="card-sub">修改后其它设备上的登录会立即失效</div>
          </div>
        </div>
        <div class="card-body">
          <div v-if="error" class="auth-error" role="alert">
            <AppIcon name="alert" />
            <span>{{ error }}</span>
          </div>
          <form class="reader-password-form" @submit.prevent="submit">
            <div class="field">
              <label for="pw-old">当前密码</label>
              <input id="pw-old" v-model="form.oldPassword" class="input" type="password"
                autocomplete="current-password" @input="error = ''" />
            </div>
            <div class="field">
              <label for="pw-new">新密码</label>
              <input id="pw-new" v-model="form.newPassword" class="input" type="password"
                placeholder="至少 6 位" autocomplete="new-password" @input="error = ''" />
            </div>
            <div class="field">
              <label for="pw-confirm">确认新密码</label>
              <input id="pw-confirm" v-model="form.confirm" class="input" type="password"
                autocomplete="new-password" @input="error = ''" />
            </div>
            <button class="btn btn-primary" type="submit" :disabled="busy">
              <span v-if="busy" class="spinner-light" />
              {{ busy ? '正在提交…' : '保存新密码' }}
            </button>
          </form>
          <p class="reader-account-hint">
            忘记密码请联系图书馆重置，读者门户不支持自助找回。
          </p>
        </div>
      </section>
    </div>
  </div>
</template>
