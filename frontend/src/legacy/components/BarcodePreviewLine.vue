<template>
  <!-- 条码框下面那行：有实时预览就显示预览（蓝字高亮），否则显示"最后条码 / 续号用法 / 允许位数"。
       有冲突的条码另起一行红字；本人号段落后于全库时再起一行黄字提醒。
       三行都**不占额外高度**（无预览时也是一行灰字）。 -->
  <div class="barcode-hint-line" :class="{ 'is-active': previewText }" style="width: 100%">
    {{ previewText || `最后条码 ${lastBarcode || '—'}；+N = 续号 N 册；位数 ${digits.join(' / ')}` }}
  </div>
  <div v-if="conflicts.length" class="conflict-line">
    已占用：{{ conflicts.join('、') }}
  </div>
  <div v-if="libraryLast" class="scope-line">
    本库已用到 {{ libraryLast }}（续号按本人最后一条往后接，注意避免重号）
  </div>
</template>

<script setup lang="ts">
/**
 * 条码预览行 + 冲突行 + 号段提醒行（拆自 RecordEdit.vue， 阶段 4a）。
 *
 * **零状态、零请求**：预览文本、冲突列表与提醒文本都是页面算好的
 * （`composables/useBarcodePreview.ts` 里 250ms 去抖 + "最新值获胜"守卫的产物），
 * 这里只负责排版。空态文案里的 `—` 占位与"位数用 ` / ` 分隔"是**逐字**搬过来的
 * （那套"不留常驻说明行、但也不能空着"的口径）。
 */
defineProps<{
  /** 实时预览文本（空串 = 走空态文案）。 */
  previewText: string
  /** 续号基准号：**当前编目员**最后一条（他还没录过则是全库最大值）。 */
  lastBarcode: string
  /** 允许的条码位数（可多个）。 */
  digits: number[]
  /** 已被占用的条码（空数组 = 不显示冲突行）。 */
  conflicts: string[]
  /**
   * **全库**最后条码；仅在"高于上面那个基准号"时由页面传入（空串 = 不显示）。
   *
   * 为什么要它：续号基准改成"本人最后一条"之后，界面只显示本人的号会**误导** ——
   * 用户以为库里才用到 00000003，其实导入进来的记录已经用到 0025265
   * （导入的记录没有编目员归属，见 `api/holdings.py::last_barcode` 的说明）。
   */
  libraryLast?: string
}>()
</script>

<style scoped>
.barcode-hint-line {
  font-family: Consolas, Menlo, monospace;
  font-size: 12px; color: var(--el-text-color-secondary); line-height: 1.6;
  word-break: break-all;
}
.barcode-hint-line.is-active { color: var(--el-color-primary); font-weight: 500; }   /* 有实时预览时高亮 */
.conflict-line { font-size: 12px; color: var(--el-color-danger); }
.scope-line { font-size: 12px; color: var(--el-color-warning); }
</style>
