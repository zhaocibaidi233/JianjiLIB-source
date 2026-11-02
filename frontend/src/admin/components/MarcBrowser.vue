<template>
  <article class="card">
    <div class="card-head">
      <div>
        <div class="card-title">MARC 文件浏览</div>
        <div class="card-sub">
          打开一个 .iso / .mrc 文件，逐条查看字段、字段统计与解析错误。<b>只读，不写库。</b>
        </div>
      </div>
      <div class="mb-head-actions">
        <button v-if="session" class="btn btn-ghost btn-sm" type="button" :disabled="busy" @click="closeFile">
          关闭文件
        </button>
        <button class="btn btn-outline btn-sm" type="button" :disabled="busy" @click="pickFile">
          <AppIcon name="upload" />{{ session ? '换一个文件' : '选择文件' }}
        </button>
      </div>
    </div>

    <div class="card-body">
      <input ref="fileInput" type="file" accept=".iso,.mrc,.mrk,.txt,.dat" hidden @change="onPick" />

      <div v-if="error" class="mb-error">{{ error }}</div>

      <div v-if="busy" class="mb-state">
        <span class="spinner" />正在解析文件…（一万多条大约要几秒）
      </div>

      <div v-else-if="!session" class="mb-state mb-state--empty">
        <AppIcon name="book-open" />
        <strong>当前没有打开的文件</strong>
        <p>选择 MARC 文件后在此查看内容，无需先导入。</p>
      </div>

      <template v-else>
        <div class="mb-summary">
          <span class="mb-chip"><b>{{ session.filename }}</b></span>
          <span class="mb-chip">{{ formatSize(session.size) }}</span>
          <span class="mb-chip mb-chip--ok">{{ session.total }} 条记录</span>
          <span class="mb-chip" :class="{ 'mb-chip--warn': session.parse_error_count }">
            解析错误 {{ session.parse_error_count }}
          </span>
          <span class="mb-chip">{{ session.field_stats.length }} 种字段</span>
        </div>

        <details v-if="session.parse_error_count" class="mb-details">
          <summary>看解析错误（前 {{ session.parse_errors.length }} 条）</summary>
          <ul class="mb-err-list">
            <li v-for="(e, i) in session.parse_errors" :key="i">
              第 {{ e.index + 1 }} 条：{{ e.error }}
            </li>
          </ul>
        </details>

        <details class="mb-details">
          <summary>看字段统计（{{ session.field_stats.length }} 种）</summary>
          <div class="mb-stats">
            <span v-for="s in session.field_stats" :key="s.key" class="mb-stat">
              <code>{{ s.key }}</code><b>{{ s.count }}</b>
            </span>
          </div>
        </details>

        <div class="table-wrap mb-table">
          <table class="table">
            <thead>
              <tr>
                <th style="width: 34px" />
                <th style="width: 56px">序号</th>
                <th>题名</th>
                <th style="width: 158px">ISBN</th>
                <th style="width: 118px">责任者</th>
                <th style="width: 68px">出版年</th>
                <th style="width: 92px">分类号</th>
                <th style="width: 108px"
                  title="条码取自馆藏条码字段（905$b，旧数据在 905$h / 995$f）。书商文件若把登录号放在别的字段，需在导入时做映射，此处显示为空。">
                  条码</th>
              </tr>
            </thead>
            <tbody>
              <template v-for="row in session.records" :key="row.index">
                <tr class="mb-row" :class="{ 'is-open': openIndex === row.index }"
                  @click="toggleRow(row.index)">
                  <td>
                    <span class="mb-caret" :class="{ 'is-open': openIndex === row.index }">▸</span>
                  </td>
                  <td class="mb-num">{{ row.index }}</td>
                  <td class="mb-title" :title="row.title">{{ row.title || '（无题名）' }}</td>
                  <td class="mb-mono">{{ row.isbn || '—' }}</td>
                  <td class="mb-clip" :title="row.author">{{ row.author || '—' }}</td>
                  <td class="mb-mono">{{ row.year || '—' }}</td>
                  <td class="mb-mono">{{ row.class_no || '—' }}</td>
                  <td class="mb-mono">{{ row.barcode || '—' }}</td>
                </tr>
                <tr v-if="openIndex === row.index" class="mb-fields-row">
                  <td colspan="8">
                    <div class="mb-fields">
                      <div v-for="(f, fi) in row.rows" :key="fi" class="mb-field">
                        <span class="mb-tag">{{ f.tag }}</span>
                        <span class="mb-ind">{{ f.control ? '' : f.ind }}</span>
                        <span class="mb-value">
                          <template v-if="f.control">{{ f.text || '—' }}</template>
                          <template v-else>
                            <span v-for="(sf, si) in f.subfields" :key="si" class="mb-sf">
                              <i>${{ sf[0] }}</i>{{ sf[1] }}
                            </span>
                          </template>
                        </span>
                      </div>
                      <p v-if="!row.rows.length" class="field-note">这条记录没有任何字段。</p>
                    </div>
                  </td>
                </tr>
              </template>
            </tbody>
          </table>
        </div>

        <div class="mb-pager">
          <span class="mb-pager-info">
            第 {{ session.page }} / {{ pageCount }} 页 · 共 {{ session.total }} 条
          </span>
          <div class="mb-pager-actions">
            <button class="btn btn-ghost btn-sm" type="button"
              :disabled="session.page <= 1 || busy" @click="gotoPage(session.page - 1)">
              上一页
            </button>
            <button class="btn btn-ghost btn-sm" type="button"
              :disabled="session.page >= pageCount || busy" @click="gotoPage(session.page + 1)">
              下一页
            </button>
            <select v-model.number="pageSize" class="select mb-page-size" @change="reloadFirstPage">
              <option :value="20">每页 20 条</option>
              <option :value="50">每页 50 条</option>
              <option :value="100">每页 100 条</option>
            </select>
          </div>
        </div>
      </template>
    </div>
  </article>
