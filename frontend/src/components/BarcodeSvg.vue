<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import JsBarcode from 'jsbarcode'

const props = withDefaults(defineProps<{
  value: string
  format?: string
  height?: number
}>(), {
  format: 'CODE39',
  height: 32,
})

const svg = ref<SVGSVGElement | null>(null)
const invalid = ref(false)

function render() {
  const text = props.value.trim()
  if (!svg.value) return
  invalid.value = false
  try {
    JsBarcode(svg.value, text, {
      format: props.format,
      displayValue: false,
      height: props.height,
      width: 1.4,
      margin: 0,
      lineColor: '#182420',
      background: 'transparent',
    })
  } catch {
    invalid.value = true
    svg.value.innerHTML = ''
  }
}

onMounted(render)
watch(() => [props.value, props.format, props.height], render)
</script>

<template>
  <span class="barcode-render">
    <svg v-show="!invalid" ref="svg" class="label-barcode-svg" />
    <span v-if="invalid" class="barcode-invalid">{{ value || '无条码' }}</span>
  </span>
</template>

<style scoped>
.barcode-render { display: block; width: 100%; line-height: 0; }
.label-barcode-svg { width: 100%; height: auto; max-height: 14mm; }
.barcode-invalid {
  display: block;
  padding: 1mm;
  border: 1px dashed var(--danger);
  font-size: 7pt;
  line-height: 1.2;
  color: var(--danger);
}
</style>
