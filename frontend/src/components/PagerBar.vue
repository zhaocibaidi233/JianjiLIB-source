<script setup lang="ts">
import { computed, ref } from 'vue'
import AppIcon from './AppIcon.vue'
import { scrollElementToTop } from '../lib/scroll'

const props = defineProps<{
  page: number
  pageSize: number
  total: number
  noun?: string
}>()

const emit = defineEmits<{ 'update:page': [page: number] }>()

const rootEl = ref<HTMLElement | null>(null)

/** 计数单位。前后两处共用同一个 noun，避免「第 1–20 条，共 80 种图书」这类量词混用。 */
const unit = computed(() => props.noun || '条')
const pages = computed(() => Math.max(1, Math.ceil(props.total / props.pageSize)))
const from = computed(() => (props.total === 0 ? 0 : (props.page - 1) * props.pageSize + 1))
const to = computed(() => Math.min(props.total, props.page * props.pageSize))

const visible = computed<Array<number | '…'>>(() => {
  const total = pages.value
  if (total <= 7) return Array.from({ length: total }, (_, i) => i + 1)
  const current = props.page
  const rows: Array<number | '…'> = [1]
  if (current > 4) rows.push('…')
  const start = Math.max(2, current - 1)
  const end = Math.min(total - 1, current + 1)
  for (let i = start; i <= end; i += 1) rows.push(i)
  if (current < total - 3) rows.push('…')
  rows.push(total)
  return rows
})

function go(page: number) {
  if (page < 1 || page > pages.value || page === props.page) return
  emit('update:page', page)
  // 翻页后把**分页栏自己**滚到视口顶（右侧紧接新内容）。
  // 分页栏在列表上方，滚到它等于"从新一页的开头看起"；用户若已滚到列表深处，
  // 点不了它，所以这里只在能点到的场景生效，不做额外跳转。
  scrollElementToTop(rootEl.value)
}
</script>

<template>
  <div ref="rootEl" class="table-foot">
    <span v-if="total">第 {{ from }}–{{ to }} {{ unit }}，共 {{ total }} {{ unit }}</span>
    <span v-else>共 0 {{ unit }}</span>
    <nav v-if="pages > 1" class="pager" aria-label="分页">
      <button type="button" aria-label="上一页" :disabled="page <= 1" @click="go(page - 1)">
        <AppIcon name="chevron-left" :size="15" />
      </button>
      <template v-for="(item, index) in visible" :key="`${item}-${index}`">
        <span v-if="item === '…'" class="pager-gap">…</span>
        <button
          v-else
          type="button"
          :class="{ 'is-active': item === page }"
          :aria-current="item === page ? 'page' : undefined"
          @click="go(item)"
        >{{ item }}</button>
      </template>
      <button type="button" aria-label="下一页" :disabled="page >= pages" @click="go(page + 1)">
        <AppIcon name="chevron-right" :size="15" />
      </button>
    </nav>
  </div>
</template>

<style scoped>
.pager-gap {
  min-width: 20px;
  text-align: center;
  color: var(--faint);
}
</style>