</template>

<script setup lang="ts">
/**
 * MARC 文件浏览面板（全局设置 → MARC 浏览）。
 *
 * 定位：**导入前的"先看一眼"**。导入页的预览只给前 10 条 + 字段统计，
 * 想看第 3000 条长什么样就只能导进去；这个工具补的就是这一段——翻到任意一条，
 * 看它每个字段的原始内容，也能看出文件里混没混进脏记录。
 *
 * 解析在后端做一次并按内容哈希缓存（见 api/marc_browser.py），翻页只取内存切片；
 * 这里只负责把每页的记录画出来。
 */
import { computed, onBeforeUnmount, ref } from 'vue'
import { del, errorMessage, get, post } from '../../lib/api'
import AppIcon from '../../components/AppIcon.vue'

interface MarcRow {
  tag: string
  ind: string
  control: boolean
  subfields: Array<[string, string]>
  text: string
}

interface MarcRecord {
  index: number
  title: string
  isbn: string
  author: string
  publisher: string
  year: string
  class_no: string
  barcode: string
  rows: MarcRow[]
}

interface MarcSession {
  token: string
  filename: string
  size: number
  total: number
  parse_errors: Array<{ index: number; error: string }>
  parse_error_count: number
  field_stats: Array<{ key: string; count: number }>
  page: number
  page_size: number
  records: MarcRecord[]
}

const fileInput = ref<HTMLInputElement | null>(null)
const busy = ref(false)
const error = ref('')
const session = ref<MarcSession | null>(null)
const openIndex = ref<number | null>(null)
const pageSize = ref(20)

const pageCount = computed(() => {
  if (!session.value) return 1
  return Math.max(1, Math.ceil(session.value.total / session.value.page_size))
})

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

function pickFile() {
  error.value = ''
  fileInput.value?.click()
}

async function onPick(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''            // 允许再次选同一个文件
  if (!file) return
  busy.value = true
  error.value = ''
  openIndex.value = null
  try {
    const form = new FormData()
    form.append('file', file)
    session.value = await post<MarcSession>('/marc/browse', form, {
      params: { page_size: pageSize.value },
    })
  } catch (e) {
    session.value = null
    error.value = errorMessage(e)
  } finally {
    busy.value = false
  }
}

async function gotoPage(page: number) {
  if (!session.value) return
  busy.value = true
  error.value = ''
  openIndex.value = null
  try {
    const data = await get<MarcSession>(`/marc/browse/${session.value.token}`, {
      page, page_size: session.value.page_size,
    })
    session.value = { ...session.value, ...data }
  } catch (e) {
    error.value = errorMessage(e)
  } finally {
    busy.value = false
  }
}

/**
 * 换每页条数：先把新值写回会话，再回第 1 页。
 *
 * 注意不能只改下拉框绑的 `pageSize`——请求用的是 `session.page_size`，
 * 两者不同步的话，下拉框变了、表格还是 20 行（真机测出来的）。
 */
async function reloadFirstPage() {
  if (!session.value) return
  session.value.page_size = pageSize.value
  await gotoPage(1)
}

function toggleRow(index: number) {
  openIndex.value = openIndex.value === index ? null : index
}

