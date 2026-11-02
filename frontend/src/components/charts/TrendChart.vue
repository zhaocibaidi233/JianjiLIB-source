<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { smoothPath } from '../../lib/curve'

export interface ChartSeries {
  key: string
  label: string
  color: string
  values: number[]
}

const props = withDefaults(defineProps<{
  labels: string[]
  series: ChartSeries[]
  height?: number
  unit?: string
}>(), {
  height: 244,
  unit: '册',
})

const BASE_W = 720
const PAD = { top: 18, right: 16, bottom: 34, left: 40 }
let uidSeed = 0
const uid = `trend-${(uidSeed += 1)}-${Math.random().toString(36).slice(2, 7)}`

const activeIndex = ref<number | null>(null)
const wrapRef = ref<HTMLElement | null>(null)

// 宽度跟随容器实际像素：viewBox 用真实宽度，图形不会被等比缩放挤到中间，
// 坐标文字也能保持 11.5px 的实际大小（原来 720 宽 viewBox 塞进 830px 卡里会缩水）。
const boxW = ref(BASE_W)
const W = computed(() => boxW.value)
const H = computed(() => props.height)
const plotW = computed(() => W.value - PAD.left - PAD.right)
const plotH = computed(() => H.value - PAD.top - PAD.bottom)

/** Y 轴取整刻度：峰值 13 时给 0/5/10/15，而不是 0/4/8/12/15 */
const scale = computed(() => {
  const values = props.series.flatMap((item) => item.values)
  const rawMax = Math.max(0, ...values)
  const rough = Math.max(1, rawMax) / 4
  const mag = 10 ** Math.floor(Math.log10(rough))
  const norm = rough / mag
  const stepSize = (norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 2.5 ? 2.5 : norm <= 5 ? 5 : 10) * mag
  const max = Math.max(stepSize * 2, Math.ceil(rawMax / stepSize) * stepSize)
  const ticks: number[] = []
  for (let value = 0; value <= max + 1e-9; value += stepSize) {
    ticks.push(Number(value.toFixed(4)))
  }
  return { max, ticks }
})

const step = computed(() => plotW.value / Math.max(props.labels.length - 1, 1))
const xOf = (index: number) => PAD.left + index * step.value
const yOf = (value: number) => PAD.top + (1 - value / scale.value.max) * plotH.value

// 平滑路径在 lib/curve.ts（纯函数 + 单测）：Catmull-Rom 加单调钳制，
// 尖峰不再冲出实际数据点（钳制理由见彼处注释）。

const rendered = computed(() =>
  props.series.map((item, index) => {
    const values = item.values.length ? item.values : new Array(props.labels.length).fill(0)
    const points = values.map((value, i) => [xOf(i), yOf(value)] as [number, number])
    const line = smoothPath(points)
    const base = PAD.top + plotH.value
    const area = line
      ? `${line} L ${points[points.length - 1][0]},${base} L ${points[0][0]},${base} Z`
      : ''
    return { ...item, index, points, line, area }
  }),
)

const yTicks = computed(() => scale.value.ticks.map((value) => ({
  y: PAD.top + (1 - value / scale.value.max) * plotH.value,
  label: value >= 1000 ? `${(value / 1000).toFixed(1)}k` : String(Math.round(value)),
})))

const xTicks = computed(() => {
  const total = props.labels.length
  const indexes = new Set<number>()
  if (total <= 7) {
    props.labels.forEach((_, i) => indexes.add(i))
  } else {
    for (let i = 0; i < 7; i += 1) {
      indexes.add(Math.round((i * (total - 1)) / 6))
    }
  }
  return props.labels.map((label, i) => ({
    x: xOf(i),
    label: indexes.has(i) ? label : '',
  }))
})

const active = computed(() => {
  const index = activeIndex.value
  if (index === null) return null
  const points = rendered.value.map((item) => ({
    key: item.key,
    label: item.label,
    color: item.color,
    value: Number(item.values[index] ?? 0),
    y: item.points[index]?.[1] ?? 0,
  }))
  const x = xOf(index)
  return {
    index,
    label: props.labels[index] || '',
    x,
    left: Math.min(94, Math.max(6, (x / W.value) * 100)),
    top: Math.min(88, Math.max(12, (Math.min(...points.map((p) => p.y)) / H.value) * 100)),
    points,
  }
})

let observer: ResizeObserver | null = null
onMounted(() => {
  const el = wrapRef.value
  if (!el || typeof ResizeObserver === 'undefined') return
  observer = new ResizeObserver((entries) => {
    const width = Math.round(entries[0].contentRect.width)
    if (width > 0 && width !== boxW.value) boxW.value = width
  })
  observer.observe(el)
})
onBeforeUnmount(() => observer?.disconnect())

