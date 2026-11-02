<script setup lang="ts">
import AppIcon from './AppIcon.vue'

/**
 * 可排序表头（图书库 el-table `sortable="custom"` 的设计系统等价物）。
 *
 * 交互与图书库一致：asc → desc → 默认顺序 三态循环，点击交给页面的
 * `setSort`（usePagedList 的，回第 1 页重查；后端白名单见各接口）。
 * 闲置时淡色下箭头；激活升序翻转成上箭头、降序常色，并给 th 挂
 * aria-sort（排序方向对读屏可感知）。
 *
 *   <ThSort label="姓名" sort-key="name" :current="list.sortKey"
 *           :order="list.sortOrder" @sort="setSort" />
 */
defineProps<{
  label: string
  /** 本列的排序键（= 后端 sort 白名单的键） */
  sortKey: string
  /** 当前生效的排序键（list.sortKey） */
  current: string
  /** 当前方向（list.sortOrder） */
  order: '' | 'asc' | 'desc'
}>()

const emit = defineEmits<{ sort: [key: string] }>()
</script>

<template>
  <th :aria-sort="current === sortKey ? (order === 'asc' ? 'ascending' : 'descending') : 'none'">
    <button type="button" class="th-sort"
            :class="{ 'is-active': current === sortKey, 'is-asc': current === sortKey && order === 'asc' }"
            @click="emit('sort', sortKey)">
      {{ label }}
      <AppIcon name="chevron-down" :size="13" class="th-sort-caret" />
    </button>
  </th>
</template>
