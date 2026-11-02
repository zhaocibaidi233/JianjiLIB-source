<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import AppIcon from '../components/AppIcon.vue'
import BookCover from '../components/BookCover.vue'
import { errorMessage, readerGet } from '../lib/api'
import { daysBetween, formatMonthDay, formatNumber } from '../lib/format'
import { readerAuth } from '../lib/readerAuth'

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
  note?: string | null
  loan_count?: number
  created_at?: string | null
}

interface LoanRow {
  id: number
  title: string
  due_on: string | null
  overdue?: boolean
}

interface Reservation {
  id: number
  status: string
  status_label: string
  title?: string | null
}

interface HomeData {
  library: { id: number; name: string }
  reader: { id: number; name: string; card_no: string }
  stats: { copies: number; available: number; titles: number; new_titles: number }
  categories: Array<{ key: string; label: string; copies: number }>
  curated: BookCard[]
  popular: BookCard[]
  new_arrivals: BookCard[]
  hot_keywords: string[]
}

const router = useRouter()
const home = ref<HomeData | null>(null)
const loans = ref<LoanRow[]>([])
const reservations = ref<Reservation[]>([])
const loading = ref(true)
const error = ref('')
const reader = computed(() => readerAuth.reader)

const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches

const hour = new Date().getHours()
const greeting = hour < 11 ? '早上好' : hour < 14 ? '中午好' : hour < 18 ? '下午好' : '晚上好'
const today = new Intl.DateTimeFormat('zh-CN', {
  month: 'long',
  day: 'numeric',
  weekday: 'long',
}).format(new Date())

const activeReservations = computed(() =>
  reservations.value.filter(item => ['PENDING', 'READY'].includes(item.status)),
)
const sortedLoans = computed(() =>
  [...loans.value]
    .filter(loan => loan.due_on)
    .sort((a, b) => String(a.due_on).localeCompare(String(b.due_on))),
)
const nearestLoan = computed(() => sortedLoans.value[0] || null)
const recentLoans = computed(() => sortedLoans.value.slice(0, 3))
const dueSoonLoans = computed(() =>
  sortedLoans.value.filter(loan => {
    const days = daysBetween(loan.due_on)
    return days !== null && days <= 3
  }),
)
const dueSoonCount = computed(() => dueSoonLoans.value.length)
const firstDueSoon = computed(() => dueSoonLoans.value[0] || null)
const loanLimit = computed(() => reader.value?.rules.max_items || 10)
const ringLength = 106.8
const ringOffset = computed(() => {
  const used = Math.min(1, loans.value.length / Math.max(1, loanLimit.value))
  return ringLength - ringLength * used
})

/** 主视觉右侧书脊墙：新到书目不足时退回推荐位 */
const shelfBooks = computed(() => {
  const current = home.value
  if (!current) return []
  const pool = current.new_arrivals.length ? current.new_arrivals : wallBooks.value
  const seen = new Set<number>()
  const result: BookCard[] = []
  for (const book of pool) {
    if (seen.has(book.record_id)) continue
    seen.add(book.record_id)
    result.push(book)
    if (result.length === 10) break
  }
  return result
})

const recommendations = computed(() => {
  const current = home.value
  if (!current) return []
  const seen = new Set<number>()
  const result: BookCard[] = []
  for (const book of [...current.curated, ...current.popular, ...current.new_arrivals]) {
    if (seen.has(book.record_id)) continue
    seen.add(book.record_id)
    result.push(book)
  }
  return result
})
const wallBooks = computed(() => recommendations.value.slice(0, 16))
const hotIds = computed(() => {
  const list = [...(home.value?.popular || [])]
    .sort((a, b) => (b.loan_count || 0) - (a.loan_count || 0))
    .slice(0, 3)
  return new Set(list.map(book => book.record_id))
})
const newIds = computed(() => new Set((home.value?.new_arrivals || []).map(book => book.record_id)))

