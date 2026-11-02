import pluginVue from 'eslint-plugin-vue'
import { defineConfigWithVueTs, vueTsConfigs } from '@vue/eslint-config-typescript'

export default defineConfigWithVueTs(
  // 构建产物不入库也不进 lint
  { ignores: ['dist/**', 'node_modules/**', 'coverage/**'] },
  pluginVue.configs['flat/essential'],
  vueTsConfigs.recommended,
  {
    rules: {
      // 视图文件是单词名（Labels / Stats / Seats…），中文产品语境不改名
      'vue/multi-word-component-names': 'off',
      // legacy 代码面大量使用 any（老 axios 封装的迁移期产物）。门禁先拦"真 bug"
      // （未定义变量、漏挂组件、v-html 注入面），不在类型风格上拦人——要收紧
      // 时再开，配专项提交。
      '@typescript-eslint/no-explicit-any': 'off',
      '@typescript-eslint/no-unused-vars': [
        'error',
        { argsIgnorePattern: '^_', varsIgnorePattern: '^_' },
      ],
    },
  },
  {
    // MarcPane 是「共享可变草稿」设计：fields 数组由 RecordEdit 持有，本组件
    // 就地增删改（删字段 splice、加字段 push、v-model 直接改字段值），父组件
    // 拿同一份引用负责版本协议与脏状态。改成 emit 事件流要连动 RecordEdit 的
    // 整条保存链——留给拆分 RecordEdit 的专项，这里明确豁免。
    files: ['src/legacy/components/MarcPane.vue'],
    rules: { 'vue/no-mutating-props': 'off' },
  },
)
