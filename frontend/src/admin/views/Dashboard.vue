<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { RouterLink } from 'vue-router'
import AppIcon from '../../components/AppIcon.vue'
import BookCover from '../../components/BookCover.vue'
import TrendChart, { type ChartSeries } from '../../components/charts/TrendChart.vue'
import { dashboardState, loadDashboard } from '../../stores/dashboard'

const data = computed(() => dashboardState.data)
const pending = computed(() => dashboardState.loading || (!dashboardState.data && !dashboardState.error))
const failed = computed(() => Boolean(dashboardState.error) && !dashboardState.data)

const range = ref<7 | 14>(14)
const visible = reactive({ borrowed: true, returned: true })
const nf = new Intl.NumberFormat('zh-CN')

function fmt(value: number | null | undefined): string {
  return nf.format(Number(value || 0))
}

function shortDate(iso: string): string {
  const [, month, day] = iso.split('-')
  return `${Number(month)}/${Number(day)}`
}

function longDate(iso: string | null | undefined): string {
  if (!iso) return ''
  const [, month, day] = iso.split('-')
  return `${Number(month)}月${Number(day)}日`
}

const trendRows = computed(() => data.value?.trend.slice(-range.value) || [])
const labels = computed(() => trendRows.value.map((row) => shortDate(row.date)))
const borrowedValues = computed(() => trendRows.value.map((row) => row.borrowed))
const returnedValues = computed(() => trendRows.value.map((row) => row.returned))

const series = computed<ChartSeries[]>(() => {
  const rows: ChartSeries[] = []
  if (visible.borrowed) {
    rows.push({ key: 'borrowed', label: '外借', color: 'var(--primary)', values: borrowedValues.value })
  }
  if (visible.returned) {
    rows.push({ key: 'returned', label: '归还', color: 'var(--accent)', values: returnedValues.value })
  }
  return rows
})

function toggleSeries(key: 'borrowed' | 'returned') {
  if (visible[key] && Number(visible.borrowed) + Number(visible.returned) === 1) return
  visible[key] = !visible[key]
}

const borrowedTotal = computed(() => borrowedValues.value.reduce((sum, n) => sum + n, 0))
const returnedTotal = computed(() => returnedValues.value.reduce((sum, n) => sum + n, 0))
const peak = computed(() => Math.max(0, ...borrowedValues.value))
const peakIndex = computed(() => borrowedValues.value.indexOf(peak.value))
const peakDate = computed(() => {
  const row = trendRows.value[peakIndex.value]
  return row ? longDate(row.date) : ''
})

const deltaMeta = computed(() => {
  const k = data.value?.kpis
  if (!k) return { borrowed: { diff: 0, cls: 'flat' }, returned: { diff: 0, cls: 'flat' } }
  const borrowed = k.today_borrowed - k.yesterday_borrowed
  const returned = k.today_returned - k.yesterday_returned
  return {
    borrowed: { diff: borrowed, cls: borrowed > 0 ? '' : borrowed < 0 ? 'down' : 'flat' },
    returned: { diff: returned, cls: returned > 0 ? '' : returned < 0 ? 'down' : 'flat' },
  }
})

function deltaText(diff: number): string {
  if (diff === 0) return '与昨日持平'
  return `${diff > 0 ? '+' : ''}${diff} 册`
}

const COLLECTION_COLORS = ['var(--primary)', '#6fa08c', 'var(--accent)', '#c98a5e', 'var(--line-strong)']

const collection = computed(() => {
  const source = data.value?.collection
  const total = source?.total || 0
  if (!source || !total) return { total: 0, segments: [] as Array<{ label: string; key: string; copies: number; percent: number; color: string }> }
  const rows = [...source.rows].sort((a, b) => b.copies - a.copies)
  const top = rows.slice(0, 4)
  const rest = rows.slice(4).reduce((sum, row) => sum + row.copies, 0)
  const segments = top.map((row, index) => ({
    label: row.label,
    key: row.key,
    copies: row.copies,
    percent: Math.round((row.copies / total) * 100),
    color: COLLECTION_COLORS[index],
  }))
  if (rest > 0) {
    segments.push({
      label: '其他',
      key: '',
      copies: rest,
      percent: Math.round((rest / total) * 100),
      color: COLLECTION_COLORS[4],
    })
  }
  return { total, segments }
})