const ranking = computed(() =>
  [...(home.value?.popular || [])]
    .filter(book => (book.loan_count || 0) > 0)
    .sort((a, b) => (b.loan_count || 0) - (a.loan_count || 0))
    .slice(0, 5),
)
const rankMax = computed(() => Math.max(1, ranking.value[0]?.loan_count || 1))

const totalCategoryCopies = computed(() =>
  Math.max(1, (home.value?.categories || []).reduce((sum, item) => sum + item.copies, 0)),
)
/** 分类占全馆藏书的比例，进度条长度与百分比用同一口径 */
function categoryPercent(copies: number): number {
  return Math.max(3, Math.round((copies / totalCategoryCopies.value) * 100))
}

/* ---------- 馆员精选轮播 ---------- */
const pickIndex = ref(0)
const picks = computed(() => home.value?.curated || [])
const pick = computed<BookCard | null>(() => picks.value[pickIndex.value] || null)
const pickMeta = computed(() => {
  const book = pick.value
  if (!book) return ''
  return [book.author || '佚名', book.publisher, book.year ? `${book.year} 年` : '', book.class_no]
    .filter(Boolean)
    .join(' · ')
})
const pickShelf = computed(() => pick.value?.shelf_no || pick.value?.call_no || '架位待定')
let pickTimer: number | undefined
let pickPaused = false

function selectPick(index: number) {
  pickIndex.value = index
}
function pausePick(value: boolean) {
  pickPaused = value
}

function startCarousel() {
  stopCarousel()
  if (reduced) return
  pickTimer = window.setInterval(() => {
    if (pickPaused || picks.value.length < 2) return
    pickIndex.value = (pickIndex.value + 1) % picks.value.length
  }, 7000)
}
function stopCarousel() {
  if (pickTimer) window.clearInterval(pickTimer)
  pickTimer = undefined
}

/* ---------- 检索 ---------- */
const searchQ = ref('')
function submitSearch() {
  const q = searchQ.value.trim()
  void router.push({ path: '/reader/catalog', query: q ? { q } : {} })
}

function dueParts(loan: LoanRow): { month: string; day: string } {
  const raw = (loan.due_on || '').slice(0, 10)
  const date = raw ? new Date(`${raw}T00:00:00`) : null
  if (!date || Number.isNaN(date.getTime())) return { month: '待定', day: '--' }
  return { month: `${date.getMonth() + 1} 月`, day: String(date.getDate()).padStart(2, '0') }
}
function leftText(loan: LoanRow): string {
  const days = daysBetween(loan.due_on)
  if (days < 0) return `逾期 ${-days} 天`
  if (days === 0) return '今天到期'
  return `剩 ${days} 天`
}
function isDueSoon(loan: LoanRow): boolean {
  const days = daysBetween(loan.due_on)
  return days !== null && days <= 3
}
function isOverdue(loan: LoanRow): boolean {
  return daysBetween(loan.due_on) < 0
}
function bookLink(book: BookCard) {
  return { path: '/reader/catalog', query: { q: book.title || book.isbn || '' } }
}

function hashOf(text: string): number {
  let value = 0
  for (let i = 0; i < text.length; i += 1) value = (value * 31 + text.charCodeAt(i)) >>> 0
  return value
}
function spineStyle(title: string | null) {
  const h = hashOf(title || '')
  return {
    height: `${118 + (h % 6) * 8 + ((h >> 4) % 7)}px`,
    width: `${22 + ((h >> 3) % 18)}px`,
  }
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    home.value = await readerGet<HomeData>('/portal/home')
    const [loanData, reservationData] = await Promise.all([
      readerGet<{ active: LoanRow[] }>('/portal/loans', { status: 'active' }).catch(() => ({ active: [] })),
      readerGet<{ items: Reservation[] }>('/portal/reservations', { status: 'all' }).catch(() => ({ items: [] })),
    ])
    loans.value = loanData.active || []
    reservations.value = reservationData.items || []
    pickIndex.value = 0
    startCarousel()
  } catch (e) {
    error.value = errorMessage(e)
  } finally {
    loading.value = false
  }
}

