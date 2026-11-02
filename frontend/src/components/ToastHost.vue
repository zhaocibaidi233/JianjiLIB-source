<script setup lang="ts">
import AppIcon from './AppIcon.vue'
import { dismissToast, toasts } from '../lib/toast'

const iconOf = (kind: string) =>
  kind === 'error' ? 'alert' : kind === 'info' ? 'info' : 'check-circle'
</script>

<template>
  <div class="toast-host" aria-live="polite">
    <TransitionGroup name="toast">
      <button
        v-for="item in toasts"
        :key="item.id"
        type="button"
        class="toast toast-item"
        :class="item.kind"
        @click="dismissToast(item.id)"
      >
        <AppIcon :name="iconOf(item.kind)" :size="18" />
        <span>{{ item.message }}</span>
      </button>
    </TransitionGroup>
  </div>
</template>

<style scoped>
.toast-host {
  position: fixed;
  right: 22px;
  bottom: 22px;
  z-index: 1200;
  display: flex;
  flex-direction: column;
  gap: 10px;
  align-items: flex-end;
  pointer-events: none;
}
.toast-item {
  pointer-events: auto;
  display: inline-flex;
  align-items: center;
  gap: 9px;
  max-width: min(420px, calc(100vw - 44px));
  padding: 11px 16px;
  border-radius: 12px;
  background: var(--ink);
  color: var(--surface);
  box-shadow: var(--shadow-lg);
  font-size: 13px;
  text-align: left;
}
.toast-item.error { background: var(--danger-deep); }
.toast-item.info { background: var(--info); }
.toast-enter-active, .toast-leave-active { transition: all .22s var(--ease); }
.toast-enter-from, .toast-leave-to { opacity: 0; transform: translateY(8px) scale(.98); }
@media (max-width: 640px) {
  .toast-host { right: 14px; bottom: 76px; left: 14px; align-items: stretch; }
  .toast-item { max-width: none; }
}
</style>
