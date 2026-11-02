<script lang="ts">
const modalPanels: HTMLElement[] = []
</script>

<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref, watch } from 'vue'
import AppIcon from './AppIcon.vue'

const props = withDefaults(defineProps<{
  open: boolean
  title: string
  subtitle?: string
  size?: 'sm' | 'md' | 'lg'
  closeOnBackdrop?: boolean
  busy?: boolean
}>(), {
  subtitle: '',
  size: 'md',
  closeOnBackdrop: true,
  busy: false,
})

const emit = defineEmits<{ close: [] }>()
const panel = ref<HTMLElement | null>(null)
let previousFocus: HTMLElement | null = null
let registeredPanel: HTMLElement | null = null

function close() {
  if (!props.busy) emit('close')
}

function focusableElements() {
  return Array.from(panel.value?.querySelectorAll<HTMLElement>(
    'button:not(:disabled), a[href], input:not([type="hidden"]):not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"]):not(:disabled)',
  ) || []).filter((element) => {
    const style = getComputedStyle(element)
    return !element.closest('[hidden], [inert]') && style.display !== 'none' && style.visibility !== 'hidden'
  })
}

function onKeydown(event: KeyboardEvent) {
  if (!props.open || modalPanels.at(-1) !== panel.value) return
  if (event.key === 'Escape') {
    event.preventDefault()
    event.stopImmediatePropagation()
    close()
    return
  }
  if (event.key !== 'Tab' || !panel.value) return
  const focusable = focusableElements()
  if (!focusable.length) {
    event.preventDefault()
    panel.value.focus()
    return
  }
  const first = focusable[0]!
  const last = focusable[focusable.length - 1]!
  const outside = !panel.value.contains(document.activeElement)
  if (event.shiftKey && (document.activeElement === first || document.activeElement === panel.value || outside)) {
    event.preventDefault()
    last.focus()
  } else if (!event.shiftKey && (document.activeElement === last || document.activeElement === panel.value || outside)) {
    event.preventDefault()
    first.focus()
  }
}

function releasePanel() {
  const wasTop = registeredPanel !== null && modalPanels.at(-1) === registeredPanel
  const index = registeredPanel ? modalPanels.indexOf(registeredPanel) : -1
  if (index >= 0) modalPanels.splice(index, 1)
  registeredPanel = null
  document.body.classList.toggle('no-scroll', modalPanels.length > 0)
  document.removeEventListener('keydown', onKeydown)
  if (wasTop && previousFocus?.isConnected) previousFocus.focus()
  previousFocus = null
}

watch(
  () => props.open,
  async (open) => {
    if (!open) {
      releasePanel()
      return
    }
    previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null
    await nextTick()
    if (!props.open || !panel.value) return
    registeredPanel = panel.value
    modalPanels.push(registeredPanel)
    document.body.classList.add('no-scroll')
    document.addEventListener('keydown', onKeydown)
    const target = focusableElements().find((element) => !element.classList.contains('modal-close'))
    ;(target || panel.value).focus()
  },
  { immediate: true, flush: 'post' },
)

onBeforeUnmount(releasePanel)
</script>

<template>
  <Teleport to="body">
    <div
      v-if="open"
      class="overlay"
      @mousedown.self="closeOnBackdrop && close()"
    >
      <section
        ref="panel"
        class="modal"
        :class="`modal-${size}`"
        role="dialog"
        aria-modal="true"
        :aria-label="title"
        :aria-busy="busy"
        tabindex="-1"
      >
        <header class="modal-head">
          <div>
            <h3>{{ title }}</h3>
            <p v-if="subtitle">{{ subtitle }}</p>
          </div>
          <button class="modal-close" type="button" aria-label="关闭" :disabled="busy" @click="close">
            <AppIcon name="x" :size="18" />
          </button>
        </header>
        <div class="modal-body">
          <slot />
        </div>
        <footer v-if="$slots.footer" class="modal-foot">
          <slot name="footer" />
        </footer>
      </section>
    </div>
  </Teleport>
</template>
