<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import AppIcon from '../../components/AppIcon.vue'
import BookCover from '../../components/BookCover.vue'
import { can } from '../../lib/auth'
import { errorMessage, get, post } from '../../lib/api'
import { holdingLocationLabel, holdingStatusLabel, sourceLabel } from '../../lib/displayLabels'
import { formatDate, formatDateTime } from '../../lib/format'
import { readerLevelLabel } from '../../legacy/readerLevel'
import { toast, toastError } from '../../lib/toast'

interface MarcField {
  tag: string
  i1?: string
  i2?: string
  data?: string
  sf?: Array<[string, string]>
}
interface Finding {
  rule_id: string
  level: 'ERROR' | 'WARNING' | 'INFO'
  message: string
  tag: string
  rule_name: string
  hint: string
  plain: string
}
interface RecordDetail {
  id: number
  library_id: number
  leader: string
  fields: MarcField[]
  source: string
  created_by: string | null
  created_at: string | null
  updated_at: string | null
  shelf_no: string | null
  volume: string | null
  item_type: string | null
  reader_level: string | null
  search: {
    isbn_norm: string | null
    title_norm: string | null
    title_pinyin_initials: string | null
    author_norm: string | null
    year: number | null
    class_no: string | null
  }
  findings: Finding[]
  mrk: string
  barcodes: string[]
}

interface Holding {
  id: number
  barcode: string | null
  call_no: string | null
  location: string | null
  volume: string | null
  status: string
  withdraw_no: string | null
  withdraw_reason: string | null
  withdrawn_at: string | null
  withdrawn_by: string | null
}

interface DictionaryItem {
  code: string
  name: string
}

const route = useRoute()
const recordId = computed(() => Number(route.params.id))
const record = ref<RecordDetail | null>(null)
const holdings = ref<Holding[]>([])
const itemTypes = ref<DictionaryItem[]>([])
const loading = ref(true)
const checking = ref(false)      // 「重新质检」进行中
const error = ref('')
const showMarcText = ref(false)
const holdingsRef = ref<HTMLElement | null>(null)
const canWrite = computed(() => can('records', 'write'))

function first(tag: string, code: string): string {
  for (const field of record.value?.fields || []) {
    if (String(field.tag) !== tag) continue
    for (const pair of field.sf || []) {
      if (String(pair[0]) !== code) continue
      const value = String(pair[1] || '').trim()
      if (value) return value
    }
  }
  return ''
}

const title = computed(() => record.value?.search.title_norm || '未知题名')
const author = computed(() => record.value?.search.author_norm || '佚名')
const publisher = computed(() => first('210', 'c'))
const place = computed(() => first('210', 'a'))
const edition = computed(() => first('205', 'a'))
const pages = computed(() => first('215', 'a'))
const size = computed(() => first('215', 'd'))
const price = computed(() => first('010', 'd'))
const itemTypeName = computed(() => {
  const raw = record.value?.item_type?.trim()
  if (!raw) return ''
  return itemTypes.value.find((item) => item.code === raw)?.name || raw
})
const callNo = computed(() => holdings.value.find((item) => item.call_no)?.call_no || '—')

const availableCount = computed(() => holdings.value.filter((item) => item.status === 'AVAILABLE').length)
const inactiveCount = computed(() => holdings.value.filter((item) => item.status === 'WITHDRAWN' || item.status === 'LOST').length)

const levelMeta: Record<string, { label: string; cls: string }> = {
  ERROR: { label: '错误', cls: 'badge-danger' },
  WARNING: { label: '提醒', cls: 'badge-warn' },
  INFO: { label: '建议', cls: 'badge-neutral' },
}

const errorCount = computed(() => record.value?.findings.filter((item) => item.level === 'ERROR').length || 0)
const warnCount = computed(() => record.value?.findings.filter((item) => item.level === 'WARNING').length || 0)

function subfields(field: MarcField): string {
  if (field.data) return field.data
  return (field.sf || [])
    .map((pair) => `$${(pair as Array<string>)[0]} ${(pair as Array<string>)[1]}`)
    .join('  ')
}

function holdingStatus(status: string): { label: string; cls: string } {
  if (status === 'AVAILABLE') return { label: holdingStatusLabel(status), cls: 'ok' }
  // 停流通/另库保存（D14）：不是注销，用 warn（橙点）与已注销/已遗失区分开
  if (status === 'SUSPENDED') return { label: holdingStatusLabel(status), cls: 'warn' }
  if (status === 'WITHDRAWN') return { label: holdingStatusLabel(status), cls: 'muted' }
  if (status === 'LOST') return { label: holdingStatusLabel(status), cls: 'danger' }
  return { label: holdingStatusLabel(status), cls: 'muted' }
}

/**
 * 重新质检（`POST /records/{id}/validate`）。
 *
 * 保存时只对"当次提交的字段"跑一次质检，之后记录被转库、被导入、被别人改过，
 * 页面上这份 findings 就是旧的。这个按钮让"这一条**现在**有什么问题"随时可查
 * —— 与「数据体检」不同，那边扫的是全库某类问题，这里只看这一条。
 *
 * 接口一直是有的，但前端从没调用过（本次补的是入口，后端零改动）。
 */
