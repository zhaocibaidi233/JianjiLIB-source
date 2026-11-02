<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import AppIcon from '../components/AppIcon.vue'
import BaseModal from '../components/BaseModal.vue'
import BookCover from '../components/BookCover.vue'
import PagerBar from '../components/PagerBar.vue'
import { errorMessage, readerGet, readerPost } from '../lib/api'
import { holdingLocationLabel } from '../lib/displayLabels'
import { formatDate, formatNumber } from '../lib/format'
import { toast, toastError } from '../lib/toast'

interface BookCard {
  record_id: number
  title: string | null
  author: string | null
  publisher: string | null
  year: number | null
  class_no: string | null
  isbn: string | null
  call_no: string | null
  shelf_no?: string
  active_count: number
  available_count: number
}

interface HomeMeta {
  library: { id: number; name: string }
  stats: { copies: number; available: number; titles: number; new_titles: number }
  categories: Array<{ key: string; label: string; copies: number }>
}

interface Reservation {
  id: number
  status: string
  status_label: string
  queue_position: number | null
  expires_at: string | null
  seconds_left: number | null
}

interface BookDetail extends BookCard {
  summary: string | null
  subjects: string[]
  locations: string[]
  shelf_no: string
  hold_days: number
  can_reserve: boolean
  reserve_reason: string
  reservation: Reservation | null
  reader_status: string
}

type SortKey = 'title' | 'year' | 'popular'
const SORTS: Array<{ key: SortKey; label: string }> = [
  { key: 'title', label: '默认' },
  { key: 'year', label: '出版年' },
  { key: 'popular', label: '借阅热度' },
]

const route = useRoute()
const router = useRouter()
const home = ref<HomeMeta | null>(null)
const loading = ref(true)
const metaError = ref('')
const books = ref<BookCard[]>([])
const bookTotal = ref(0)
const bookPage = ref(1)
// 大屏一屏多放几排：宽屏按可用高度给 16/20 条，小屏保持 12 条
const bookPageSize = (() => {
  if (typeof window === 'undefined') return 12
  if (window.innerWidth < 1500) return 12
  return window.innerHeight >= 1000 ? 20 : 16
})()
const booksLoading = ref(false)
const booksError = ref('')
const q = ref(typeof route.query.q === 'string' ? route.query.q : '')
const category = ref(typeof route.query.category === 'string' ? route.query.category : '')
const sort = ref<SortKey>('title')

const detailOpen = ref(false)
const detail = ref<BookDetail | null>(null)
const detailLoading = ref(false)
const detailError = ref('')
const reserving = ref(false)
const detailLocationText = computed(() => {
  const current = detail.value
  if (!current?.locations.length) return '—'
  return current.locations.map(loc => holdingLocationLabel(loc, current.shelf_no)).join('、')
})
const currentCategory = computed(() =>
  home.value?.categories.find(item => item.key === category.value) || null,
)

watch(
  () => route.query.q,
  (value) => {
    const next = typeof value === 'string' ? value : ''
    if (next === q.value.trim()) return
    q.value = next
    void loadBooks(true)
  },
)

watch(
  () => route.query.category,
  (value) => {
    const next = typeof value === 'string' ? value : ''
    if (next === category.value) return
    category.value = next
    void loadBooks(true)
  },
)

async function loadMeta() {
  try {
    home.value = await readerGet<HomeMeta>('/portal/home')
  } catch (e) {
    metaError.value = errorMessage(e)
  }
}

async function loadBooks(resetPage = false) {
  if (resetPage) bookPage.value = 1
  booksLoading.value = true
  booksError.value = ''
  try {
    const data = await readerGet<{ total: number; items: BookCard[] }>('/portal/books', {
      q: q.value.trim() || undefined,
      category: category.value || undefined,
      sort: sort.value,
      page: bookPage.value,
      page_size: bookPageSize,
    })
    books.value = data.items
    bookTotal.value = data.total
  } catch (e) {
    booksError.value = errorMessage(e)
    books.value = []
    bookTotal.value = 0
  } finally {
    booksLoading.value = false
  }
}

function queryFor(nextQ: string, nextCategory: string): Record<string, string> {
  const query: Record<string, string> = {}
  if (nextQ.trim()) query.q = nextQ.trim()
  if (nextCategory) query.category = nextCategory
  return query
}
function submitSearch() {
  const term = q.value.trim()
  void router.replace({ path: '/reader/catalog', query: queryFor(term, category.value) })
  void loadBooks(true)
}
function chooseCategory(key: string) {
  const next = category.value === key ? '' : key
  category.value = next
  void router.replace({ path: '/reader/catalog', query: queryFor(q.value, next) })
  void loadBooks(true)
}
function setSort(key: SortKey) {
  if (sort.value === key) return
  sort.value = key
  void loadBooks(true)
}
function setPage(page: number) {
  bookPage.value = page
  void loadBooks()
}
function clearFilters() {
  q.value = ''
  category.value = ''
  void router.replace({ path: '/reader/catalog' })
  void loadBooks(true)
}

