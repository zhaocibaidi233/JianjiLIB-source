<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import AppIcon from '../../components/AppIcon.vue'
import BookCover from '../../components/BookCover.vue'
import BaseModal from '../../components/BaseModal.vue'
import ConfirmDialog from '../../components/ConfirmDialog.vue'
import { can } from '../../lib/auth'
import { del, errorMessage, get, post, put } from '../../lib/api'
import { formatDate, formatMonthDay } from '../../lib/format'
import { toast, toastError } from '../../lib/toast'
import { libraryState, loadLibraries } from '../../stores/libraries'

type RecTab = 'curated' | 'popular' | 'new'

interface RecCard {
  record_id: number
  title: string | null
  author: string | null
  publisher: string | null
  year: number | null
  class_no: string | null
  isbn: string | null
  call_no: string | null
  shelf_no: string | null
  active_count: number
  available_count: number
  recommendation_id?: number
  note?: string | null
  sort_order?: number
  loan_count?: number
  created_at?: string | null
}

interface RecData {
  library_id: number
  library_name: string
  curated: RecCard[]
  popular: RecCard[]
  new_arrivals: RecCard[]
}

interface RecordRow {
  id: number
  title: string | null
  author: string | null
  class_no: string | null
  call_no: string | null
  active_count: number
  total_count: number
}

interface RecordList {
  total: number
  items: RecordRow[]
}

const canWrite = computed(() => can('circulation', 'write'))
const libraryId = computed(() => libraryState.currentId)
const tab = ref<RecTab>('popular')
const loading = ref(true)
const error = ref('')
const curated = ref<RecCard[]>([])
const popular = ref<RecCard[]>([])
const arrivals = ref<RecCard[]>([])
const busy = ref(false)

const TABS: Array<{ key: RecTab; label: string; desc: string }> = [
  { key: 'popular', label: '热门借阅', desc: '近 90 天外借次数自动统计' },
  { key: 'curated', label: '馆员精选', desc: '人工挑选并排序，优先呈现给读者' },
  { key: 'new', label: '新到图书', desc: '近 90 天入藏且当前可借' },
]

const visibleArrivals = computed(() => arrivals.value.slice(0, 8))

async function load() {
  loading.value = true
  error.value = ''
  try {
    const data = await get<RecData>('/recommendations', {
      library_id: libraryId.value || undefined,
    })
    curated.value = data.curated
    popular.value = data.popular
    arrivals.value = data.new_arrivals
    ensureSelection()
  } catch (e) {
    error.value = errorMessage(e)
    curated.value = []
    popular.value = []
    arrivals.value = []
  } finally {
    loading.value = false
  }
}

watch(libraryId, () => void load())

function tabCount(key: RecTab): number {
  return key === 'curated' ? curated.value.length : key === 'popular' ? popular.value.length : arrivals.value.length
}

/* ---------------- 预览 ---------------- */
const selectedId = ref<number | null>(null)

const previewCard = computed<RecCard | null>(() => {
  const id = selectedId.value
  if (id === null) return null
  return [...curated.value, ...popular.value, ...arrivals.value].find((card) => card.record_id === id) || null
})
const previewCuratedCard = computed(() =>
  curated.value.find((card) => card.record_id === selectedId.value) || null,
)
const previewIsCurated = computed(() => Boolean(previewCuratedCard.value))
const previewShelf = computed(() => {
  const card = previewCard.value
  if (!card) return '架位待定'
  return [card.shelf_no, card.call_no].filter(Boolean).join(' · ') || '架位待定'
})

/* 荐书卡：推荐语直接在横线上写 */
const noteEditing = ref(false)
const noteDraft = ref('')
const noteInput = ref<HTMLTextAreaElement | null>(null)

function startNoteEdit() {
  const card = previewCuratedCard.value
  if (!card?.recommendation_id || !canWrite.value || busy.value) return
  noteDraft.value = card.note || ''
  noteEditing.value = true
  nextTick(() => noteInput.value?.focus())
}

function cancelNoteEdit() {
  noteEditing.value = false
  noteDraft.value = ''
}