async function revalidate() {
  if (checking.value || !record.value) return
  checking.value = true
  try {
    const data = await post<{ id: number; findings: Finding[] }>(
      `/records/${record.value.id}/validate`)
    const list = data.findings ?? []
    record.value.findings = list
    toast(list.length ? `质检完成：${list.length} 条提示` : '质检完成：未发现问题',
      list.length ? 'info' : 'success')
  } catch (e) {
    toastError(errorMessage(e))
  } finally {
    checking.value = false
  }
}

async function load() {
  if (!Number.isFinite(recordId.value)) {
    error.value = '书目编号无效'
    loading.value = false
    return
  }
  loading.value = true
  error.value = ''
  try {
    record.value = await get<RecordDetail>(`/records/${recordId.value}`)
    const [data, dictionary] = await Promise.all([
      get<{ holdings: Holding[] }>(`/records/${recordId.value}/holdings`),
      get<{ items: Array<DictionaryItem | string> }>('/dictionaries/item_types')
        .catch(() => ({ items: [] as Array<DictionaryItem | string> })),
    ])
    holdings.value = data.holdings
    itemTypes.value = dictionary.items.map((item) => (
      typeof item === 'string' ? { code: item, name: item } : item
    ))
    if (route.query.tab === 'holdings') {
      await nextTick()
      holdingsRef.value?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }
  } catch (e) {
    record.value = null
    holdings.value = []
    itemTypes.value = []
    error.value = errorMessage(e)
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <section class="view">
    <div class="page-tools">
      <RouterLink class="text-link" to="/admin/records">
        <AppIcon name="arrow-left" />图书库
      </RouterLink>
      <span class="spacer" />
      <RouterLink v-if="canWrite" class="btn btn-primary btn-sm" :to="`/admin/records/${recordId}/edit`">
        <AppIcon name="edit" />编辑书目
      </RouterLink>
    </div>

    <div v-if="loading" class="card" aria-busy="true">
      <div class="skeleton" style="height: 180px; margin: 16px" />
      <div class="skeleton" style="height: 300px; margin: 16px" />
    </div>

    <div v-else-if="error" class="card dash-error">
      <span class="error-icon"><AppIcon name="alert" /></span>
      <strong>无法读取书目</strong>
      <p>{{ error }}</p>
      <button class="btn btn-primary btn-sm" type="button" @click="load">重新加载</button>
    </div>

    <template v-else-if="record">
      <article class="card">
        <div class="record-detail-head">
          <div class="rd-cover">
            <BookCover :title="title" :author="author" size="card" />
          </div>
          <div class="record-detail-info">
            <h2>{{ title }}</h2>
            <div class="rd-author">{{ author }}</div>
            <div class="rd-meta">
              <span v-if="publisher">{{ publisher }}<template v-if="place"> · {{ place }}</template></span>
              <span v-if="record.search.year">{{ record.search.year }}</span>
              <span v-if="record.search.isbn_norm">ISBN {{ record.search.isbn_norm }}</span>
              <span v-if="record.search.class_no">分类 {{ record.search.class_no }}</span>
              <span v-if="itemTypeName">{{ itemTypeName }}</span>
            </div>
            <div class="rd-tags">
              <span class="badge badge-primary no-dot">{{ sourceLabel(record.source) }}</span>
              <span v-if="record.created_by" class="badge badge-neutral no-dot">编目员 {{ record.created_by }}</span>
              <span v-if="record.volume" class="badge badge-neutral no-dot">卷册 {{ record.volume }}</span>
              <span v-if="record.shelf_no" class="badge badge-neutral no-dot">排架号 {{ record.shelf_no }}</span>
            </div>
          </div>
          <div class="row-actions">
            <button class="btn btn-outline btn-sm" type="button" @click="showMarcText = !showMarcText">
              <AppIcon name="file" />{{ showMarcText ? '隐藏 MARC 文本' : '查看 MARC 文本' }}
            </button>
          </div>
        </div>
        <div class="record-side-stats">
          <div class="record-side-stat"><strong>{{ holdings.length }}</strong><span>馆藏总册</span></div>
          <div class="record-side-stat"><strong>{{ availableCount }}</strong><span>在架可借</span></div>
          <div class="record-side-stat"><strong>{{ inactiveCount }}</strong><span>注销 / 遗失</span></div>
        </div>
        <div v-if="showMarcText" style="padding: 0 22px 22px">
          <pre class="record-mrk">{{ record.mrk }}</pre>
        </div>
      </article>

      <div class="profile-layout">
        <div class="profile-main">
          <article ref="holdingsRef" class="card">
            <div class="card-head">
              <div>
                <div class="card-title">馆藏与条码</div>
                <div class="card-sub">共 {{ holdings.length }} 册 · 在架 {{ availableCount }} 册</div>
              </div>
            </div>
            <div class="table-wrap holdings-table">
              <table class="table">
                <thead>
                  <tr><th>条码</th><th>索书号</th><th>馆藏地点</th><th>状态</th><th>注销信息</th></tr>
                </thead>
                <tbody>
                  <tr v-if="!holdings.length">
                    <td colspan="5" class="table-state">该书目当前没有馆藏</td>
                  </tr>
                  <template v-else>
                    <tr v-for="item in holdings" :key="item.id">
                    <td class="num">{{ item.barcode || '—' }}</td>
                    <td>{{ item.call_no || '—' }}</td>
                    <td>{{ holdingLocationLabel(item.location, record.shelf_no) }}</td>
                    <td>
                      <span class="status-text" :class="holdingStatus(item.status).cls">
                        <span class="dot" />{{ holdingStatus(item.status).label }}
                      </span>
                    </td>
                    <td class="cell-date">
                      <template v-if="item.withdraw_no">
                        {{ item.withdraw_no }} · {{ item.withdraw_reason || '—' }}
                        <div class="cell-sub">{{ formatDate(item.withdrawn_at) }} {{ item.withdrawn_by || '' }}</div>
                      </template>
                      <template v-else>—</template>
                    </td>
                  </tr>
                  </template>
                </tbody>
              </table>
            </div>
          </article>

          <article class="card">
            <div class="card-head">
              <div>
                <div class="card-title">MARC 字段</div>
                <div class="card-sub">CNMARC · 共 {{ record.fields.length }} 个字段</div>
              </div>
            </div>
            <div class="card-body">
              <div class="marc-list">
                <div v-for="(field, index) in record.fields" :key="`${field.tag}-${index}`" class="marc-row">
                  <span class="tag">{{ field.tag }}</span>
                  <span class="ind">{{ field.data ? '    ' : `${field.i1 || ' '}${field.i2 || ' '}` }}</span>
                  <span class="value">{{ subfields(field) }}</span>
                </div>
              </div>
            </div>
          </article>
        </div>

        <aside class="profile-side">
          <article class="card record-meta-card">
            <div class="card-head">
              <div class="card-title">出版信息</div>
            </div>
            <div class="card-body">
              <div class="info-line"><span>出版者</span><strong>{{ publisher || '—' }}</strong></div>
              <div class="info-line"><span>出版地</span><strong>{{ place || '—' }}</strong></div>
              <div class="info-line"><span>版次</span><strong>{{ edition || '—' }}</strong></div>
              <div class="info-line"><span>页数</span><strong>{{ pages || '—' }}</strong></div>
              <div class="info-line"><span>尺寸</span><strong>{{ size || '—' }}</strong></div>
              <div class="info-line"><span>定价</span><strong>{{ price || '—' }}</strong></div>
              <div class="info-line"><span>索书号</span><strong>{{ callNo }}</strong></div>
              <div class="info-line"><span>读者对象</span><strong>{{ readerLevelLabel(record.reader_level) }}</strong></div>
            </div>
          </article>

          <article class="card">
            <div class="card-head">
              <div class="card-title">编目信息</div>
            </div>
            <div class="card-body">
              <div class="info-line"><span>记录编号</span><strong>{{ record.id }}</strong></div>
              <div class="info-line"><span>来源</span><strong>{{ sourceLabel(record.source) }}</strong></div>
              <div class="info-line"><span>编目员</span><strong>{{ record.created_by || '—' }}</strong></div>
              <div class="info-line"><span>创建时间</span><strong>{{ formatDateTime(record.created_at) || '—' }}</strong></div>
              <div class="info-line"><span>更新时间</span><strong>{{ formatDateTime(record.updated_at) || '—' }}</strong></div>
              <div class="info-line"><span>拼音检索</span><strong>{{ record.search.title_pinyin_initials || '—' }}</strong></div>
            </div>
          </article>

          <article class="card">
            <div class="card-head">
              <div>
                <div class="card-title">质检结果</div>
                <div class="card-sub">
                  <template v-if="record.findings.length">{{ record.findings.length }} 条提示 · 错误 {{ errorCount }} · 提醒 {{ warnCount }}</template>
                  <template v-else>未发现问题</template>
                </div>
              </div>
              <button class="btn btn-ghost btn-sm" type="button" :disabled="checking"
                title="按当前字段重新跑一遍质检规则" @click="revalidate">
                <span v-if="checking" class="spinner" />
                <AppIcon v-else name="refresh" />
                {{ checking ? '质检中…' : '重新质检' }}
              </button>
            </div>
            <div class="card-body">
              <div v-if="record.findings.length" class="finding-list">
                <div v-for="item in record.findings" :key="`${item.rule_id}-${item.tag}`" class="finding-row">
                  <span class="badge no-dot" :class="levelMeta[item.level]?.cls || 'badge-neutral'">
                    {{ levelMeta[item.level]?.label || item.level }}
                  </span>
                  <span><strong>{{ item.rule_name || item.rule_id }}</strong>：{{ item.plain || item.message }}</span>
                </div>
              </div>
              <div v-else class="empty-card" style="padding: 20px">
                <AppIcon name="check-circle" />
                该记录通过全部质检规则
              </div>
            </div>
          </article>
        </aside>
      </div>
    </template>
  </section>
</template>