async function openBook(card: BookCard) {
  detailOpen.value = true
  detail.value = null
  detailError.value = ''
  detailLoading.value = true
  try {
    detail.value = await readerGet<BookDetail>(`/portal/books/${card.record_id}`)
  } catch (e) {
    detailError.value = errorMessage(e)
  } finally {
    detailLoading.value = false
  }
}

async function reloadDetail() {
  if (!detail.value) return
  detail.value = await readerGet<BookDetail>(`/portal/books/${detail.value.record_id}`)
}
async function reserveBook() {
  if (!detail.value || reserving.value) return
  reserving.value = true
  try {
    const data = await readerPost<{ reservation: Reservation; message: string }>(
      '/portal/reservations',
      { record_id: detail.value.record_id },
    )
    toast(data.message)
    await reloadDetail()
  } catch (e) {
    toastError(errorMessage(e))
  } finally {
    reserving.value = false
  }
}
async function cancelReservation() {
  if (!detail.value?.reservation) return
  reserving.value = true
  try {
    await readerPost(`/portal/reservations/${detail.value.reservation.id}/cancel`)
    toast('预约已取消')
    await reloadDetail()
  } catch (e) {
    toastError(errorMessage(e))
  } finally {
    reserving.value = false
  }
}

onMounted(async () => {
  await Promise.all([loadMeta(), loadBooks()])
  loading.value = false
})
</script>