async function saveNote() {
  const card = previewCuratedCard.value
  if (!card?.recommendation_id || busy.value) return
  busy.value = true
  try {
    await put(`/recommendations/${card.recommendation_id}`, { note: noteDraft.value.trim() })
    toast('推荐语已保存')
    noteEditing.value = false
    await load()
  } catch (e) {
    toastError(errorMessage(e))
  } finally {
    busy.value = false
  }
}

watch(selectedId, () => {
  if (noteEditing.value) cancelNoteEdit()
})

function ensureSelection() {
  if (previewCard.value) return
  const first = curated.value[0] || popular.value[0] || arrivals.value[0]
  selectedId.value = first?.record_id ?? null
}

function select(card: RecCard) {
  selectedId.value = card.record_id
}

function inCurated(recordId: number): boolean {
  return curated.value.some((card) => card.record_id === recordId)
}

const maxLoan = computed(() => Math.max(1, ...popular.value.map((card) => card.loan_count || 0)))
function loanPercent(count?: number): number {
  return Math.max(6, Math.round(((count || 0) / maxLoan.value) * 100))
}

/* ---------------- Reorder ---------------- */
const draggingId = ref<number | null>(null)
const dragOverId = ref<number | null>(null)

function persistOrder(previous: RecCard[]) {
  const ids = curated.value.map((row) => row.recommendation_id).filter((id): id is number => Boolean(id))
  busy.value = true
  post('/recommendations/reorder', { ids })
    .catch((e) => {
      curated.value = previous
      toastError(errorMessage(e))
    })
    .finally(() => {
      busy.value = false
    })
}

function moveTo(from: number, to: number) {
  if (!canWrite.value || busy.value || from === to || from < 0 || to < 0 || to >= curated.value.length) return
  const previous = [...curated.value]
  const rows = [...curated.value]
  const [item] = rows.splice(from, 1)
  rows.splice(to, 0, item)
  curated.value = rows
  persistOrder(previous)
}

function move(index: number, direction: -1 | 1) {
  moveTo(index, index + direction)
}

function onDragStart(index: number, event: DragEvent) {
  if (!canWrite.value) return
  draggingId.value = curated.value[index]?.record_id ?? null
  event.dataTransfer?.setData('text/plain', String(index))
  if (event.dataTransfer) event.dataTransfer.effectAllowed = 'move'
}

function onDragOver(index: number, event: DragEvent) {
  if (draggingId.value === null) return
  event.preventDefault()
  dragOverId.value = curated.value[index]?.record_id ?? null
}

function onDrop(index: number) {
  const from = curated.value.findIndex((row) => row.record_id === draggingId.value)
  draggingId.value = null
  dragOverId.value = null
  if (from >= 0) moveTo(from, index)
}

function onDragEnd() {
  draggingId.value = null
  dragOverId.value = null
}

/* ---------------- 快捷加入精选 ---------------- */
async function quickAdd(card: RecCard) {
  if (!canWrite.value || busy.value || inCurated(card.record_id)) return
  busy.value = true
  try {
    const result = await post<{ message: string }>('/recommendations', {
      record_id: card.record_id,
      note: null,
    })
    toast(result.message)
    selectedId.value = card.record_id
    await load()
  } catch (e) {
    toastError(errorMessage(e))
  } finally {
    busy.value = false
  }
}

/* ---------------- Edit note ---------------- */
const editTarget = ref<RecCard | null>(null)
const editNote = ref('')
const editError = ref('')

function openEdit(card: RecCard) {
  editTarget.value = card
  editNote.value = card.note || ''
  editError.value = ''
}

async function submitEdit() {
  const card = editTarget.value
  if (!card?.recommendation_id) return
  busy.value = true
  try {
    await put(`/recommendations/${card.recommendation_id}`, { note: editNote.value.trim() })
    toast('推荐语已保存')
    editTarget.value = null
    await load()
  } catch (e) {
    editError.value = errorMessage(e)
  } finally {
    busy.value = false
  }
}

/* ---------------- Remove ---------------- */
const removeTarget = ref<RecCard | null>(null)

async function submitRemove() {
  const card = removeTarget.value
  if (!card?.recommendation_id) return
  busy.value = true
  try {
    await del(`/recommendations/${card.recommendation_id}`)
    toast('已从推荐中移除')
    removeTarget.value = null
    if (selectedId.value === card.record_id) selectedId.value = null
    await load()
  } catch (e) {
    toastError(errorMessage(e))
  } finally {
    busy.value = false
  }
}

