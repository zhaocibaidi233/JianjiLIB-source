<template>
  <div class="page-header">
    <div class="page-header__text">
      <h2 class="page-header__title">
        <span v-if="icon" class="page-header__icon"><el-icon><component :is="icon" /></el-icon></span>
        <span>{{ title }}</span>
      </h2>
      <p v-if="desc" class="page-header__desc">{{ desc }}</p>
    </div>
    <div v-if="$slots.actions" class="page-header__actions">
      <slot name="actions" />
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * 各页统一的标题区。
 *
 * 为什么要有它：改造前 10 个页面里有 6 个**完全没有标题**（进去只有筛选器/表格），
 * 用户只能靠侧栏高亮判断自己在哪；另外 2 个自己写了内联样式的标题，字号粗细还不一样。
 * 统一成一个组件后：标题措辞、副标题说明、右侧操作区的位置都一致。
 *
 * `icon` 传 @element-plus/icons-vue 的图标组件（与侧栏同一套映射），标题前渲染一枚
 * 淡色徽标；不传就是纯文字标题（向后兼容）。用矢量图标而非 emoji：emoji 跨平台
 * 渲染不一致、也不专业。
 *
 * 用法：
 *   <PageHeader :icon="FirstAidKit" title="数据体检" desc="全库扫描五类问题，每条可跳转编目页修改">
 *     <template #actions><el-button>重新体检</el-button></template>
 *   </PageHeader>
 */
import type { Component } from 'vue'
defineProps<{ title: string; desc?: string; icon?: Component }>()
</script>

<style scoped>
.page-header {
  display: flex;
  align-items: flex-start;
  gap: 14px;
  margin-bottom: 14px;
}
.page-header__text {
  flex: 1;
  min-width: 0;
}
.page-header__title {
  margin: 0;
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 20px;
  font-weight: 700;
  line-height: 1.35;
  color: var(--app-text);
  letter-spacing: .01em;
}
.page-header__icon {
  width: 32px; height: 32px;
  border-radius: 10px;
  display: grid; place-items: center;
  flex: none;
  font-size: 17px;
  color: var(--app-primary);
  background: var(--app-primary-soft);
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--app-primary) 8%, transparent);
}
.page-header__desc {
  margin: 5px 0 0;
  color: var(--app-text-muted);
  font-size: 12.5px;
  line-height: 1.6;
}
.page-header__actions {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  justify-content: flex-end;
}
@media (max-width: 720px) {
  .page-header { flex-direction: column; gap: 10px; }
  .page-header__actions { width: 100%; justify-content: flex-start; }
}
</style>
