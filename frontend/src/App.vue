<script setup lang="ts">
import { RouterView } from 'vue-router'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import IconSprite from './components/IconSprite.vue'
import ToastHost from './components/ToastHost.vue'
</script>

<template>
  <IconSprite />
  <el-config-provider :locale="zhCn">
    <RouterView />
  </el-config-provider>
  <ToastHost />
</template>
