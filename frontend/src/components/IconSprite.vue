<script setup lang="ts">
import { ICON_BODIES } from './icons'
</script>

<template>
  <svg xmlns="http://www.w3.org/2000/svg" style="display: none" aria-hidden="true">
    <symbol
      v-for="(body, name) in ICON_BODIES"
      :id="`i-${name}`"
      :key="name"
      viewBox="0 0 24 24"
      v-html="body"
    />
  </svg>
</template>