/* ---------------- Add book ---------------- */
const addOpen = ref(false)
const search = ref('')
const searchLoading = ref(false)
const results = ref<RecordRow[]>([])
const picked = ref<RecordRow | null>(null)
const addNote = ref('')
const addError = ref('')
const addBusy = ref(false)

function openAdd() {
  addOpen.value = true
  search.value = ''
  results.value = []
  picked.value = null
  addNote.value = ''
  addError.value = ''
}

async function searchRecords() {
  const query = search.value.trim()
  if (!query) {
    results.value = []
    return
  }
  searchLoading.value = true
  try {
    const data = await get<RecordList>('/records', {
      library_id: libraryId.value || undefined,
      q: query,
      active_only: true,
      page_size: 10,
    })
    const existing = new Set(curated.value.map((row) => row.record_id))
    results.value = data.items.filter((row) => !existing.has(row.id))
    picked.value = null
  } catch (e) {
    toastError(errorMessage(e))
  } finally {
    searchLoading.value = false
  }
}

async function submitAdd() {
  if (!picked.value) {
    addError.value = '请选择要推荐的书目'
    return
  }
  addBusy.value = true
  addError.value = ''
  try {
    const result = await post<{ message: string }>('/recommendations', {
      record_id: picked.value.id,
      note: addNote.value.trim() || null,
    })
    toast(result.message)
    addOpen.value = false
    tab.value = 'curated'
    selectedId.value = picked.value.id
    await load()
  } catch (e) {
    addError.value = errorMessage(e)
  } finally {
    addBusy.value = false
  }
}

onMounted(async () => {
  await loadLibraries()
  await load()
})
</script>

