<script setup lang="ts">
import AppIcon from './AppIcon.vue'

/**
 * 列表表格的三态行（加载中 / 出错 / 空），usePagedList 的配套组件。
 *
 * 过去每个列表页手抄一遍 `<tr class="table-state">` ×3，只有文案与 colspan
 * 不同；错误行"alert 图标 + 重试按钮"的口径也只有部分页面带全。收到这里
 * 之后：新列表页一行接入，老页面逐步换。
 *
 * 用法：组件外层挂 v-if（有状态行才渲染），数据行用 v-else 跟在后面——
 * `v-else` 必须紧跟带 v-if 的兄弟节点，所以条件要在页面写一遍：
 *
 *   <TableState v-if="list.loading || list.error || !list.items.length"
 *               :colspan="8" :list="list" empty="当前没有符合条件的读者" :retry="load" />
 *   <template v-else>
 *     <tr v-for="row in list.items" ...>
 *
 * `retry` 不传则错误行只有提示没有按钮（资源库嵌套列表等场景）。
 */
defineProps<{
  colspan: number
  list: { loading: boolean; error: string; items: unknown[] }
  empty: string
  loadingText?: string
  retry?: () => void
}>()
</script>

<template>
  <tr v-if="list.loading">
    <td :colspan="colspan" class="table-state">
      <span class="spinner" />{{ loadingText ?? '正在读取…' }}
    </td>
  </tr>
  <tr v-else-if="list.error">
    <td :colspan="colspan" class="table-state">
      <AppIcon name="alert" />{{ list.error }}
      <div v-if="retry" style="margin-top: 10px">
        <button class="btn btn-outline btn-sm" type="button" @click="retry">重新加载</button>
      </div>
    </td>
  </tr>
  <tr v-else-if="!list.items.length">
    <td :colspan="colspan" class="table-state">{{ empty }}</td>
  </tr>
</template>
