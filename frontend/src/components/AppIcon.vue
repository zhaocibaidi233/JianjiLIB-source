<script setup lang="ts">
withDefaults(defineProps<{
  name: string
  size?: number | string
  strokeWidth?: number | string
}>(), {
  size: undefined,
  strokeWidth: undefined,
})
</script>

<template>
  <svg
    class="icon"
    aria-hidden="true"
    :style="size ? { width: typeof size === 'number' ? `${size}px` : size, height: typeof size === 'number' ? `${size}px` : size } : undefined"
    :stroke-width="strokeWidth"
  >
    <use :href="`#i-${name}`" />
  </svg>
</template>