<template>
  <section class="view recpage">
    <header class="recp-band">
      <div class="recp-band-top">
        <span class="recp-sq" aria-hidden="true">荐</span>
        <p class="recp-motto">
          <span class="recp-motto-mark" aria-hidden="true">「</span>寻书有径，<em>阅读有期</em><span class="recp-motto-mark" aria-hidden="true">」</span>
        </p>
        <div class="recp-tools">
          <button v-if="canWrite" class="recp-btn recp-btn-amber" type="button" @click="openAdd">
            <AppIcon name="plus" :size="15" />加入推荐
          </button>
        </div>
      </div>

      <div class="recp-tabs" role="tablist" aria-label="推荐分类">
        <button
          v-for="item in TABS"
          :key="item.key"
          class="recp-tab"
          type="button"
          role="tab"
          :class="{ 'on': tab === item.key }"
          :aria-selected="tab === item.key"
          @click="tab = item.key"
        >
          <span class="recp-tab-k">
            <b>{{ item.label }}</b>
            <i>{{ tabCount(item.key) }}</i>
            <span v-if="item.key === 'curated' && canWrite" class="recp-tab-tag">可排架</span>
          </span>
          <span class="recp-tab-d">{{ item.desc }}</span>
        </button>
      </div>
    </header>

    <div class="recp-work">
      <section class="recp-canvas">
        <div class="recp-pane">
          <div v-if="loading" class="recp-state" aria-busy="true">
            <div class="skeleton" style="height: 120px" />
            <div class="skeleton" style="height: 120px" />
            <div class="skeleton" style="height: 120px" />
          </div>

          <div v-else-if="error" class="recp-state recp-state-error">
            <AppIcon name="alert" :size="26" />
            <strong>无法读取推荐内容</strong>
            <p>{{ error }}</p>
            <button class="recp-btn recp-btn-primary" type="button" @click="load">重新加载</button>
          </div>

          <template v-else>
            <!-- 热门借阅：排架清单 -->
            <template v-if="tab === 'popular'">
              <div v-if="popular.length" class="recp-rows">
                <article
                  v-for="(card, index) in popular.slice(0, 10)"
                  :key="card.record_id"
                  class="recp-row"
                  :class="{ sel: selectedId === card.record_id }"
                  @click="select(card)"
                >
                  <span class="recp-no" :class="{ top: index < 3 }">{{ String(index + 1).padStart(2, '0') }}</span>
                  <span class="recp-mini">
                    <BookCover :title="card.title || '未知题名'" :author="card.author" size="mini" />
                  </span>
                  <span class="recp-ti">
                    <strong>{{ card.title || '未知题名' }}</strong>
                    <em>{{ card.author || '佚名' }}</em>
                  </span>
                  <span class="recp-cl">{{ [card.class_no, card.shelf_no || card.call_no].filter(Boolean).join(' · ') || '未分类' }}</span>
                  <span class="recp-lo">
                    <span class="n"><b>{{ card.loan_count || 0 }}</b> 次外借</span>
                    <span class="bar"><i :style="{ width: `${loanPercent(card.loan_count)}%` }" /></span>
                  </span>
                  <span class="recp-pill" :class="card.available_count ? 'in' : 'out'">
                    {{ card.available_count ? `在馆 ${card.available_count}` : '暂无可借' }}
                  </span>
                  <span class="recp-ac">
                    <button
                      class="recp-sbtn"
                      :class="{ done: inCurated(card.record_id) }"
                      type="button"
                      :disabled="!canWrite || busy || inCurated(card.record_id)"
                      @click.stop="quickAdd(card)"
                    >
                      <AppIcon :name="inCurated(card.record_id) ? 'check' : 'plus'" :size="13" />
                      {{ inCurated(card.record_id) ? '已在精选' : '加入精选' }}
                    </button>
                    <RouterLink class="recp-tlink" :to="`/admin/records/${card.record_id}`" @click.stop>查看馆藏</RouterLink>
                  </span>
                </article>
              </div>
              <div v-else class="recp-state">
                <AppIcon name="trend-up" :size="26" />
                <strong>当前没有借阅热度数据</strong>
                <p>产生借阅记录后，热门榜会自动生成。</p>
              </div>
              <div class="recp-foot">
                <span>显示前 {{ Math.min(popular.length, 10) }} 条 · 按近 90 天外借次数排序，仅含仍可借的书目</span>
                <RouterLink to="/admin/records">前往图书库 →</RouterLink>
              </div>
            </template>

            <!-- 馆员精选：排架卡 -->
            <template v-else-if="tab === 'curated'">
              <div v-if="curated.length" class="recp-shelf">
                <article
                  v-for="(card, index) in curated"
                  :key="card.record_id"
                  class="recp-pc"
                  :class="{
                    'sel': selectedId === card.record_id,
                    'is-dragging': draggingId === card.record_id,
                    'is-drop-target': dragOverId === card.record_id,
                  }"
                  :draggable="canWrite"
                  @dragstart="onDragStart(index, $event)"
                  @dragover="onDragOver(index, $event)"
                  @drop.prevent="onDrop(index)"
                  @dragend="onDragEnd"
                  @click="select(card)"
                >
                  <span class="recp-rk">{{ index + 1 }}</span>
                  <span class="recp-grip" aria-hidden="true" />
                  <span class="recp-pc-cover">
                    <BookCover :title="card.title || '未知题名'" :author="card.author" size="card" />
                  </span>
                  <span class="recp-pc-b">
                    <strong>{{ card.title || '未知题名' }}</strong>
                    <span class="au">{{ [card.author || '佚名', card.class_no].filter(Boolean).join(' · ') }}</span>
                    <button
                      class="recp-note"
                      :class="{ empty: !card.note }"
                      type="button"
                      title="编辑推荐语"
                      @click.stop="openEdit(card)"
                    >
                      {{ card.note || '点这里写推荐语…' }}
                    </button>
                    <span class="f">
                      <span class="recp-pill" :class="card.available_count ? 'in' : 'out'">
                        {{ card.available_count ? `在馆 ${card.available_count}` : '暂无可借' }}
                      </span>
                      <span class="sp" />
                      <template v-if="canWrite">
                        <button class="recp-iact" type="button" title="上移" aria-label="上移" :disabled="index === 0 || busy" @click.stop="move(index, -1)">
                          <AppIcon name="arrow-up" :size="13" />
                        </button>
                        <button class="recp-iact" type="button" title="下移" aria-label="下移" :disabled="index === curated.length - 1 || busy" @click.stop="move(index, 1)">
                          <AppIcon name="arrow-down" :size="13" />
                        </button>
                        <button class="recp-iact" type="button" title="编辑推荐语" aria-label="编辑推荐语" @click.stop="openEdit(card)">
                          <AppIcon name="edit" :size="13" />
                        </button>
                        <button class="recp-iact del" type="button" title="移出推荐" aria-label="移出推荐" @click.stop="removeTarget = card">
                          <AppIcon name="trash" :size="13" />
                        </button>
                      </template>
                    </span>
                  </span>
                </article>

                <button v-if="canWrite" class="recp-add" type="button" @click="openAdd">
                  <AppIcon name="plus" :size="22" />
                  <span>加入推荐书目</span>
                </button>
              </div>
              <div v-else class="recp-state">
                <AppIcon name="star" :size="26" />
                <strong>当前没有馆员精选</strong>
                <p>加入推荐后，读者门户会优先展示这些书目。</p>
                <button v-if="canWrite" class="recp-btn recp-btn-primary" type="button" @click="openAdd">加入推荐</button>
              </div>
              <div class="recp-foot">
                <span>拖动卡片调整顺序，保存后读者门户立即生效</span>
                <span v-if="canWrite">推荐语限 256 字</span>
              </div>
            </template>

            <!-- 新到图书：到书墙 -->
            <template v-else>
              <div v-if="visibleArrivals.length" class="recp-new">
                <article
                  v-for="card in visibleArrivals"
                  :key="card.record_id"
                  class="recp-nc"
                  :class="{ sel: selectedId === card.record_id }"
                  @click="select(card)"
                >
                  <span class="recp-nc-cover">
                    <BookCover :title="card.title || '未知题名'" :author="card.author" size="card" />
                  </span>
                  <strong>{{ card.title || '未知题名' }}</strong>
                  <em>{{ [card.author || '佚名', card.class_no].filter(Boolean).join(' · ') }}</em>
                  <span class="recp-nc-meta">
                    <span v-if="card.created_at" class="dt"><i aria-hidden="true" />{{ formatMonthDay(card.created_at) }} 入藏</span>
                    <span class="recp-pill" :class="card.available_count ? 'in' : 'out'">
                      {{ card.available_count ? `在馆 ${card.available_count}` : '暂无可借' }}
                    </span>
                  </span>
                  <span class="f">
                    <button
                      class="recp-sbtn"
                      :class="{ done: inCurated(card.record_id) }"
                      type="button"
                      :disabled="!canWrite || busy || inCurated(card.record_id)"
                      @click.stop="quickAdd(card)"
                    >
                      <AppIcon :name="inCurated(card.record_id) ? 'check' : 'plus'" :size="13" />
                      {{ inCurated(card.record_id) ? '已在精选' : '加入精选' }}
                    </button>
                  </span>
                </article>
              </div>
              <div v-else class="recp-state">
                <AppIcon name="sparkles" :size="26" />
                <strong>近期没有新到图书</strong>
                <p>完成编目入库后，新到图书会出现在这里。</p>
              </div>
              <div class="recp-foot">
                <span>显示 {{ visibleArrivals.length }} 种 · 近 90 天入藏且当前可借，按入藏时间倒序</span>
                <RouterLink to="/admin/records">前往图书库 →</RouterLink>
              </div>
            </template>
          </template>
        </div>
      </section>

      <aside class="recp-side">
        <section class="recp-paper" :class="{ off: previewCard && !previewIsCurated }">
          <div class="recp-sh">
            <h3>读者门户预览</h3>
            <span class="recp-sp" />
            <span v-if="previewCard && previewIsCurated" class="recp-live"><i aria-hidden="true" />已上架</span>
            <span v-else-if="previewCard" class="recp-off"><i aria-hidden="true" />未上架</span>
            <button
              v-if="previewCard && previewIsCurated && canWrite && !noteEditing"
              class="recp-paper-edit"
              type="button"
              :title="previewCard.note ? '编辑推荐语' : '写推荐语'"
              :aria-label="previewCard.note ? '编辑推荐语' : '写推荐语'"
              @click="startNoteEdit"
            ><AppIcon name="edit" :size="14" /></button>
          </div>

          <template v-if="previewCard">
            <div class="recp-paper-book">
              <span class="recp-paper-cover">
                <span v-if="previewIsCurated" class="recp-seal" aria-hidden="true">荐</span>
                <BookCover :title="previewCard.title || '未知题名'" :author="previewCard.author" size="card" />
              </span>
              <div class="info">
                <strong>{{ previewCard.title || '未知题名' }}</strong>
                <em>{{ previewCard.author || '佚名' }}</em>
                <em v-if="previewCard.publisher">{{ previewCard.publisher }}</em>
              </div>
            </div>

            <div class="recp-lines" :class="{ editing: noteEditing }">
              <template v-if="noteEditing">
                <textarea
                  ref="noteInput"
                  v-model="noteDraft"
                  class="recp-lines-input"
                  maxlength="256"
                  placeholder="写一句推荐语，读者会在这里看到"
                  @keydown.ctrl.enter.prevent="saveNote"
                  @keydown.meta.enter.prevent="saveNote"
                  @keydown.esc="cancelNoteEdit"
                />
              </template>
              <template v-else>
                <p
                  v-if="previewIsCurated && previewCard.note"
                  class="recp-lines-text"
                  :class="{ editable: canWrite }"
                  :title="canWrite ? '点击编辑推荐语' : ''"
                  @click="canWrite && startNoteEdit()"
                >{{ previewCard.note }}</p>
                <button
                  v-else-if="previewIsCurated && canWrite"
                  class="recp-lines-empty"
                  type="button"
                  @click="startNoteEdit"
                >写一句推荐语，读者会在这里看到</button>
                <p v-else-if="previewIsCurated" class="recp-lines-ro">馆员暂未填写推荐语，可以先从馆藏信息了解这本书。</p>
                <p v-else class="recp-lines-ro">还没加入馆员精选，读者在「发现」页看不到这本书。</p>
              </template>
            </div>

            <div class="recp-file">
              <div class="recp-file-h">馆藏档案</div>
              <div class="recp-file-figs">
                <div>
                  <span>近 90 天外借</span>
                  <strong>{{ previewCard.loan_count || 0 }}<small>次</small></strong>
                </div>
                <div>
                  <span>可借 / 馆藏</span>
                  <strong>{{ previewCard.available_count }}<small>/ {{ previewCard.active_count }} 册</small></strong>
                </div>
              </div>
              <div class="recp-file-rows">
                <span><i>分类号</i>{{ previewCard.class_no || '未分类' }}</span>
                <span><i>入藏</i>{{ previewCard.created_at ? formatDate(previewCard.created_at) : '—' }}</span>
                <span v-if="previewCard.isbn"><i>ISBN</i>{{ previewCard.isbn }}</span>
              </div>
            </div>

            <div class="recp-paper-foot">
              <template v-if="noteEditing">
                <span class="recp-count">{{ noteDraft.length }} / 256</span>
                <span class="recp-sp" />
                <button class="recp-note-btn" type="button" @click="cancelNoteEdit">取消</button>
                <button class="recp-note-btn primary" type="button" :disabled="busy" @click="saveNote">保存</button>
              </template>
              <template v-else>
                <span v-if="previewIsCurated" class="recp-pill pos">精选第 {{ previewCuratedCard?.sort_order }} 位</span>
                <span class="recp-pill shelf">{{ previewShelf }}</span>
                <span class="recp-sp" />
                <button
                  v-if="canWrite && !previewIsCurated"
                  class="recp-quote-act"
                  type="button"
                  :disabled="busy"
                  @click="quickAdd(previewCard)"
                ><AppIcon name="plus" :size="12" />加入精选</button>
              </template>
            </div>
          </template>

          <div v-else class="recp-paper-empty">
            <AppIcon name="eye" :size="20" />
            <span>点击左侧任意书目，查看读者会看到的样子</span>
          </div>
        </section>

        <div class="recp-card">
          <div class="recp-sh">
            <h3>推荐位规则</h3>
            <span class="recp-sp" />
            <span class="recp-cap">读者门户生效逻辑</span>
          </div>
          <div class="recp-rule"><i>01</i><span><b>馆员精选优先</b>，占据发现页最显眼的推荐位</span></div>
          <div class="recp-rule"><i>02</i><span><b>拖动卡片即改读者所见顺序</b>，保存即生效</span></div>
          <div class="recp-rule"><i>03</i><span><b>推荐语限 256 字</b>，写清适合谁读</span></div>
          <div class="recp-quota">
            <span>精选</span><b>{{ curated.length }}</b><span>种</span>
            <span class="recp-sp" />
            <span class="recp-cap">热门 {{ popular.length }} · 新到 {{ arrivals.length }}</span>
          </div>
        </div>
      </aside>
    </div>

    <BaseModal
      :open="Boolean(editTarget)"
      title="编辑推荐语"
      :subtitle="editTarget?.title || ''"
      size="sm"
      @close="editTarget = null"
    >
      <div class="field">
        <label for="rec-note-edit">推荐语</label>
        <textarea id="rec-note-edit" v-model="editNote" class="textarea" rows="4" maxlength="256" placeholder="面向读者的简短推荐理由" />
        <span class="hint">{{ editNote.length }} / 256</span>
      </div>
      <p v-if="editError" class="field-note is-error" style="margin-top: 10px" role="alert">{{ editError }}</p>
      <template #footer>
        <button class="btn btn-ghost" type="button" :disabled="busy" @click="editTarget = null">取消</button>
        <button class="btn btn-primary" type="button" :disabled="busy" @click="submitEdit">
          {{ busy ? '保存中…' : '保存推荐语' }}
        </button>
      </template>
    </BaseModal>

    <ConfirmDialog
      :open="Boolean(removeTarget)"
      title="移出推荐"
      :message="removeTarget ? `确认将《${removeTarget.title || '未知题名'}》移出馆员精选？\n不会删除书目与馆藏。` : ''"
      confirm-text="移出推荐"
      :busy="busy"
      @cancel="removeTarget = null"
      @confirm="submitRemove"
    />

    <BaseModal
      :open="addOpen"
      title="加入馆员推荐"
      subtitle="仅可推荐仍有在架馆藏的书目"
      @close="addOpen = false"
    >
      <div class="recp-search">
        <AppIcon name="search" :size="15" />
        <input
          v-model="search"
          class="input"
          placeholder="搜索题名、责任者、ISBN 或条码"
          autocomplete="off"
          @keydown.enter.prevent="searchRecords"
        >
        <button class="recp-btn recp-btn-primary" type="button" :disabled="searchLoading" @click="searchRecords">
          {{ searchLoading ? '检索中…' : '检索' }}
        </button>
      </div>
      <div v-if="results.length" class="recp-pick-list">
        <button
          v-for="row in results"
          :key="row.id"
          class="recp-pick"
          :class="{ on: picked?.id === row.id }"
          type="button"
          @click="picked = row"
        >
          <span class="recp-pick-cover"><BookCover :title="row.title || '未知题名'" :author="row.author" size="card" /></span>
          <span class="recp-pick-info">
            <strong>{{ row.title || '未知题名' }}</strong>
            <span>{{ row.author || '佚名' }} · {{ row.call_no || row.class_no || '未分类' }} · 馆藏 {{ row.total_count }} 册</span>
          </span>
          <AppIcon v-if="picked?.id === row.id" name="check-circle" :size="17" />
        </button>
      </div>
      <p v-else-if="search && !searchLoading" class="field-note" style="margin-top: 10px">没有检索到可推荐的书目</p>
      <div class="field" style="margin-top: 14px">
        <label for="rec-note-add">推荐语</label>
        <textarea id="rec-note-add" v-model="addNote" class="textarea" rows="3" maxlength="256" placeholder="面向读者的简短推荐理由（可选）" />
        <span class="hint">{{ addNote.length }} / 256</span>
      </div>
      <p v-if="addError" class="field-note is-error" style="margin-top: 10px" role="alert">{{ addError }}</p>
      <template #footer>
        <button class="btn btn-ghost" type="button" :disabled="addBusy" @click="addOpen = false">取消</button>
        <button class="btn btn-primary" type="button" :disabled="addBusy || !picked" @click="submitAdd">
          {{ addBusy ? '提交中…' : '加入推荐' }}
        </button>
      </template>
    </BaseModal>
  </section>
</template>