const donutGradient = computed(() => {
  const { total, segments } = collection.value
  if (!segments.length) return 'conic-gradient(var(--bg-deep) 0 100%)'
  let cursor = 0
  const parts = segments.map((segment, index) => {
    const from = cursor
    cursor += (segment.copies / total) * 100
    const to = index === segments.length - 1 ? 100 : cursor
    return `${segment.color} ${from.toFixed(2)}% ${to.toFixed(2)}%`
  })
  return `conic-gradient(${parts.join(', ')})`
})

const otherTodos = computed(() => data.value?.todos.filter((todo) => todo.kind !== 'overdue') || [])

const TODO_META: Record<string, { icon: string; tone: string }> = {
  compensation: { icon: 'receipt', tone: 'amber' },
  suspended: { icon: 'users', tone: 'primary' },
  reservation: { icon: 'bookmark', tone: 'info' },
  ready_reservation: { icon: 'bookmark', tone: 'info' },
}

interface ReminderRow {
  key: string
  kind: 'overdue' | 'todo'
  title: string
  meta: string
  tone: string
  icon: string
  link: string
  recordId?: number
}

const reminderRows = computed<ReminderRow[]>(() => {
  const rows: ReminderRow[] = []
  for (const item of data.value?.overdue_items || []) {
    rows.push({
      key: `overdue-${item.loan_id}`,
      kind: 'overdue',
      title: item.title || '未知题名',
      meta: `${item.reader_name || item.reader_card_no || '读者'} · 已逾期 ${item.days_late} 天`,
      tone: 'danger',
      icon: 'alert',
      link: '/admin/circulation?tab=overdue',
      recordId: item.record_id,
    })
  }
  for (const todo of otherTodos.value) {
    rows.push({
      key: `todo-${todo.kind}`,
      kind: 'todo',
      title: todo.label,
      meta: `${todo.count} 项 · ${todo.detail}`,
      tone: TODO_META[todo.kind]?.tone || 'primary',
      icon: TODO_META[todo.kind]?.icon || 'info',
      link: todo.link,
    })
  }
  return rows
})

const visibleReminders = computed(() => reminderRows.value.slice(0, 4))
const hiddenReminders = computed(() => Math.max(0, reminderRows.value.length - 4))
const hiddenLink = computed(() => reminderRows.value[4]?.link || '/admin/circulation')
const hiddenLinkLabel = computed(() =>
  hiddenLink.value.startsWith('/admin/reservations')
    ? '前往预约到书'
    : hiddenLink.value.startsWith('/admin/readers')
      ? '前往读者'
      : '前往借还台',
)

const pendingCount = computed(() => {
  const overdue = data.value?.kpis.overdue || 0
  return overdue + otherTodos.value.reduce((sum, todo) => sum + todo.count, 0)
})

function collectionLink(key: string): string {
  return key ? `/admin/records?class=${encodeURIComponent(key)}` : '/admin/records'
}

/** 图表高度跟着视口：2K/1440p 给足 300，1080P 全屏 236，常见 900px 可用高度收到 180 保证一屏 */
const viewportH = ref(typeof window === 'undefined' ? 900 : window.innerHeight)
const chartHeight = computed(() => {
  const h = viewportH.value
  if (h >= 1300) return 300
  if (h >= 1000) return 236
  if (h >= 900) return 180
  return 166
})
function trackViewport() {
  viewportH.value = window.innerHeight
}

onMounted(() => {
  trackViewport()
  window.addEventListener('resize', trackViewport)
  void loadDashboard()
})
onBeforeUnmount(() => window.removeEventListener('resize', trackViewport))
</script>

