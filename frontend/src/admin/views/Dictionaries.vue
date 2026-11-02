<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import AppIcon from '../../components/AppIcon.vue'
import BaseModal from '../../components/BaseModal.vue'
import ConfirmDialog from '../../components/ConfirmDialog.vue'
import { can } from '../../lib/auth'
import { errorMessage, get, post, put } from '../../lib/api'
import { formatNumber } from '../../lib/format'
import { toast, toastError } from '../../lib/toast'

interface CodedItem { code: string; name: string }
type DictItem = string | CodedItem
interface DictType {
  key: string
  label: string
  kind: 'simple' | 'coded'
  items: DictItem[]
  count: number
  builtin_count: number
  customized: boolean
}

const USAGE: Record<string, string> = {
  locations: '用于馆藏地点、发货清单地点映射与书标筛选。',
  currencies: '用于定价与财产登记金额的币种口径，可设置本馆默认币种。',
  item_types: '用于编目记录的文献类型，在图书库与统计中展示。',
  acquisition: '用于记载图书入藏方式，供财产登记与统计使用。',
  languages: '用于编目语种候选，保持与 MARC 101 语种一致。',
  payment: '用于采购付款方式候选。',
  responsibility: '用于编目责任方式候选，如著、编、译。',
}

const canWrite = computed(() => can('dictionaries', 'write'))
const types = ref<DictType[]>([])
const selectedKey = ref('locations')
const draft = ref<DictItem[]>([])
const search = ref('')
const loading = ref(true)
const error = ref('')
const saving = ref(false)
const addValue = ref('')
const addCode = ref('')
const addName = ref('')
const resetOpen = ref(false)
const resetBusy = ref(false)
const importOpen = ref(false)
const importText = ref('')
const importError = ref('')
const defaultCurrency = ref('CNY')
const defaultBusy = ref(false)

const selected = computed(() => types.value.find((item) => item.key === selectedKey.value) || null)
const isCoded = computed(() => selected.value?.kind === 'coded')
const filtered = computed(() => {
  const text = search.value.trim().toLowerCase()
  if (!text) return draft.value
  return draft.value.filter((item) => {
    if (typeof item === 'string') return item.toLowerCase().includes(text)
    return item.code.toLowerCase().includes(text) || item.name.toLowerCase().includes(text)
  })
})
const dirty = computed(() => {
  if (!selected.value) return false
  return JSON.stringify(draft.value) !== JSON.stringify(selected.value.items)
})

function cloneItems(items: DictItem[]): DictItem[] {
  return items.map((item) => (typeof item === 'string' ? item : { code: item.code, name: item.name }))
}

function selectType(key: string) {
  selectedKey.value = key
  search.value = ''
  addValue.value = ''
  addCode.value = ''
  addName.value = ''
}

watch(selectedKey, () => {
  if (selected.value) draft.value = cloneItems(selected.value.items)
})

async function load() {
  loading.value = true
  error.value = ''
  try {
    const data = await get<{ types: DictType[] }>('/dictionaries')
    types.value = data.types
    if (!types.value.some((item) => item.key === selectedKey.value)) {
      selectedKey.value = types.value[0]?.key || 'locations'
    }
    draft.value = cloneItems(selected.value?.items || [])
    try {
      const currency = await get<{ code: string }>('/dictionaries/currencies/default')
      defaultCurrency.value = currency.code
    } catch {
      defaultCurrency.value = 'CNY'
    }
  } catch (e) {
    error.value = errorMessage(e)
  } finally {
    loading.value = false
  }
}

function addItem() {
  if (isCoded.value) {
    const code = addCode.value.trim()
    const name = addName.value.trim()
    if (!code || !name) {
      toastError('请填写代码与名称')
      return
    }
    if (draft.value.some((item) => typeof item !== 'string' && item.code === code)) {
      toastError('代码已存在')
      return
    }
    draft.value.push({ code, name })
    addCode.value = ''
    addName.value = ''
  } else {
    const value = addValue.value.trim()
    if (!value) return
    if (draft.value.some((item) => typeof item === 'string' && item === value)) {
      toastError('该条目已存在')
      return
    }
    draft.value.push(value)
    addValue.value = ''
  }
}

function removeItem(index: number) {
  const actual = filtered.value[index]
  const at = draft.value.findIndex((item) => item === actual)
  if (at >= 0) draft.value.splice(at, 1)
}