<template>
  <div class="reader-view cat-page">
    <header class="cat-head">
      <div>
        <div class="cat-eyebrow">COLLECTION</div>
        <h1>馆藏检索</h1>
        <p v-if="home">
          {{ home.library.name }} · {{ formatNumber(home.stats.titles) }} 种书目 · {{ formatNumber(home.stats.copies) }} 册馆藏
        </p>
        <p v-else-if="metaError">馆藏概况暂时无法读取，仍可继续检索。</p>
      </div>
      <RouterLink class="btn btn-outline btn-sm cat-back" to="/reader">
        <AppIcon name="compass" />返回门户
      </RouterLink>
    </header>

    <div v-if="loading" class="reader-loading"><span class="spinner" />正在准备馆藏…</div>
    <template v-else>
      <section class="cat-tools" aria-label="馆藏筛选">
        <form class="cat-search" role="search" @submit.prevent="submitSearch">
          <AppIcon name="search" :size="16" />
          <input v-model="q" placeholder="搜索题名、责任者或 ISBN" aria-label="搜索馆藏" autocomplete="off">
          <button type="submit">检索</button>
        </form>
        <div class="cat-results">
          <span><b>{{ formatNumber(bookTotal) }}</b> 种结果</span>
          <span v-if="currentCategory">· {{ currentCategory.label }}</span>
          <span v-else-if="q">· “{{ q }}”</span>
          <button v-if="q || category" class="text-link" type="button" @click="clearFilters">清除筛选</button>
        </div>
        <span class="cat-sort" role="group" aria-label="排序">
          <button
            v-for="item in SORTS"
            :key="item.key"
            type="button"
            :class="{ 'is-on': sort === item.key }"
            @click="setSort(item.key)"
          >{{ item.label }}</button>
        </span>
      </section>

      <div class="cat-chips" role="group" aria-label="图书分类">
        <button type="button" class="cat-chip" :class="{ 'is-on': !category }" @click="chooseCategory(category)">全部</button>
        <button
          v-for="item in home?.categories || []"
          :key="item.key"
          type="button"
          class="cat-chip"
          :class="{ 'is-on': category === item.key }"
          @click="chooseCategory(item.key)"
        >
          {{ item.label }}<span>{{ formatNumber(item.copies) }}</span>
        </button>
      </div>

      <div v-if="booksLoading" class="reader-loading"><span class="spinner" />正在读取馆藏…</div>
      <div v-else-if="booksError" class="reader-empty">
        <AppIcon name="alert" /><strong>馆藏读取失败</strong><p>{{ booksError }}</p>
        <button class="btn btn-outline btn-sm" type="button" @click="loadBooks()">重试</button>
      </div>
      <div v-else-if="!books.length" class="reader-empty">
        <AppIcon name="search" />
        <strong>当前没有匹配的图书</strong>
        <p>可更换关键词，或重新选择左侧分类。</p>
      </div>
      <div v-else class="cat-grid">
        <button v-for="book in books" :key="book.record_id" class="cat-book" type="button" @click="openBook(book)">
          <span class="cat-cover">
            <BookCover :title="book.title || '未知题名'" :author="book.author" size="card" />
          </span>
          <span class="cat-book-main">
            <span class="cat-book-top">
              <span class="cat-status" :class="{ 'is-out': !book.available_count }">
                {{ book.available_count ? `可借 ${book.available_count} 册` : '可预约' }}
              </span>
              <span class="cat-class">{{ book.class_no || '馆藏' }}</span>
            </span>
            <strong class="cat-book-title">{{ book.title || '未知题名' }}</strong>
            <span class="cat-book-author">{{ [book.author || '佚名', book.publisher, book.year].filter(Boolean).join(' · ') }}</span>
            <span class="cat-book-foot">
              <span>{{ book.call_no || book.shelf_no || '索书号待定' }}</span>
              <em>查看详情 <AppIcon name="chevron-right" :size="11" /></em>
            </span>
          </span>
        </button>
      </div>

      <PagerBar
        v-if="!booksLoading && !booksError && bookTotal"
        class="cat-pager"
        :page="bookPage"
        :page-size="bookPageSize"
        :total="bookTotal"
        noun="种"
        @update:page="setPage"
      />
    </template>

    <BaseModal
      :open="detailOpen"
      :title="detail?.title || '馆藏详情'"
      subtitle="馆藏信息与预约"
      size="lg"
      @close="detailOpen = false"
    >
      <div v-if="detailLoading" class="reader-loading"><span class="spinner" />正在读取馆藏详情…</div>
      <div v-else-if="detailError" class="reader-empty"><AppIcon name="alert" /><strong>无法读取详情</strong><p>{{ detailError }}</p></div>
      <div v-else-if="detail" class="reader-book-detail">
        <div class="reader-book-detail-cover">
          <BookCover :title="detail.title || '未知题名'" :author="detail.author" size="card" />
        </div>
        <div>
          <h2 class="reader-detail-title">{{ detail.title || '未知题名' }}</h2>
          <div class="reader-detail-author">{{ detail.author || '佚名' }}</div>
          <div class="reader-detail-tags">
            <span class="badge" :class="detail.available_count ? 'badge-ok' : 'badge-warn'">
              {{ detail.available_count ? `可借 ${detail.available_count} 册` : '暂无可借' }}
            </span>
            <span v-if="detail.class_no" class="badge badge-neutral no-dot">{{ detail.class_no }}</span>
            <span v-if="detail.call_no" class="badge badge-neutral no-dot">{{ detail.call_no }}</span>
            <span v-if="detail.year" class="badge badge-neutral no-dot">{{ detail.year }}</span>
          </div>
          <p v-if="detail.summary" class="reader-detail-desc">{{ detail.summary }}</p>
          <div v-if="detail.subjects.length" class="reader-detail-tags">
            <span v-for="subject in detail.subjects.slice(0, 6)" :key="subject" class="reader-location-chip">{{ subject }}</span>
          </div>
          <div class="reader-detail-meta">
            <div class="info-line"><span>出版者</span><strong>{{ detail.publisher || '—' }}</strong></div>
            <div class="info-line"><span>ISBN</span><strong>{{ detail.isbn || '—' }}</strong></div>
            <div class="info-line"><span>索书号</span><strong>{{ detail.call_no || '—' }}</strong></div>
            <div class="info-line"><span>馆藏地点</span><strong>{{ detailLocationText }}</strong></div>
          </div>
          <div v-if="detail.reservation" class="reader-note-card" style="margin-top: 14px">
            <strong>{{ detail.reservation.status_label }}</strong>
            <template v-if="detail.reservation.status === 'READY'">
              · 请于 {{ formatDate(detail.reservation.expires_at) }} 前到馆取书
            </template>
            <template v-else> · 当前排队第 {{ detail.reservation.queue_position || 1 }} 位</template>
          </div>
          <div class="reader-detail-action">
            <template v-if="detail.reservation">
              <button class="btn btn-outline" type="button" :disabled="reserving" @click="cancelReservation">
                {{ reserving ? '处理中…' : '取消预约' }}
              </button>
              <p>已登记预约，到书后会在这里更新状态。</p>
            </template>
            <template v-else-if="detail.can_reserve">
              <button class="btn btn-primary" type="button" :disabled="reserving" @click="reserveBook">
                <AppIcon name="bookmark" />{{ reserving ? '提交中…' : '预约这本书' }}
              </button>
              <p>到书后保留 {{ detail.hold_days }} 天，请按时到馆取书。</p>
            </template>
            <template v-else>
              <button class="btn btn-soft" type="button" disabled>{{ detail.available_count ? '到馆借阅' : detail.reserve_reason }}</button>
              <p>{{ detail.available_count ? `当前有 ${detail.available_count} 册可借，请到馆办理借阅。` : detail.reserve_reason }}</p>
            </template>
          </div>
        </div>
      </div>
    </BaseModal>
  </div>
</template>
