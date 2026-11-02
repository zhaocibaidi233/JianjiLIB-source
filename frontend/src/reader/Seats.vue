<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import AppIcon from '../components/AppIcon.vue'
import { errorMessage, readerGet, readerPost } from '../lib/api'
import { addDaysIso, formatDate, todayIso } from '../lib/format'
import { readerAuth } from '../lib/readerAuth'
import { toast, toastError } from '../lib/toast'

interface Slot { start: string; end: string }
interface SeatArea {
  id: number
  name: string
  floor: string | null
  description: string | null
  rows: number
  cols: number
  open_time: string
  close_time: string
  slots: Slot[]
  enabled: boolean
}
interface Seat {
  id: number
  code: string
  row: number
  col: number
  kind: string
  enabled: boolean
  booked: boolean
  mine: boolean
  booking_id: number | null
}
interface Booking {
  id: number
  seat_id: number
  seat_code: string | null
  area_id: number | null
  area_name: string | null
  floor: string | null
  booking_date: string
  start_time: string
  end_time: string
  status: string
  status_label: string
  checked_in_at: string | null
}
interface BanInfo {
  banned: boolean
  until: string | null
  no_show_count: number
}
interface SeatMap {
  area: SeatArea
  date: string
  slot: Slot
  seats: Seat[]
  my_booking: Booking | null
  ban: BanInfo
}

const areas = ref<SeatArea[]>([])
const selectedAreaId = ref<number | null>(null)
const selectedDate = ref(todayIso())
const selectedSlot = ref('')
const map = ref<SeatMap | null>(null)
const bookings = ref<Booking[]>([])
const ban = ref<BanInfo>({ banned: false, until: null, no_show_count: 0 })
const selectedSeat = ref<Seat | null>(null)
const loadingAreas = ref(true)
const loadingMap = ref(false)
const loadingBookings = ref(false)
const error = ref('')
const mapError = ref('')
const bookingBusy = ref(false)
const busyId = ref<number | null>(null)

const reader = computed(() => readerAuth.reader)
const occupiedCount = computed(() =>
  map.value ? map.value.seats.filter(seat => seat.booked || seat.mine).length : 0,
)
const selectedArea = computed(() =>
  areas.value.find((area) => area.id === selectedAreaId.value) || null,
)
const slots = computed(() => selectedArea.value?.slots || [])
let mapRequest = 0
const mapContext = ref('')
function contextKey() {
  return JSON.stringify([selectedAreaId.value, selectedDate.value, selectedSlot.value])
}
const mapMatchesContext = computed(() => Boolean(map.value)
  && mapContext.value === contextKey()
  && map.value?.area.id === selectedAreaId.value
  && map.value?.date === selectedDate.value
  && map.value?.slot.start === selectedSlot.value)
const canBook = computed(() =>
  Boolean(selectedSeat.value && map.value?.seats.some((seat) => seat === selectedSeat.value
    && seat.enabled && !seat.booked))
  && mapMatchesContext.value
  && !loadingMap.value
  && !ban.value.banned
  && !map.value?.my_booking
  && reader.value?.status === 'ACTIVE'
  && !bookingBusy.value,
)

const dayOptions = computed(() => {
  const out = []
  const today = todayIso()
  for (let index = 0; index < 7; index += 1) {
    const iso = addDaysIso(index)
    const date = new Date(`${iso}T00:00:00`)
    const weekday = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'][date.getDay()]
    out.push({
      iso,
      weekday: index === 0 ? '今天' : index === 1 ? '明天' : weekday,
      label: `${date.getMonth() + 1}/${date.getDate()}`,
      isToday: iso === today,
    })
  }
  return out
})

function pickInitialSlot(area: SeatArea, day: string): string {
  if (!area.slots.length) return ''
  if (day !== todayIso()) return area.slots[0].start
  const now = new Date()
  const hhmm = `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}`
  return area.slots.find((slot) => slot.start >= hhmm)?.start || area.slots[area.slots.length - 1].start
}

