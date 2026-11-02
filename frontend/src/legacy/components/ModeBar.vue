<template>
  <!-- 作业方式：三个开关 + 「规范一次」。
       ⚠️ 三者**存储口径不同，别合并**（各自实现在 RecordEdit.vue，这里只管显示与点击）：
         套录模式 → localStorage（个人作业习惯，不跨机、不发请求）
         排架号   → 按图书库（settings `lib{id}|shelf_no`，与图书库列表页同一个存储）
         自动规范 → 全局（settings `optimize_mode`）
       常驻可见（用户要求"一眼看全状态"），窄屏 flex-wrap 自动换行。
       用胶囊而不是 el-switch：卡片内容宽只有 ~226px(1440) / ~210px(1366)，
       三个带文字的 switch 一定溢出；胶囊是开关**状态**，末尾那个是**动作**（虚线边框区分）。 -->
  <div class="mode-bar">
    <el-tooltip placement="bottom" content="开：保存后回到套录框，便于连续编目。关：保存后留在条码框，便于连续贴条码。">
      <button type="button" class="mode-chip" :class="{ 'is-on': catalogMode === 'catalog' }"
        :aria-pressed="catalogMode === 'catalog'" @click="emit('toggleCatalogMode')">套录模式</button>
    </el-tooltip>
    <el-tooltip placement="bottom" content="按图书库设置：是否在编目界面显示「排架号」。关闭后仅不显示，已有数据保留。">
      <button type="button" class="mode-chip" :class="{ 'is-on': shelfNoEnabled }"
        :aria-pressed="shelfNoEnabled" @click="emit('toggleShelfNo')">排架号</button>
    </el-tooltip>
    <el-tooltip placement="bottom" content="开启后，打开记录或载入套录底稿时自动规范（标点、语种、出版年、出版地、题名拆分）。只补缺失、不覆盖已有值，保存时落库。">
      <button type="button" class="mode-chip" :class="{ 'is-on': optimizeAuto }"
        :aria-pressed="optimizeAuto" @click="emit('toggleOptimize')">自动规范</button>
    </el-tooltip>
    <el-tooltip placement="bottom" content="立即规范一次，不改变上面的开关状态。">
      <button type="button" class="mode-chip mode-chip--action" :disabled="optimizing"
        @click="emit('optimize')">{{ optimizing ? '规范中…' : '⟳ 规范一次' }}</button>
    </el-tooltip>
  </div>
</template>

<script setup lang="ts">
/**
 * 录入栏「作业方式」胶囊栏（拆自 RecordEdit.vue， 阶段 4a）。
 *
 * **零状态**：四个值全部由页面传入、四个动作全部 emit 回页面 ——
 * 三个开关的**存储口径各不相同**（localStorage / 按图书库 settings / 全局 settings），
 * 把它们搬进组件就等于把三套持久化也搬进来，那是"把复杂度搬家"。
 * 这里只负责"长什么样、点了之后通知谁"。
 *
 * 四个 emoji 式的状态都用 `aria-pressed` 标了出来：胶囊不是 `<button>` 的默认语义，
 * 读屏软件只能靠它判断"这个开关是开是关"。
 */
defineProps<{
  /** 套录模式：决定条码回车保存后光标去哪（'catalog' = 回 ISBN 框）。 */
  catalogMode: 'catalog' | 'barcode'
  /** 排架号开关（按图书库）。 */
  shelfNoEnabled: boolean
  /** 自动规范开关（全局）。 */
  optimizeAuto: boolean
  /** 「规范一次」进行中（按钮转 `规范中…` 并禁用）。 */
  optimizing: boolean
}>()

const emit = defineEmits<{
  toggleCatalogMode: []
  toggleShelfNo: []
  toggleOptimize: []
  optimize: []
}>()
</script>

<style scoped>
/* 卡片内容宽只有 ~226px(1440) / ~210px(1366)，三个带文字的 el-switch 必溢出，
   故改用紧凑胶囊；窄屏 flex-wrap 自动换行（换行只影响本栏高度，不影响表单）。
   开=实心主色、关=描边 —— 状态一眼可辨，不依赖开关的滑块位置。 */
.mode-bar { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 10px; }
.mode-chip {
  font: inherit; font-size: 12px; line-height: 1.5;
  padding: 2px 10px; border-radius: 11px; cursor: pointer;
  border: 1px solid var(--line);
  background: var(--surface-soft); color: var(--muted);
  transition: background-color .15s, border-color .15s, color .15s;
}
.mode-chip:hover {
  border-color: var(--primary-soft);
  background: var(--primary-softer);
  color: var(--primary-deep);
}
.mode-chip.is-on {
  background: var(--primary);
  border-color: var(--primary);
  color: #fff;
  font-weight: 600;
  box-shadow: 0 3px 9px -6px rgba(23, 94, 84, 0.7);
}
.mode-chip.is-on:hover { color: #fff; opacity: .9; }
/* 「规范一次」是**动作**不是开关：虚线边框 + 不参与 is-on 态，避免被误读成第四个开关 */
.mode-chip--action {
  border-style: dashed;
  border-color: var(--primary-soft);
  background: var(--primary-softer);
  color: var(--primary-deep);
}
.mode-chip--action:disabled { cursor: not-allowed; opacity: .6; }
.mode-chip--action:disabled:hover {
  border-color: var(--primary-soft);
  background: var(--primary-softer);
  color: var(--primary-deep);
}
</style>
