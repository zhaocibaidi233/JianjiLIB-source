import { createApp } from 'vue'
import App from './App.vue'
import { router } from './router'
import { initTheme } from './lib/theme'
// 程序式调用的 ElMessage / ElMessageBox 扫不到模板，样式必须显式引入
import 'element-plus/es/components/message/style/css'
import 'element-plus/es/components/message-box/style/css'
import 'element-plus/theme-chalk/dark/css-vars.css'
import './styles/base.css'
import './styles/admin.css'
import './styles/app.css'
import './styles/business.css'
import './styles/stats-system.css'
import './styles/reader.css'
import './styles/reader-portal-v4.css'
import './styles/legacy.css'
import './styles/legacy-new.css'

initTheme()

createApp(App).use(router).mount('#app')