async function save() {
  if (!selected.value || saving.value) return
  const items = draft.value
    .map((item) => (typeof item === 'string' ? item.trim() : { code: item.code.trim(), name: item.name.trim() }))
    .filter((item) => (typeof item === 'string' ? item : item.code && item.name))
  if (!items.length) {
    toastError('字典至少保留一个条目')
    return
  }
  saving.value = true
  try {
    const data = await put<{ items: DictItem[]; count: number }>(`/dictionaries/${selectedKey.value}`, { items })
    selected.value.items = data.items
    selected.value.count = data.count
    selected.value.customized = true
    draft.value = cloneItems(data.items)
    toast('字典已保存')
  } catch (e) {
    toastError(errorMessage(e))
  } finally {
    saving.value = false
  }
}

function askReset() {
  resetOpen.value = true
}

async function submitReset() {
  if (!selected.value) return
  resetBusy.value = true
  try {
    const data = await post<{ items: DictItem[]; count: number }>(`/dictionaries/${selectedKey.value}/reset`)
    selected.value.items = data.items
    selected.value.count = data.count
    selected.value.customized = false
    draft.value = cloneItems(data.items)
    resetOpen.value = false
    toast('已恢复默认字典')
  } catch (e) {
    toastError(errorMessage(e))
  } finally {
    resetBusy.value = false
  }
}

async function saveDefaultCurrency() {
  defaultBusy.value = true
  try {
    const data = await put<{ code: string; name: string }>('/dictionaries/currencies/default', {
      code: defaultCurrency.value,
    })
    toast(`默认币种已设为 ${data.name}`)
  } catch (e) {
    toastError(errorMessage(e))
  } finally {
    defaultBusy.value = false
  }
}

function openImport() {
  importText.value = ''
  importError.value = ''
  importOpen.value = true
}

function submitImport() {
  importError.value = ''
  const lines = importText.value.split(/\r?\n/).map((line) => line.trim()).filter(Boolean)
  if (!lines.length) {
    importError.value = '没有可导入的条目'
    return
  }
  let added = 0
  let updated = 0
  for (const line of lines) {
    const parts = line.split(/[,，\t]/).map((part) => part.trim())
    if (isCoded.value) {
      const code = parts[0]
      const name = parts[1] || parts[0]
      if (!code) continue
      const existing = draft.value.find((item) => typeof item !== 'string' && item.code === code)
      if (existing && typeof existing !== 'string') {
        existing.name = name
        updated += 1
      } else {
        draft.value.push({ code, name })
        added += 1
      }
    } else {
      if (!parts[0]) continue
      if (draft.value.some((item) => item === parts[0])) continue
      draft.value.push(parts[0])
      added += 1
    }
  }
  importOpen.value = false
  toast(`已加入草稿：新增 ${added} 条${updated ? `，更新 ${updated} 条` : ''}。保存后生效`)
}

onMounted(load)
</script>

