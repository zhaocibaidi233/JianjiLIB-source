/// <reference types="vite/client" />

export {}

declare module 'vue-router' {
  interface RouteMeta {
    title?: string
    subtitle?: string
    module?: string
    features?: string[]
    stage?: string
  }
}