async function loadAreas() {
  loadingAreas.value = true
  error.value = ''
  try {
    const data = await readerGet<{ areas: SeatArea[]; ban: BanInfo; reader_status: string }>(
      '/portal/seats/areas',
    )
    areas.value = data.areas
    ban.value = data.ban
    if (!selectedAreaId.value && data.areas.length) {
      selectedAreaId.value = data.areas[0].id
      selectedSlot.value = pickInitialSlot(data.areas[0], selectedDate.value)
    } else if (selectedArea.value) {
      if (!selectedArea.value.slots.some((slot) => slot.start === selectedSlot.value)) {
        selectedSlot.value = pickInitialSlot(selectedArea.value, selectedDate.value)
      }
    }
  } catch (e) {
    error.value = errorMessage(e)
  } finally {
    loadingAreas.value = false
  }
}

function invalidateMap() {
  mapRequest += 1
  selectedSeat.value = null
  map.value = null
  mapContext.value = ''
  mapError.value = ''
  loadingMap.value = Boolean(selectedAreaId.value && selectedSlot.value)
}

async function loadMap() {
  invalidateMap()
  const request = mapRequest
  const context = contextKey()
  const params = { date: selectedDate.value, area_id: selectedAreaId.value, slot: selectedSlot.value }
  if (!params.area_id || !params.slot) return
  const current = () => request === mapRequest && context === contextKey()
  try {
    const data = await readerGet<SeatMap>('/portal/seats', params)
    if (!current()) return
    if (data.area.id !== params.area_id || data.date !== params.date || data.slot.start !== params.slot) {
      throw new Error('座位图与当前预约条件不一致，请重新加载')
    }
    map.value = data
    mapContext.value = context
    ban.value = data.ban
  } catch (e) {
    if (current()) mapError.value = errorMessage(e)
  } finally {
    if (current()) loadingMap.value = false
  }
}

async function loadBookings() {
  loadingBookings.value = true
  try {
    const data = await readerGet<{ items: Booking[]; ban: BanInfo }>('/portal/seats/bookings')
    bookings.value = data.items
    ban.value = data.ban
  } catch {
    bookings.value = []
  } finally {
    loadingBookings.value = false
  }
}

async function refreshAll() {
  await Promise.all([loadMap(), loadBookings()])
}

function chooseDate(iso: string) {
  selectedDate.value = iso
  if (selectedArea.value) {
    const valid = selectedArea.value.slots.some((slot) => slot.start === selectedSlot.value)
    if (!valid) selectedSlot.value = pickInitialSlot(selectedArea.value, iso)
  }
}

function onAreaChange(event: Event) {
  const id = Number((event.target as HTMLSelectElement).value)
  selectedAreaId.value = id || null
  const area = selectedArea.value
  if (area) selectedSlot.value = pickInitialSlot(area, selectedDate.value)
}

function onSlotChange(event: Event) {
  selectedSlot.value = (event.target as HTMLSelectElement).value
}

function chooseSeat(seat: Seat) {
  if (loadingMap.value || !mapMatchesContext.value || !map.value?.seats.includes(seat)
    || seat.booked || !seat.enabled || map.value.my_booking) return
  selectedSeat.value = seat
}

async function submitBooking() {
  if (!selectedSeat.value || !canBook.value || !mapMatchesContext.value) return
  const context = contextKey()
  const request = mapRequest
  const payload = {
    seat_id: selectedSeat.value.id,
    booking_date: selectedDate.value,
    start_time: selectedSlot.value,
  }
  bookingBusy.value = true
  try {
    await readerPost('/portal/seats/bookings', payload)
    toast('座位预约成功')
    if (context === contextKey() && request === mapRequest) {
      selectedSeat.value = null
      await refreshAll()
    } else {
      await loadBookings()
    }
  } catch (e) {
    toastError(errorMessage(e))
  } finally {
    bookingBusy.value = false
  }
}

async function cancelBooking(row: Booking) {
  if (busyId.value) return
  busyId.value = row.id
  try {
    await readerPost(`/portal/seats/bookings/${row.id}/cancel`)
    toast('座位预约已取消')
    await refreshAll()
  } catch (e) {
    toastError(errorMessage(e))
  } finally {
    busyId.value = null
  }
}