<template>
  <section class="view">
    <div v-if="loading" class="card" aria-busy="true">
      <div class="skeleton" style="height: 380px; margin: 16px" />
    </div>

    <div v-else-if="error" class="card dash-error">
      <span class="error-icon"><AppIcon name="alert" /></span>
      <strong>无法读取字典</strong>
      <p>{{ error }}</p>
      <button class="btn btn-primary btn-sm" type="button" @click="load">重新加载</button>
    </div>

    <div v-else class="dict-layout">
      <aside class="card dict-types">
        <div class="card-head">
          <div>
            <div class="card-title">字典类型</div>
            <div class="card-sub">改动保存后立即影响编目候选</div>
          </div>
        </div>
        <div class="dict-type-list">
          <button
            v-for="type in types"
            :key="type.key"
            class="dict-type-btn"
            :class="{ 'is-active': selectedKey === type.key }"
            type="button"
            @click="selectType(type.key)"
          >
            <span>
              <strong>{{ type.label }}</strong>
              <small>{{ type.customized ? '已自定义' : '默认值' }}</small>
            </span>
            <span class="dict-type-count">{{ formatNumber(type.count) }}</span>
          </button>
        </div>
      </aside>

      <article v-if="selected" class="data-card">
        <div class="card-head">
          <div>
            <div class="card-title">{{ selected.label }}</div>
            <div class="card-sub">{{ USAGE[selected.key] || '供编目与业务表单使用。' }}</div>
          </div>
          <div class="card-head-actions">
            <span v-if="dirty" class="badge badge-warn no-dot">有未保存修改</span>
            <span v-else class="badge badge-neutral no-dot">{{ selected.customized ? '已自定义' : '默认值' }}</span>
          </div>
        </div>

        <div class="toolbar">
          <div class="input-icon search-field">
            <input v-model="search" class="input" :placeholder="isCoded ? '搜索代码或名称' : '搜索条目'" autocomplete="off">
            <AppIcon name="search" />
          </div>
          <template v-if="selected.key === 'currencies'">
            <select v-model="defaultCurrency" class="select" aria-label="默认币种">
              <option v-for="item in draft" :key="typeof item === 'string' ? item : item.code" :value="typeof item === 'string' ? item : item.code">
                {{ typeof item === 'string' ? item : `${item.code} · ${item.name}` }}
              </option>
            </select>
            <button class="btn btn-outline btn-sm" type="button" :disabled="!canWrite || defaultBusy" @click="saveDefaultCurrency">
              设为默认
            </button>
          </template>
          <span class="toolbar-spacer" />
          <span class="toolbar-count">{{ formatNumber(filtered.length) }} 条</span>
          <button v-if="canWrite" class="btn btn-outline btn-sm" type="button" @click="openImport">
            <AppIcon name="upload" />批量导入
          </button>
          <button v-if="canWrite" class="btn btn-ghost btn-sm" type="button" :disabled="!selected.customized" @click="askReset">
            恢复默认
          </button>
          <button v-if="canWrite" class="btn btn-primary btn-sm" type="button" :disabled="saving" @click="save">
            {{ saving ? '保存中…' : '保存字典' }}
          </button>
        </div>

        <div class="dict-add-row">
          <template v-if="isCoded">
            <input v-model="addCode" class="input" style="width: 150px" maxlength="32" placeholder="代码，如 CNY">
            <input v-model="addName" class="input" style="flex: 1" maxlength="64" placeholder="名称，如 人民币" @keydown.enter.prevent="addItem">
          </template>
          <input v-else v-model="addValue" class="input" style="flex: 1" maxlength="64" placeholder="输入新条目" @keydown.enter.prevent="addItem">
          <button v-if="canWrite" class="btn btn-outline btn-sm" type="button" @click="addItem">
            <AppIcon name="plus" />添加
          </button>
        </div>

        <div v-if="isCoded" class="table-wrap">
          <table class="table">
            <thead><tr><th>代码</th><th>名称</th><th>操作</th></tr></thead>
            <tbody>
              <tr v-if="!filtered.length"><td colspan="3" class="table-state">当前没有匹配的条目</td></tr>
              <template v-else>
                <tr v-for="(item, index) in filtered" :key="typeof item === 'string' ? item : item.code">
                  <template v-if="typeof item !== 'string'">
                    <td><input v-model="item.code" class="input dict-inline-input" maxlength="32" :disabled="!canWrite"></td>
                    <td><input v-model="item.name" class="input dict-inline-input" maxlength="64" :disabled="!canWrite"></td>
                    <td>
                      <button v-if="canWrite" class="text-link" type="button" @click="removeItem(index)">移除</button>
                    </td>
                  </template>
                </tr>
              </template>
            </tbody>
          </table>
        </div>

        <div v-else class="dict-simple-list">
          <div v-if="!filtered.length" class="empty-card">当前没有匹配的条目</div>
          <div v-for="(item, index) in filtered" :key="`${item}-${index}`" class="dict-simple-row">
            <span>{{ item }}</span>
            <button v-if="canWrite" class="text-link" type="button" @click="removeItem(index)">移除</button>
          </div>
        </div>
      </article>
    </div>

    <ConfirmDialog
      :open="resetOpen"
      title="恢复默认字典"
      :message="`确认将「${selected?.label}」恢复为默认值？当前自定义条目将被覆盖。`"
      confirm-text="恢复默认"
      danger
      :busy="resetBusy"
      @cancel="resetOpen = false"
      @confirm="submitReset"
    />

    <BaseModal :open="importOpen" :title="`批量导入 · ${selected?.label || ''}`" subtitle="导入内容先加入草稿，保存后生效" size="sm" @close="importOpen = false">
      <div class="notice-strip">
        <AppIcon name="info" />
        <span>{{ isCoded ? '每行格式：代码,名称。重复代码会更新名称。' : '每行一个条目，自动去重。' }}</span>
      </div>
      <div class="field">
        <label for="dict-import">粘贴条目</label>
        <textarea id="dict-import" v-model="importText" class="textarea barcode-textarea" rows="7" :placeholder="isCoded ? 'CNY,人民币' : '2F 社科区'" />
      </div>
      <p v-if="importError" class="field-note is-error" style="margin-top: 10px" role="alert">{{ importError }}</p>
      <template #footer>
        <button class="btn btn-ghost" type="button" @click="importOpen = false">取消</button>
        <button class="btn btn-primary" type="button" @click="submitImport">加入草稿</button>
      </template>
    </BaseModal>
  </section>
</template>
