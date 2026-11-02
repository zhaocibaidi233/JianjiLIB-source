<script setup lang="ts">
import BaseModal from './BaseModal.vue'

withDefaults(defineProps<{
  open: boolean
  title: string
  message: string
  confirmText?: string
  cancelText?: string
  danger?: boolean
  busy?: boolean
}>(), {
  confirmText: '确认',
  cancelText: '取消',
  danger: false,
  busy: false,
})

const emit = defineEmits<{ confirm: []; cancel: [] }>()
</script>

<template>
  <BaseModal :open="open" :title="title" :busy="busy" size="sm" @close="emit('cancel')">
    <p class="confirm-message">{{ message }}</p>
    <slot />
    <template #footer>
      <button class="btn btn-ghost" type="button" :disabled="busy" @click="emit('cancel')">
        {{ cancelText }}
      </button>
      <button
        class="btn"
        :class="danger ? 'btn-danger' : 'btn-primary'"
        type="button"
        :disabled="busy"
        @click="emit('confirm')"
      >
        {{ busy ? '处理中…' : confirmText }}
      </button>
    </template>
  </BaseModal>
</template>

<style scoped>
.confirm-message {
  font-size: 13px;
  line-height: 1.75;
  color: var(--text);
  white-space: pre-line;
}
</style>
