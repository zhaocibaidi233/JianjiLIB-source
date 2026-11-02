<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(defineProps<{
  title: string
  author?: string | null
  size?: 'mini' | 'card' | 'spine'
  theme?: string
}>(), {
  author: '',
  size: 'mini',
  theme: '',
})

const THEMES = ['teal', 'indigo', 'clay', 'olive', 'plum', 'navy', 'ochre', 'forest', 'wine', 'space']

function hash(text: string): number {
  let value = 0
  for (let i = 0; i < text.length; i += 1) value = (value * 31 + text.charCodeAt(i)) >>> 0
  return value
}

const theme = computed(() => props.theme || THEMES[hash(props.title || '未知题名') % THEMES.length])
const glyph = computed(() => (props.title || '未知题名').slice(0, 1))
const shortTitle = computed(() => (props.title || '未命名').slice(0, props.size === 'mini' ? 4 : 8))
</script>

<template>
  <span
    class="book-cover"
    :class="size === 'mini' ? 'book-mini' : size === 'card' ? 'book-card' : 'book-spine'"
    :data-theme="theme"
    :data-glyph="glyph"
    aria-hidden="true"
  >
    <span class="cover-text">{{ shortTitle }}</span>
    <span v-if="size === 'card' && author" class="cover-author">{{ author }}</span>
  </span>
</template>

<style scoped>
.book-card {
  width: 100%;
  aspect-ratio: 3 / 4.35;
  display: flex;
  flex-direction: column;
  justify-content: flex-end;
  gap: 4px;
  padding: 10px;
  border-radius: 10px;
  font-size: 12px;
}
.book-spine {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 10px 3px;
  border-radius: 3px 4px 4px 3px;
  font-size: 11px;
  box-shadow: inset -2px 0 4px rgba(0, 0, 0, 0.28),
    inset 2px 0 3px rgba(255, 255, 255, 0.16),
    0 10px 18px -10px rgba(0, 0, 0, 0.6);
}
.book-spine::after { display: none; }
.cover-text {
  position: relative;
  z-index: 1;
  writing-mode: vertical-rl;
  white-space: nowrap;
  overflow: hidden;
  align-self: flex-end;
  font-family: var(--font-serif);
  font-size: 1.05em;
  line-height: 1.3;
  letter-spacing: 0.06em;
}
.book-spine .cover-text {
  align-self: center;
  max-height: 100%;
  overflow: hidden;
  white-space: nowrap;
}
.cover-author {
  position: relative;
  z-index: 1;
  writing-mode: vertical-rl;
  white-space: nowrap;
  overflow: hidden;
  max-height: 42%;
  align-self: flex-end;
  font-size: 0.68em;
  color: rgba(255, 255, 255, 0.66);
  letter-spacing: 0.04em;
}
.book-mini .cover-text {
  position: relative;
  z-index: 1;
  writing-mode: vertical-rl;
  opacity: 0.92;
}
</style>
