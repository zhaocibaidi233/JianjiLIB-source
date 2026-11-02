import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import Components from 'unplugin-vue-components/vite'
import { ElementPlusResolver } from 'unplugin-vue-components/resolvers'

const apiTarget = process.env.VITE_API_TARGET || 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [
    vue(),
    // 旧版编目 / 条码 / 书标页使用 Element Plus：按模板里真实出现的组件与指令
    // 自动引入，避免把整库打进首屏。程序式 ElMessage/ElMessageBox 的样式在 main.ts 显式引入。
    Components({
      dts: false,
      dirs: [],
      resolvers: [ElementPlusResolver()],
    }),
  ],
  server: {
    port: 5173,
    // dev server 只服务于本机开发。`fs.strict` 打开后，越出项目根的路径
    // （`/@fs/..`、符号链接指向的外部目录）一律拒绝——历史上 Vite 的路径
    // 穿越类问题都出在这个开关被关掉的时候。真正的生产形态不靠它，靠
    // 后端托管 `dist`（见 backend/app/main.py::_mount_frontend）。
    fs: { strict: true },
    proxy: {
      '/api': {
        target: apiTarget,
        changeOrigin: true,
      },
    },
  },
  // `vite preview`（构建产物预览）默认继承 `server.proxy`，但那是隐式行为：
  // 以后有人调整 server 配置、或改用 preview 的独立配置，代理会静默丢失，
  // 表现为「页面能打开但所有接口 404」。这里显式写一份，把契约钉住。
  preview: {
    port: 5173,
    proxy: {
      '/api': {
        target: apiTarget,
        changeOrigin: true,
      },
    },
  },
})
