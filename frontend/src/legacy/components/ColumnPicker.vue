<template>
  <el-popover placement="bottom-end" :width="252" trigger="click" popper-class="col-picker-pop">
    <template #reference>
      <el-button class="col-picker-btn" :icon="Operation" :title="tip">
        列<span v-if="customized" class="col-picker-count">{{ visibleCount }}/{{ columns.length }}</span>
      </el-button>
    </template>

    <div class="col-picker">
      <div class="col-picker-head">
        <span>显示哪些列</span>
        <el-button link size="small" :disabled="!customized" @click="emit('reset')">
          恢复默认
        </el-button>
      </div>
      <div class="col-picker-list">
        <el-checkbox
          v-for="c in columns"
          :key="c.key"
          :model-value="visible(c.key)"
          :disabled="visible(c.key) && visibleCount <= 1"
          @change="(v: any) => emit('toggle', c.key, Boolean(v))"
        >{{ c.label }}</el-checkbox>
      </div>
      <div class="col-picker-foot">
        <el-button link size="small" :disabled="visibleCount === columns.length" @click="emit('show-all')">
          全部显示
        </el-button>
        <span class="col-picker-note">设置记在这台电脑的浏览器里</span>
      </div>
    </div>
  </el-popover>
</template>

<script setup lang="ts">
/**
 * 表格列选择器：勾掉不想看的列。
 *
 * 为什么是「勾选要显示」而不是「勾选要隐藏」：用户看的是结果，勾上=看得到，
 * 心智负担更低。状态存在本地（见 composables/useColumnPrefs.ts）。
 *
 * 默认隐藏的列（页数、尺寸这类"偶尔才要瞄一眼"的）在列表里与其它列一视同仁，
 * 只是默认不勾——不额外加标记，勾上就能看到。
 *
 * 最后一个可见列不允许取消——只剩表头的表格看起来像页面坏了。
 */
import { computed } from 'vue'
import { Operation } from '@element-plus/icons-vue'
import type { ColumnDef } from '../composables/useColumnPrefs'

const props = defineProps<{
  columns: ColumnDef[]
  hidden: string[]
  shown: string[]
  visibleCount: number
}>()

const emit = defineEmits<{
  (e: 'toggle', key: string, on: boolean): void
  (e: 'show-all'): void
  (e: 'reset'): void
}>()

/** 与 composable 同一套判定：默认隐藏的看 shown，其余看 hidden */
function visible(key: string): boolean {
  const def = props.columns.find((c) => c.key === key)
  if (!def) return false
  return def.defaultHidden ? props.shown.includes(key) : !props.hidden.includes(key)
}

/** 有任何偏离默认的改动，才算"自定义过"，才显示计数与「恢复默认」 */
const customized = computed(() => props.hidden.length > 0 || props.shown.length > 0)

const tip = computed(() => {
  const off = props.columns.filter((c) => !visible(c.key)).map((c) => c.label)
  if (!off.length) return '选择要显示哪些列'
  return `已隐藏 ${off.length} 列：${off.join('、')}`
})
</script>

<style scoped>
.col-picker-count {
  margin-left: 4px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.col-picker-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-bottom: 6px;
  margin-bottom: 6px;
  border-bottom: 1px solid var(--el-border-color-lighter);
  font-size: 13px;
  color: var(--el-text-color-regular);
}
.col-picker-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
  max-height: 340px;
  overflow-y: auto;
}
.col-picker-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-top: 8px;
  padding-top: 6px;
  border-top: 1px solid var(--el-border-color-lighter);
}
.col-picker-note {
  font-size: 11px;
  color: var(--el-text-color-secondary);
}
</style>