onMounted(load)
onBeforeUnmount(stopCarousel)
</script>

<template>
  <div class="dsc-page">
    <div v-if="loading" class="reader-loading"><span class="spinner" />正在准备你的图书馆…</div>
    <div v-else-if="error || !home" class="reader-empty">
      <AppIcon name="alert" />
      <strong>门户读取失败</strong>
      <p>{{ error || '暂时无法读取馆藏信息' }}</p>
      <button class="btn btn-primary btn-sm" type="button" @click="load">重新加载</button>
    </div>
    <template v-else>
      <!-- 门厅 -->
      <section class="dsc-foyer dsc-in">
        <span class="dsc-foyer-mark" aria-hidden="true">阅</span>
        <div class="dsc-foyer-l">
          <div class="dsc-eyebrow">
            <b>{{ today }}</b>
            <span>{{ home.library.name }}</span>
          </div>
          <h1>{{ greeting }}，<em>{{ reader?.name || home.reader.name }}</em>。</h1>
          <p class="dsc-foyer-sub">
            <template v-if="dueSoonCount">
              有 <b>{{ dueSoonCount }}</b> 册图书 3 天内到期，
            </template>
            <template v-else>当前没有临期图书，</template>
            馆内还有 <b>{{ formatNumber(home.stats.available) }}</b> 册可借。
          </p>

          <form class="dsc-search" role="search" @submit.prevent="submitSearch">
            <AppIcon name="search" :size="18" />
            <input v-model="searchQ" type="search" placeholder="检索题名、责任者、ISBN 或条码" aria-label="检索馆藏" autocomplete="off">
            <button class="dsc-search-go" type="submit">检索馆藏</button>
          </form>
          <div v-if="home.hot_keywords.length" class="dsc-hot">
            <i>热门</i>
            <RouterLink
              v-for="word in home.hot_keywords.slice(0, 6)"
              :key="word"
              :to="{ path: '/reader/catalog', query: { q: word } }"
            >{{ word }}</RouterLink>
          </div>

          <div class="dsc-foyer-stats">
            <div><b>{{ formatNumber(home.stats.available) }}</b><span>册可借</span></div>
            <div><b>{{ formatNumber(home.stats.titles) }}</b><span>种书目</span></div>
            <div><b>{{ home.categories.length }}</b><span>个分类</span></div>
            <div><b>{{ formatNumber(home.stats.new_titles) }}</b><span>近 30 天新增</span></div>
          </div>
        </div>

        <div class="dsc-shelf">
          <div class="dsc-shelf-head">
            <h3>新 书 上 架</h3>
            <RouterLink to="/reader/catalog">全部新书<AppIcon name="arrow-right" :size="13" /></RouterLink>
          </div>
          <div v-if="shelfBooks.length" class="dsc-books">
            <RouterLink
              v-for="book in shelfBooks"
              :key="book.record_id"
              class="dsc-spine-slot"
              :style="spineStyle(book.title)"
              :to="bookLink(book)"
              :title="book.title || '未知题名'"
            >
              <BookCover :title="book.title || '未知题名'" :author="book.author" size="spine" />
            </RouterLink>
          </div>
          <div class="dsc-board" aria-hidden="true" />
          <div class="dsc-shelf-note">
            <span>近 30 天新增 <b>{{ formatNumber(home.stats.new_titles) }} 种</b></span>
            <span>当前可借 <b>{{ formatNumber(home.stats.available) }} 册</b></span>
          </div>
        </div>
      </section>

      <!-- 我的借阅状态 -->
      <section class="dsc-ribbon dsc-in dsc-in2" aria-label="我的借阅状态">
        <RouterLink class="dsc-rib" to="/reader/loans">
          <span class="dsc-rib-ico"><AppIcon name="book-open" :size="19" /></span>
          <span class="dsc-rib-txt">
            <span class="dsc-rib-k">当前外借</span>
            <span class="dsc-rib-v"><b>{{ formatNumber(loans.length) }}</b><i>册</i></span>
            <span class="dsc-rib-n">{{ nearestLoan ? `最近 ${formatMonthDay(nearestLoan.due_on)} 到期` : '当前没有待还图书' }}</span>
          </span>
        </RouterLink>
        <RouterLink class="dsc-rib warn" to="/reader/loans">
          <span class="dsc-rib-ico"><AppIcon name="clock" :size="19" /></span>
          <span class="dsc-rib-txt">
            <span class="dsc-rib-k">临期与逾期</span>
            <span class="dsc-rib-v"><b>{{ formatNumber(dueSoonCount) }}</b><i>册</i></span>
            <span class="dsc-rib-n">{{ firstDueSoon ? `《${firstDueSoon.title}》 · ${leftText(firstDueSoon)}` : '暂无临期图书' }}</span>
          </span>
        </RouterLink>
        <RouterLink class="dsc-rib" :to="{ path: '/reader/loans', query: { tab: 'reservations' } }">
          <span class="dsc-rib-ico"><AppIcon name="bookmark" :size="19" /></span>
          <span class="dsc-rib-txt">
            <span class="dsc-rib-k">预约进行中</span>
            <span class="dsc-rib-v"><b>{{ formatNumber(activeReservations.length) }}</b><i>册</i></span>
            <span class="dsc-rib-n">{{ activeReservations.length ? '到馆后通知取书' : '暂时没有预约' }}</span>
          </span>
        </RouterLink>
        <RouterLink class="dsc-rib" to="/reader/loans">
          <svg class="dsc-ring" viewBox="0 0 42 42" aria-hidden="true">
            <circle class="t" cx="21" cy="21" r="17" />
            <circle
              class="d"
              cx="21"
              cy="21"
              r="17"
              transform="rotate(-90 21 21)"
              :stroke-dasharray="ringLength"
              :stroke-dashoffset="ringOffset"
            />
          </svg>
          <span class="dsc-rib-txt">
            <span class="dsc-rib-k">借阅额度</span>
            <span class="dsc-rib-v"><b>{{ loans.length }}</b><i>/ {{ loanLimit }} 本</i></span>
            <span class="dsc-rib-n">借期 {{ reader?.rules.loan_days || 30 }} 天 · 可续借 {{ reader?.rules.max_renew || 1 }} 次</span>
          </span>
        </RouterLink>
      </section>

      <div class="dsc-body">
        <div class="dsc-main">
          <!-- 馆员精选 -->
          <section v-if="picks.length" class="dsc-card dsc-in dsc-in3">
            <div class="dsc-sec-head">
              <h2>馆员精选</h2>
              <p>馆员读过之后推荐给你的书</p>
              <RouterLink class="dsc-more" to="/reader/catalog">更多书目<AppIcon name="arrow-right" :size="13" /></RouterLink>
            </div>
            <div v-if="pick" class="dsc-pick" @mouseenter="pausePick(true)" @mouseleave="pausePick(false)">
              <div class="dsc-pick-cover">
                <span class="dsc-seal" aria-hidden="true">荐</span>
                <BookCover :title="pick.title || '未知题名'" :author="pick.author" size="card" />
              </div>
              <div class="dsc-pick-body">
                <div class="dsc-pick-tag">馆员精选 <em>读者首页优先展示</em></div>
                <RouterLink :to="bookLink(pick)"><h3 class="dsc-pick-title">{{ pick.title || '未知题名' }}</h3></RouterLink>
                <div class="dsc-pick-meta">{{ pickMeta }}</div>
                <div class="dsc-quote">
                  <p :class="{ empty: !pick.note }">{{ pick.note || '馆员暂未填写推荐语，可以先从馆藏信息了解这本书。' }}</p>
                </div>
                <div class="dsc-pick-foot">
                  <span class="dsc-pill" :class="pick.available_count ? 'in' : 'out'">
                    {{ pick.available_count ? `在馆 ${pick.available_count} 册 · 可借` : '暂无可借 · 可预约' }}
                  </span>
                  <span class="dsc-pill shelf">{{ pickShelf }}</span>
                  <RouterLink class="dsc-btn dsc-btn-p" :to="bookLink(pick)">
                    <AppIcon name="search" :size="15" />查看馆藏
                  </RouterLink>
                  <div v-if="picks.length > 1" class="dsc-dots">
                    <button
                      v-for="(_, index) in picks"
                      :key="index"
                      type="button"
                      :class="{ on: index === pickIndex }"
                      :aria-label="`第 ${index + 1} 本`"
                      @click="selectPick(index)"
                    />
                  </div>
                </div>
              </div>
            </div>
            <div v-else class="dsc-rec-empty">
              <AppIcon name="books" :size="24" />
              <span>馆员正在整理推荐，去馆藏里逛逛吧。</span>
              <RouterLink to="/reader/catalog">浏览馆藏</RouterLink>
            </div>
          </section>

          <!-- 推荐书墙 -->
          <section class="dsc-card dsc-wall-card dsc-in dsc-in4">
            <div class="dsc-sec-head">
              <h2>为你推荐</h2>
              <p>馆员精选、热门借阅与新到图书</p>
              <RouterLink class="dsc-more" to="/reader/catalog">更多书目<AppIcon name="arrow-right" :size="13" /></RouterLink>
            </div>
            <div v-if="recommendations.length" class="dsc-wall">
              <RouterLink
                v-for="book in recommendations"
                :key="book.record_id"
                class="dsc-bk"
                :to="bookLink(book)"
              >
                <span class="dsc-bk-c">
                  <span v-if="newIds.has(book.record_id)" class="dsc-flag new">新书</span>
                  <span v-else-if="hotIds.has(book.record_id)" class="dsc-flag">热门</span>
                  <BookCover :title="book.title || '未知题名'" :author="book.author" size="card" />
                  <span class="dsc-bk-ov"><span>查看馆藏</span></span>
                </span>
                <span class="dsc-bk-i">
                  <strong>{{ book.title || '未知题名' }}</strong>
                  <em>{{ book.author || '佚名' }}</em>
                  <span class="dsc-bk-st" :class="{ zero: !book.available_count }">
                    {{ book.available_count ? `可借 ${book.available_count} 册` : '可预约' }}
                  </span>
                </span>
              </RouterLink>
            </div>
            <div v-else class="dsc-rec-empty">
              <AppIcon name="books" :size="24" />
              <span>暂无推荐书目，去馆藏里逛逛吧。</span>
              <RouterLink to="/reader/catalog">浏览馆藏</RouterLink>
            </div>
          </section>

          <section class="dsc-roam dsc-in dsc-in5">
            <div>
              <h3>还没看够？全馆 {{ formatNumber(home.stats.titles) }} 种书目等你翻</h3>
              <p>支持题名、责任者、出版者与分类筛选，也可用 ISBN 或条码精确检索</p>
            </div>
            <span class="dsc-roam-sp" aria-hidden="true">書</span>
            <RouterLink class="dsc-btn dsc-btn-go" to="/reader/catalog">进入馆藏检索 →</RouterLink>
          </section>
        </div>

        <!-- 侧栏 -->
        <aside class="dsc-side">
          <section class="dsc-card dsc-side-card dsc-in dsc-in3">
            <div class="dsc-sec-head">
              <h2>最近应还</h2>
              <RouterLink class="dsc-more" to="/reader/loans">全部借阅<AppIcon name="arrow-right" :size="12" /></RouterLink>
            </div>
            <div v-if="recentLoans.length" class="dsc-due">
              <RouterLink v-for="loan in recentLoans" :key="loan.id" class="dsc-due-row" to="/reader/loans">
                <span class="dsc-due-d" :class="{ soon: isDueSoon(loan) && !isOverdue(loan), over: isOverdue(loan) }">
                  <small>{{ dueParts(loan).month }}</small>
                  <b>{{ dueParts(loan).day }}</b>
                </span>
                <span class="dsc-due-main"><strong>{{ loan.title }}</strong></span>
                <span class="dsc-tag" :class="{ soon: isDueSoon(loan) && !isOverdue(loan), over: isOverdue(loan) }">{{ leftText(loan) }}</span>
              </RouterLink>
            </div>
            <div v-else class="dsc-rec-empty small">
              <AppIcon name="check-circle" :size="22" />
              <span>当前没有待还图书</span>
              <RouterLink to="/reader/catalog">去借一本</RouterLink>
            </div>
            <div v-if="recentLoans.length" class="dsc-card-foot">
              <RouterLink to="/reader/loans">去续借<AppIcon name="arrow-right" :size="12" /></RouterLink>
            </div>
          </section>

          <section v-if="ranking.length" class="dsc-card dsc-side-card dsc-in dsc-in4">
            <div class="dsc-sec-head">
              <h2>借阅榜</h2>
              <p class="dsc-side-cap">近 90 天</p>
            </div>
            <div class="dsc-rk-list">
              <RouterLink v-for="(book, index) in ranking" :key="book.record_id" class="dsc-rk" :to="bookLink(book)">
                <b class="dsc-rk-no" :class="{ top: index < 3 }">{{ String(index + 1).padStart(2, '0') }}</b>
                <span class="dsc-rk-cover"><BookCover :title="book.title || '未知题名'" :author="book.author" size="mini" /></span>
                <span class="dsc-rk-main">
                  <strong>{{ book.title || '未知题名' }}</strong>
                  <em>{{ book.author || '佚名' }}</em>
                </span>
                <span class="dsc-rk-n">
                  <i>{{ book.loan_count || 0 }} 次</i>
                  <span class="dsc-rk-bar"><i :style="{ width: `${Math.round(((book.loan_count || 0) / rankMax) * 100)}%` }" /></span>
                </span>
              </RouterLink>
            </div>
          </section>

          <section v-if="home.categories.length" class="dsc-card dsc-side-card dsc-in dsc-in4">
            <div class="dsc-sec-head">
              <h2>分类浏览</h2>
              <RouterLink class="dsc-more" to="/reader/catalog">全部分类<AppIcon name="arrow-right" :size="12" /></RouterLink>
            </div>
            <div class="dsc-cats">
              <RouterLink
                v-for="item in home.categories.slice(0, 8)"
                :key="item.key"
                class="dsc-cat"
                :to="{ path: '/reader/catalog', query: { category: item.key } }"
              >
                <b>{{ item.label }}</b>
                <s>{{ formatNumber(item.copies) }}</s>
                <span class="dsc-cat-t"><i :style="{ width: `${categoryPercent(item.copies)}%` }" /></span>
              </RouterLink>
            </div>
          </section>

          <section class="dsc-card dsc-side-card dsc-in dsc-in5">
            <div class="dsc-sec-head">
              <h2>我的阅读</h2>
              <RouterLink class="dsc-more" to="/reader/loans">借阅历史<AppIcon name="arrow-right" :size="12" /></RouterLink>
            </div>
            <div class="dsc-read-stats">
              <div class="dsc-rs"><b>{{ formatNumber(reader?.stats.total || 0) }}</b><span>累计借阅</span></div>
              <div class="dsc-rs"><b>{{ formatNumber(loans.length) }}</b><span>当前外借</span></div>
              <div class="dsc-rs"><b>{{ formatNumber(reader?.stats.overdue || 0) }}</b><span>逾期未还</span></div>
            </div>
            <p class="dsc-read-note">
              {{ reader?.stats.overdue ? '有图书已逾期，请尽快归还。' : `借期 ${reader?.rules.loan_days || 30} 天，可续借 ${reader?.rules.max_renew || 1} 次。` }}
            </p>
          </section>
        </aside>
      </div>
    </template>
  </div>
</template>