async function checkIn(row: Booking) {
  if (busyId.value) return
  busyId.value = row.id
  try {
    await readerPost(`/portal/seats/bookings/${row.id}/check-in`)
    toast('签到成功')
    await refreshAll()
  } catch (e) {
    toastError(errorMessage(e))
  } finally {
    busyId.value = null
  }
}

function isActive(row: Booking): boolean {
  return ['BOOKED', 'CHECKED_IN'].includes(row.status)
}

function canCheckIn(row: Booking): boolean {
  if (row.status !== 'BOOKED' || row.booking_date !== todayIso()) return false
  const start = new Date(`${row.booking_date}T${row.start_time}:00`).getTime()
  const minutes = (Date.now() - start) / 60_000
  return minutes >= -30 && minutes <= 30
}

// 同步撤销旧选座，异步合并区域/时段连续变更，只请求最终上下文。
watch([selectedAreaId, selectedDate, selectedSlot], invalidateMap, { flush: 'sync' })
watch([selectedAreaId, selectedDate, selectedSlot], () => void loadMap())

onMounted(async () => {
  await loadAreas()
  await loadBookings()
})

onBeforeUnmount(() => {
  mapRequest += 1
})
</script>

<template>
  <div class="reader-view">
    <header class="reader-page-head">
      <div>
        <h1>座位预约</h1>
        <p>选择区域、日期与时段，到馆后签到使用。</p>
      </div>
      <span class="badge badge-neutral no-dot">{{ selectedArea?.floor || '—' }}</span>
    </header>

    <div v-if="loadingAreas" class="reader-loading"><span class="spinner" />正在读取座位区域…</div>
    <div v-else-if="error" class="reader-empty">
      <AppIcon name="alert" /><strong>座位信息读取失败</strong><p>{{ error }}</p>
      <button class="btn btn-outline btn-sm" type="button" @click="loadAreas">重新加载</button>
    </div>
    <template v-else>
      <div v-if="ban.banned" class="seat-ban" style="margin-bottom: 14px">
        <AppIcon name="alert" />
        因连续 3 次未签到，座位预约功能暂停至 {{ ban.until }}。仍可查看其他区域。
      </div>

      <div class="reader-date-row">
        <button
          v-for="day in dayOptions"
          :key="day.iso"
          class="reader-date-chip"
          :class="{ 'is-active': selectedDate === day.iso }"
          type="button"
          @click="chooseDate(day.iso)"
        >
          <strong>{{ day.weekday }}</strong>
          <span>{{ day.label }}</span>
        </button>
      </div>

      <div class="seat-toolbar">
        <select class="select" :value="selectedAreaId || ''" aria-label="座位区域" @change="onAreaChange">
          <option v-for="area in areas" :key="area.id" :value="area.id">{{ area.name }}</option>
        </select>
        <select class="select" :value="selectedSlot" aria-label="预约时段" @change="onSlotChange">
          <option v-for="slot in slots" :key="slot.start" :value="slot.start">{{ slot.start }}–{{ slot.end }}</option>
        </select>
        <span v-if="selectedArea" class="page-tools-note">
          <AppIcon name="clock" />开放 {{ selectedArea.open_time }}–{{ selectedArea.close_time }}
        </span>
      </div>

      <div class="seat-layout">
        <article class="reader-panel seat-map-card">
          <div class="reader-panel-head">
            <div>
              <h2>{{ selectedArea?.name || '座位图' }}</h2>
              <p>{{ selectedArea?.description || '选择可用座位，确认后保留座位。' }}</p>
            </div>
            <span v-if="selectedArea" class="badge badge-neutral no-dot">
              {{ selectedArea.rows }} × {{ selectedArea.cols }}<template v-if="map"> · 已占 {{ occupiedCount }} / {{ map.seats.length }}</template>
            </span>
          </div>
          <div class="seat-legend">
            <span><i />可选</span>
            <span><i class="mine" />我的</span>
            <span><i class="booked" />已占</span>
            <span><i class="selected" />已选</span>
          </div>
          <div v-if="loadingMap" class="reader-loading"><span class="spinner" />正在读取座位…</div>
          <div v-else-if="mapError" class="reader-empty"><AppIcon name="alert" /><strong>座位读取失败</strong><p>{{ mapError }}</p><button class="btn btn-outline btn-sm" type="button" @click="loadMap">重试</button></div>
          <div v-else-if="map" class="seat-map-scroll">
            <div
              class="seat-grid"
              :class="{ 'is-locked': Boolean(map.my_booking) }"
              :style="{ gridTemplateColumns: `repeat(${map.area.cols}, 56px)` }"
            >
              <button
                v-for="seat in map.seats"
                :key="seat.id"
                class="seat-cell"
                :class="{
                  'is-mine': seat.mine,
                  'is-booked': seat.booked,
                  'is-selected': selectedSeat?.id === seat.id,
                  'is-disabled': !seat.enabled,
                }"
                type="button"
                :disabled="seat.booked || !seat.enabled || Boolean(map.my_booking)"
                :title="`${seat.code} · ${seat.kind}`"
                @click="chooseSeat(seat)"
              >{{ seat.code }}</button>
            </div>
          </div>
        </article>

        <aside class="seat-side">
          <article class="seat-booking-summary">
            <div class="drawer-section-title">预约信息</div>
            <div class="seat-summary-line"><span>区域</span><strong>{{ selectedArea?.name || '—' }}</strong></div>
            <div class="seat-summary-line"><span>日期</span><strong>{{ formatDate(selectedDate) }}</strong></div>
            <div class="seat-summary-line"><span>时段</span><strong>{{ selectedSlot ? `${selectedSlot}–${map?.slot.end || ''}` : '—' }}</strong></div>
            <div class="seat-summary-line"><span>座位</span><strong>{{ selectedSeat?.code || '尚未选择' }}</strong></div>
            <div v-if="map?.my_booking" class="reader-note-card" style="margin-top: 8px">
              <strong>{{ map.my_booking.area_name }} {{ map.my_booking.seat_code }}</strong>
              · 当天已有预约 {{ map.my_booking.start_time }}–{{ map.my_booking.end_time }}，取消后可改约
            </div>
            <button class="btn btn-primary btn-block" type="button" :disabled="!canBook" @click="submitBooking">
              <AppIcon name="seat" />{{ bookingBusy ? '提交中…' : '确认预约' }}
            </button>
            <p v-if="reader?.status !== 'ACTIVE'" class="field-note" style="margin-top: 8px">读者当前状态不能预约座位。</p>
          </article>

          <article class="reader-panel" style="padding-bottom: 12px">
            <div class="reader-panel-head">
              <div><h2>我的预约</h2><p>签到请在开始前 30 分钟内完成</p></div>
            </div>
            <div v-if="loadingBookings" class="reader-loading" style="padding: 22px"><span class="spinner" /></div>
            <div v-else-if="!bookings.length" class="reader-empty" style="padding: 26px 16px"><AppIcon name="seat" /><strong>当前没有座位预约</strong></div>
            <div v-else class="seat-my-list" style="padding: 0 16px 4px">
              <div v-for="row in bookings" :key="row.id" class="seat-my-card">
                <div class="seat-my-head">
                  <strong>{{ row.area_name }} · {{ row.seat_code }}</strong>
                  <span>{{ row.status_label }}</span>
                </div>
                <div class="seat-my-meta">{{ formatDate(row.booking_date) }} · {{ row.start_time }}–{{ row.end_time }}</div>
                <div v-if="isActive(row)" class="seat-my-actions">
                  <button v-if="canCheckIn(row)" class="btn btn-primary btn-sm" type="button" :disabled="busyId === row.id" @click="checkIn(row)">签到</button>
                  <button class="btn btn-ghost btn-sm" type="button" :disabled="busyId === row.id" @click="cancelBooking(row)">取消</button>
                </div>
              </div>
            </div>
          </article>
        </aside>
      </div>
    </template>
  </div>
</template>
