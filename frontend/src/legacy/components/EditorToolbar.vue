<template>
  <div class="editor-head">
    <el-button @click="emit('back')">← 返回</el-button>
    <b style="font-size: 15px">{{ isNew ? '新建记录' : isTemp ? '套录编辑' : `记录 #${id}` }}</b>
    <el-tag v-if="isTemp" size="small" type="warning">未入库</el-tag>
    <el-tag v-if="detail" size="small">{{ sourceLabel(detail.source) }}</el-tag>
    <div style="flex: 1"></div>
    <!-- 未在简编表单显示的字段：压缩成一个小标签挂在工具条上。
         原先是一条整幅 el-alert（~50px），白占表单高度；移到这里等于**零成本**，
         且与"保存"同处一条视线内。系统字段（001/905）与只读区块已覆盖的 606/7xx 不计入。 -->
    <el-tooltip v-if="hiddenFields.count" placement="bottom"
      :content="`涉及 ${hiddenFields.tags.join('、')}。保存时原样保留；如需修改请切换到「全字段 MARC」。`">
      <el-tag type="info" size="small" effect="plain">
        另有 {{ hiddenFields.count }} 个字段未显示
      </el-tag>
    </el-tooltip>
    <!-- 质检摘要：并入工具条，超长省略；完整内容在悬浮提示中查看 -->
    <el-tooltip v-if="findings.length" placement="bottom-start" :content="qualitySummary">
      <span class="quality-inline" :class="hasError ? 'is-error' : 'is-warning'">
        <el-icon aria-hidden="true"><WarningFilled /></el-icon>
        <span class="quality-inline-text">{{ qualitySummary }}</span>
      </span>
    </el-tooltip>
    <!-- 保存入库放在**常驻工具条**：两个 tab 共用，且不必滚到底部才能保存 -->
    <el-button type="primary" :loading="saving" @click="emit('save')">保存入库</el-button>
  </div>
</template>

<script setup lang="ts">
/**
 * 记录编辑页的顶部工具条（拆自 `RecordEdit.vue`， §3 块 A）。
 *
 * 质检摘要并入工具条：超长时省略，悬浮提示给出完整内容，不再单独占用一行
 * （原先的 `el-tag` + 下方整幅 `el-alert` 表达同一件事，已合并）。
 *
 * ## 状态归属：9 个 props 全是只读值，两个动作 emit 回页面
 *
 * 这里**不持有任何状态** —— 标题三态（`isNew` / `isTemp` / `id`）、来源标签（`detail`）、
 * 未显示字段计数（`hiddenFields`，由 `fields` 推导）、质检三件套（`findings` /
 * `hasError` / `qualitySummary`）、保存标志（`saving`）都是页面上的现成值，原样传入。
 *
 * 两个动作留在页面（与 `MarcPane` 的 `save` 同一原则）：
 * - `back` —— 返回走路由（由父页面绑定 `router.push('/admin/records')`），组件不引 `useRouter`，可独立测；
 * - `save` —— 保存要过**质检拦截、条码校验、"仍要强制入库"对话框**那一整套流程，
 *   副作用是页面的，组件只负责"点了"。
 *
 * ## 已知的调用约束（改挂载方式时要看）
 *
 * `tests/recordEditRoute.test.ts` 用 `shallowMount` 并断言标题文本
 * （「套录编辑」「记录 #42」）。标题是原生 `<b>`，只要本组件**不被 stub** 就能断言到 ——
 * 该文件的 `mountEditor()` 因此在 `stubs` 里显式放行了本组件。
 */
import { WarningFilled } from '@element-plus/icons-vue'
import { sourceLabel } from '../../lib/displayLabels'
import type { Finding } from '../api'

defineProps<{
  /** 新建记录（路由 `new`）：标题显示「新建记录」。 */
  isNew: boolean
  /** 套录/资源库临时副本（路由 `temp`）：标题显示「套录编辑」并挂「未入库」标签。 */
  isTemp: boolean
  /** 已入库记录的数字 id（字符串形态）——两者都不是时的标题 `记录 #id`。 */
  id: string
  /** 已入库记录详情（只用到 `source`；新建/套录副本时为 null，则不挂来源标签）。 */
  detail: { source?: string } | null
  /** 简编表单看不见但记录里存在的字段（`{ tags, count }`，由页面的 `fields` 推导）。 */
  hiddenFields: { tags: string[]; count: number }
  /** 质检结论。本组件只读它的长度（标签与提示条都按"有没有问题"出现）。 */
  findings: Finding[]
  /** 是否存在 ERROR 级问题 —— 决定标签/提示条的配色（danger 拦入库 / warning 仅建议）。 */
  hasError: boolean
  /** 提示条那句话（规则名列举），由页面算好 —— 组件不做文案拼接。 */
  qualitySummary: string
  /** 保存请求在途（与「全字段 MARC」tab 的「保存入库」共用页面的同一个标志）。 */
  saving: boolean
}>()

const emit = defineEmits<{
  /** 点「← 返回」。 */
  back: []
  /** 点「保存入库」（页面实现 `saveAndHoldings()`）。 */
  save: []
}>()
</script>

<style scoped>
/* 页面顶部工具条：返回 / 标题 / 状态标签 / 未显示字段 / 质检 / 保存入库，单行。
   **吸顶**（2026-10-25 易用性整改）：简编表单要滚到中下部（条码格、提要）才能
   录完，工具条若跟着滚走，「保存入库」就看不见了——录完一本书还得滚回顶上按
   保存。`top` 扣掉馆员工作台自己的 `.topbar`（变量见 styles/admin.css）。 */
.editor-head {
  display: flex; align-items: center; gap: 10px; margin-bottom: 8px;
  flex-wrap: wrap;   /* 窄窗口下允许换行，不把「保存入库」挤出可视区 */
  position: sticky;
  top: var(--topbar-h, 62px);
  z-index: 30;
  padding: 6px 0;
  margin-top: -6px;
  background: var(--el-bg-color, #fff);
}
/* 质检摘要：并入工具条，超长省略，不单独占一行 */
.quality-inline {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  max-width: min(38vw, 380px);
  min-width: 0;
  padding: 3px 9px;
  border: 1px solid var(--accent-soft);
  border-radius: 7px;
  background: var(--accent-soft);
  color: var(--accent-deep);
  font-size: 12px;
  line-height: 1.5;
}
.quality-inline .el-icon { flex: none; font-size: 14px; }
.quality-inline-text {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.quality-inline.is-error {
  border-color: var(--danger-soft);
  background: var(--danger-soft);
  color: var(--danger-deep);
}

/* 矮屏自适应：与页面 `@media (max-height: 890px)` 里 `.editor-card` 的收紧节奏一致
   （那条媒体查询在 RecordEdit.vue，这里只跟走 `.editor-head` 自己的那一行）。 */
@media (max-height: 890px) {
  .editor-head { margin-bottom: 4px; }
}
</style>