async function closeFile() {
  const token = session.value?.token
  session.value = null
  openIndex.value = null
  if (token) {
    // 主动释放后端缓存；失败也无所谓，30 分钟后自己过期
    try { await del(`/marc/browse/${token}`) } catch { /* 忽略 */ }
  }
}

// 离开设置页时顺手释放，别把十几 MB 的解析结果一直挂在服务端内存里
onBeforeUnmount(() => {
  const token = session.value?.token
  if (token) { void del(`/marc/browse/${token}`).catch(() => undefined) }
})
</script>

<style scoped>
.mb-head-actions {
  display: flex;
  gap: 8px;
  align-items: center;
}
.mb-state {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 18px 4px;
  color: var(--text-secondary);
  font-size: 13px;
}
.mb-state--empty {
  flex-direction: column;
  gap: 6px;
  padding: 32px 16px;
  text-align: center;
  border: 1px dashed var(--border);
  border-radius: 10px;
}
.mb-state--empty p {
  margin: 0;
  font-size: 12px;
  color: var(--text-tertiary);
}
.mb-error {
  margin-bottom: 12px;
  padding: 10px 12px;
  font-size: 13px;
  color: var(--danger, #b54540);
  background: var(--danger-soft, #fdf0ef);
  border-radius: 8px;
}
.mb-summary {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 12px;
}
.mb-chip {
  padding: 3px 9px;
  font-size: 12px;
  color: var(--text-secondary);
  background: var(--surface-sunken, #f4f6f2);
  border-radius: 999px;
}
.mb-chip b {
  color: var(--text-primary);
}
.mb-chip--ok {
  color: #2f6f4e;
  background: #eaf5ee;
}
.mb-chip--warn {
  color: #8a5a1b;
  background: #fbf1e0;
}
.mb-details {
  margin-bottom: 10px;
  font-size: 12px;
}
.mb-details summary {
  cursor: pointer;
  color: var(--text-secondary);
  padding: 4px 0;
}
.mb-err-list {
  margin: 6px 0 0;
  padding-left: 18px;
  color: var(--danger, #b54540);
  line-height: 1.7;
}
.mb-stats {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 6px;
  max-height: 150px;
  overflow-y: auto;
}
.mb-stat {
  display: inline-flex;
  gap: 4px;
  padding: 2px 7px;
  font-size: 11px;
  background: var(--surface-sunken, #f4f6f2);
  border-radius: 5px;
}
.mb-stat code {
  color: var(--text-primary);
}
.mb-stat b {
  color: var(--text-tertiary);
  font-weight: 500;
}
.mb-table {
  max-height: 520px;
  overflow: auto;
}
/* 面板本身不宽（设置页是两栏），表格给一个下限宽度，宁可横向滚也别把
   题名挤成竖排——第一版就踩了这个，题名一个字一行。 */
.mb-table table {
  min-width: 860px;
}
.mb-row {
  cursor: pointer;
}
.mb-row:hover td {
  background: var(--surface-hover, #f7f9f6);
}
.mb-row.is-open td {
  background: var(--surface-hover, #f7f9f6);
}
.mb-caret {
  display: inline-block;
  color: var(--text-tertiary);
  transition: transform 0.15s;
}
.mb-caret.is-open {
  transform: rotate(90deg);
}
.mb-num {
  color: var(--text-tertiary);
  font-variant-numeric: tabular-nums;
}
.mb-mono {
  font-variant-numeric: tabular-nums;
}
.mb-title,
.mb-clip {
  font-weight: 500;
  max-width: 0;              /* 配合 table-layout:auto，让列按剩余空间收敛 */
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.mb-clip {
  font-weight: 400;
}
.mb-fields-row td {
  padding: 0;
  background: var(--surface-sunken, #f7f9f6);
}
.mb-fields {
  padding: 10px 14px 12px 46px;
  font-size: 12px;
  line-height: 1.9;
}
.mb-field {
  display: flex;
  gap: 8px;
}
.mb-tag {
  flex: 0 0 34px;
  color: var(--text-tertiary);
  font-variant-numeric: tabular-nums;
}
.mb-ind {
  flex: 0 0 20px;
  color: var(--text-tertiary);
  white-space: pre;
}
.mb-value {
  flex: 1;
  word-break: break-all;
}
.mb-sf {
  margin-right: 10px;
}
.mb-sf i {
  margin-right: 1px;
  color: var(--accent, #4a7c59);
  font-style: normal;
  font-size: 11px;
}
.mb-pager {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: 12px;
}
.mb-pager-info {
  font-size: 12px;
  color: var(--text-secondary);
}
.mb-pager-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}
.mb-page-size {
  width: auto;
  height: 30px;
  font-size: 12px;
}
</style>