<template>
  <section class="view">
    <div v-if="pending" class="dash-loading" aria-busy="true" aria-label="正在读取工作台数据">
      <div class="skeleton-grid">
        <div v-for="i in 4" :key="i" class="skeleton skeleton-stat" />
      </div>
      <div class="skeleton-row">
        <div class="skeleton skeleton-block" />
        <div class="skeleton skeleton-block" />
      </div>
      <div class="skeleton skeleton-block" style="height: 180px" />
    </div>

    <div v-else-if="failed" class="card dash-error">
      <span class="error-icon"><AppIcon name="alert" /></span>
      <strong>工作台数据暂时读取失败</strong>
      <p>{{ dashboardState.error }}</p>
      <button class="btn btn-primary btn-sm" type="button" @click="loadDashboard(true)">重新加载</button>
    </div>

    <template v-else-if="data">
      <div v-if="dashboardState.error" class="dash-banner" role="status">
        <AppIcon name="alert" :size="15" />
        <span>数据可能不是最新：{{ dashboardState.error }}</span>
        <button type="button" @click="loadDashboard(true)">重试</button>
      </div>

      <div class="stat-grid">
        <RouterLink class="stat-card" :to="{ path: '/admin/circulation', query: { tab: 'today', mode: 'borrow' } }">
          <div class="stat-top">
            <div>
              <div class="stat-label">今日外借</div>
              <div class="stat-value">{{ fmt(data.kpis.today_borrowed) }}<small>册</small></div>
            </div>
            <span class="stat-icon"><AppIcon name="books" /></span>
          </div>
          <div class="stat-foot">
            <span class="delta" :class="deltaMeta.borrowed.cls">
              <AppIcon name="trend-up" />
              {{ deltaText(deltaMeta.borrowed.diff) }}
            </span>
            <span>昨日 {{ fmt(data.kpis.yesterday_borrowed) }} 册</span>
          </div>
        </RouterLink>

        <RouterLink class="stat-card" :to="{ path: '/admin/circulation', query: { tab: 'today', mode: 'return' } }">
          <div class="stat-top">
            <div>
              <div class="stat-label">今日归还</div>
              <div class="stat-value">{{ fmt(data.kpis.today_returned) }}<small>册</small></div>
            </div>
            <span class="stat-icon blue"><AppIcon name="rotate" /></span>
          </div>
          <div class="stat-foot">
            <span class="delta" :class="deltaMeta.returned.cls">
              <AppIcon name="trend-up" />
              {{ deltaText(deltaMeta.returned.diff) }}
            </span>
            <span>昨日 {{ fmt(data.kpis.yesterday_returned) }} 册</span>
          </div>
        </RouterLink>

        <RouterLink class="stat-card" :to="{ path: '/admin/circulation', query: { tab: 'active' } }">
          <div class="stat-top">
            <div>
              <div class="stat-label">外借册数</div>
              <div class="stat-value">{{ fmt(data.kpis.active_loans) }}<small>册</small></div>
            </div>
            <span class="stat-icon amber"><AppIcon name="layers" /></span>
          </div>
          <div class="stat-foot">
            <span class="badge badge-primary no-dot">{{ fmt(data.kpis.readers_active) }} 位读者外借</span>
            <span>读者 {{ fmt(data.kpis.reader_total) }} 位</span>
          </div>
        </RouterLink>

        <RouterLink
          class="stat-card"
          :class="{ 'is-danger': data.kpis.overdue > 0 }"
          :to="{ path: '/admin/circulation', query: { tab: 'overdue' } }"
        >
          <div class="stat-top">
            <div>
              <div class="stat-label">逾期未还</div>
              <div class="stat-value">{{ fmt(data.kpis.overdue) }}<small>册</small></div>
            </div>
            <span class="stat-icon rose"><AppIcon name="alert" /></span>
          </div>
          <div class="stat-foot">
            <span class="delta" :class="data.kpis.overdue > 0 ? 'down' : 'flat'">
              <AppIcon name="alert" />
              {{ data.kpis.overdue > 0 ? '需要跟进' : '全部按期' }}
            </span>
            <span class="stat-link">去处理 <AppIcon name="arrow-right" /></span>
          </div>
        </RouterLink>
      </div>

      <div class="dash-row">
        <article class="card trend-card">
          <div class="card-head">
            <div>
              <div class="card-title">流通趋势</div>
              <div class="card-sub">每日外借与归还 · 本地日口径</div>
            </div>
            <div class="segmented" role="group" aria-label="趋势时间范围">
              <button type="button" :class="{ 'is-active': range === 7 }" @click="range = 7">近 7 天</button>
              <button type="button" :class="{ 'is-active': range === 14 }" @click="range = 14">近 14 天</button>
            </div>
          </div>

          <div class="trend-summary">
            <div class="ts-item"><strong>{{ fmt(borrowedTotal) }}</strong><span>外借累计 · 册</span></div>
            <div class="ts-divider" />
            <div class="ts-item"><strong>{{ fmt(returnedTotal) }}</strong><span>归还累计 · 册</span></div>
            <div class="ts-divider" />
            <div class="ts-item"><strong>{{ fmt(peak) }}</strong><span>单日峰值 · {{ peakDate }}</span></div>
            <!-- 图例并入统计行：1080P 下省一整行高度（紧凑档样式见 admin.css） -->
            <div class="trend-legend">
              <button type="button" :class="{ 'is-off': !visible.borrowed }" @click="toggleSeries('borrowed')">
                <span class="legend-dot" style="background: var(--primary)" />
                外借
              </button>
              <button type="button" :class="{ 'is-off': !visible.returned }" @click="toggleSeries('returned')">
                <span class="legend-dot" style="background: var(--accent)" />
                归还
              </button>
            </div>
          </div>

          <div v-if="borrowedTotal + returnedTotal > 0" class="trend-chart">
            <TrendChart :labels="labels" :series="series" :height="chartHeight" unit="册" />
          </div>
          <div v-else class="empty-state">
            <AppIcon name="chart" />
            <strong>外借第一本书后，这里会出现曲线</strong>
            <p>今日的外借与归还按天累计在趋势中。</p>
          </div>
        </article>

        <article class="card donut-card">
          <div class="card-head">
            <div>
              <div class="card-title">馆藏结构</div>
              <div class="card-sub">按中图法大类 · {{ data.library?.name || '全部图书库' }}</div>
            </div>
            <RouterLink class="icon-btn" to="/admin/records" title="查看图书库" aria-label="查看图书库">
              <AppIcon name="arrow-right" />
            </RouterLink>
          </div>
          <div class="card-body">
            <template v-if="collection.segments.length">
              <div class="donut-flex">
                <div class="donut" :style="{ background: donutGradient }">
                  <div class="donut-center">
                    <strong>{{ fmt(collection.total) }}</strong>
                    <span>在架馆藏 · 册</span>
                  </div>
                </div>
                <ul class="donut-legend collection-legend">
                  <li v-for="segment in collection.segments" :key="segment.label">
                    <RouterLink class="lg-link" :to="collectionLink(segment.key)">
                      <span class="legend-dot" :style="{ background: segment.color }" />
                      <span class="lg-label">{{ segment.label }}</span>
                      <span class="lg-count">{{ fmt(segment.copies) }}</span>
                      <span class="lg-pct">{{ segment.percent }}%</span>
                    </RouterLink>
                  </li>
                </ul>
              </div>
              <div class="donut-foot">
                <span>共 <strong>{{ fmt(data.kpis.title_count) }}</strong> 种书目</span>
                <span>在架 {{ fmt(collection.total) }} 册</span>
              </div>
            </template>
            <div v-else class="empty-state">
              <AppIcon name="layers" />
              <strong>当前没有在架馆藏</strong>
              <p>完成编目并录入馆藏后，分类结构会显示在这里。</p>
            </div>
          </div>
        </article>
      </div>

      <div class="dash-row-2">
        <article class="card">
          <div class="card-head">
            <div>
              <div class="card-title">逾期与待办</div>
              <div class="card-sub">建议优先处理的流通事务</div>
            </div>
            <div class="card-head-actions">
              <span v-if="pendingCount" class="todo-count">{{ pendingCount }}</span>
              <RouterLink class="text-link" to="/admin/circulation">查看借还 <AppIcon name="chevron-right" /></RouterLink>
            </div>
          </div>

          <div v-if="visibleReminders.length" class="reminder-list">
            <div v-for="row in visibleReminders" :key="row.key" class="reminder-item">
              <BookCover v-if="row.kind === 'overdue' && row.recordId" :title="row.title" />
              <span v-else class="reminder-icon" :class="row.tone">
                <AppIcon :name="row.icon" />
              </span>
              <div class="reminder-info">
                <strong>{{ row.title }}</strong>
                <span>{{ row.meta }}</span>
              </div>
              <div class="reminder-actions">
                <RouterLink class="btn btn-sm btn-outline" :to="row.link">
                  {{ row.kind === 'overdue' ? '处理' : '查看' }}
                  <AppIcon name="chevron-right" />
                </RouterLink>
              </div>
            </div>
            <div v-if="hiddenReminders" class="list-more">
              另有 {{ hiddenReminders }} 项待办，
              <RouterLink :to="hiddenLink">{{ hiddenLinkLabel }}</RouterLink>
            </div>
          </div>
          <div v-else class="empty-state">
            <AppIcon name="check-circle" />
            <strong>今天没有待办</strong>
            <p>逾期、预约到书、赔偿与暂停读者都会汇总在这里。</p>
          </div>
        </article>

        <article class="card">
          <div class="card-head">
            <div>
              <div class="card-title">热门借阅</div>
              <div class="card-sub">近 30 天借阅次数</div>
            </div>
            <span class="badge badge-neutral no-dot">TOP 5</span>
          </div>

          <div v-if="data.hot_books.length" class="hot-list">
            <RouterLink
              v-for="(book, index) in data.hot_books"
              :key="book.record_id"
              class="hot-row"
              :to="`/admin/records/${book.record_id}`"
            >
              <span class="hot-rank">{{ index + 1 }}</span>
              <BookCover :title="book.title" />
              <div class="hot-info">
                <strong>{{ book.title }}</strong>
                <span>
                  {{ book.author || '佚名' }} ·
                  <span class="hot-avail" :class="{ 'is-zero': !book.available_copies }">
                    {{ book.available_copies ? `在馆 ${book.available_copies}` : '暂无可借' }}
                  </span>
                </span>
              </div>
              <span class="hot-count">{{ fmt(book.loans_30d) }}<small>次借阅</small></span>
            </RouterLink>
          </div>
          <div v-else class="empty-state">
            <AppIcon name="star" />
            <strong>当前没有借阅数据</strong>
            <p>第一笔外借后，热门榜会按近 30 天自动生成。</p>
          </div>
        </article>

        <!-- 新书上架：1080P 紧凑档下并入同行第三列（样式见 admin.css 工作台紧凑档） -->
        <article class="card dash-new-card">
          <div class="card-head">
            <div>
              <div class="card-title">新书上架</div>
              <div class="card-sub">最近入藏 · {{ data.library?.name || '全部图书库' }}</div>
            </div>
            <RouterLink class="text-link" to="/admin/records">查看全部 <AppIcon name="arrow-right" /></RouterLink>
          </div>
          <div v-if="data.new_books.length" class="new-books">
            <RouterLink
              v-for="book in data.new_books.slice(0, 4)"
              :key="book.record_id"
              class="new-book"
              :to="`/admin/records/${book.record_id}`"
            >
              <BookCover :title="book.title" :author="book.author" />
              <div class="new-book-info">
                <strong>{{ book.title }}</strong>
                <span>{{ book.author || '佚名' }}</span>
                <span class="badge badge-neutral no-dot">
                  {{ book.class_no || '未分类' }}
                  <template v-if="book.created_at"> · {{ longDate(book.created_at) }}</template>
                </span>
              </div>
            </RouterLink>
          </div>
          <div v-else class="empty-state">
            <AppIcon name="inbox" />
            <strong>当前没有新书</strong>
            <p>完成一次编目后，最近入藏的书目会出现在这里。</p>
          </div>
        </article>
      </div>
    </template>
  </section>
</template>