function onMove(event: PointerEvent) {
  const svg = wrapRef.value?.querySelector('svg')
  if (!svg || !props.labels.length) return
  const rect = svg.getBoundingClientRect()
  const x = ((event.clientX - rect.left) / rect.width) * W.value
  const index = Math.round((x - PAD.left) / step.value)
  activeIndex.value = Math.max(0, Math.min(props.labels.length - 1, index))
}

function onLeave() {
  activeIndex.value = null
}
</script>

<template>
  <div ref="wrapRef" class="chart-wrap" @pointermove="onMove" @pointerleave="onLeave">
    <svg
      class="chart-svg"
      :viewBox="`0 0 ${W} ${H}`"
      role="img"
      :aria-label="`流通趋势，${labels[0] || ''} 至 ${labels[labels.length - 1] || ''}`"
    >
      <defs>
        <linearGradient v-for="item in rendered" :id="`${uid}-${item.key}`" :key="item.key" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" :stop-color="item.color" stop-opacity="0.2" />
          <stop offset="100%" :stop-color="item.color" stop-opacity="0" />
        </linearGradient>
      </defs>
      <g class="chart-grid">
        <line v-for="tick in yTicks" :key="tick.y" :x1="PAD.left" :x2="W - PAD.right" :y1="tick.y" :y2="tick.y" />
      </g>
      <g class="chart-axis chart-axis-y">
        <text v-for="tick in yTicks" :key="`y-${tick.y}`" :x="PAD.left - 9" :y="tick.y + 3.5" text-anchor="end">
          {{ tick.label }}
        </text>
      </g>
      <g class="chart-axis">
        <text v-for="tick in xTicks" :key="`x-${tick.x}`" :x="tick.x" :y="H - 8" text-anchor="middle">
          {{ tick.label }}
        </text>
      </g>
      <g v-for="item in rendered" :key="item.key">
        <path v-if="item.area" :d="item.area" :fill="`url(#${uid}-${item.key})`" />
        <path
          :d="item.line"
          fill="none"
          :stroke="item.color"
          stroke-width="2.2"
          stroke-linecap="round"
          vector-effect="non-scaling-stroke"
        />
      </g>
      <template v-if="active">
        <line
          :x1="active.x"
          :x2="active.x"
          :y1="PAD.top"
          :y2="PAD.top + plotH"
          stroke="var(--muted)"
          stroke-opacity="0.28"
          stroke-dasharray="3 4"
        />
        <circle
          v-for="point in active.points"
          :key="`dot-${point.key}`"
          :cx="active.x"
          :cy="point.y"
          r="3.6"
          fill="var(--surface)"
          :stroke="point.color"
          stroke-width="2"
          vector-effect="non-scaling-stroke"
        />
      </template>
    </svg>
    <div v-if="active" class="chart-tip chart-tip-live" :style="{ left: `${active.left}%`, top: `${active.top}%` }">
      <span class="tip-date">{{ active.label }}</span>
      <span v-for="point in active.points" :key="point.key" class="tip-row">
        <i :style="{ background: point.color }" />
        {{ point.label }}
        <strong>{{ point.value }}</strong>
        {{ unit }}
      </span>
    </div>
  </div>
</template>

<style scoped>
.chart-wrap { position: relative; }
.chart-svg { display: block; width: 100%; height: auto; overflow: visible; touch-action: pan-y; }
.chart-axis text { font-size: 11.5px; }
.chart-axis-y text { font-variant-numeric: tabular-nums; }

/* SVG 坐标文字会随 viewBox 一起缩小，窄屏需要单独放大 */
@media (max-width: 760px) {
  .chart-axis text { font-size: 20px; }
}
@media (min-width: 481px) and (max-width: 760px) {
  .chart-axis text { font-size: 16px; }
}
.chart-tip-live {
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding: 8px 10px;
  min-width: 116px;
  opacity: 1;
  transform: translate(-50%, -118%);
  transition: left 0.1s linear, top 0.1s linear;
  pointer-events: none;
}
.tip-date { font-size: 10px; color: rgba(255, 255, 255, 0.58); margin-bottom: 1px; }
.tip-row {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 11px;
  white-space: nowrap;
  color: rgba(255, 255, 255, 0.82);
}
.tip-row i { width: 6px; height: 6px; border-radius: 2px; flex: none; }
.tip-row strong { margin-left: auto; font-weight: 700; color: #fff; font-variant-numeric: tabular-nums; }
</style>
