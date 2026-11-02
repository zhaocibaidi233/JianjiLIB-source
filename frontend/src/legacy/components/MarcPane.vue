<template>
  <div v-for="(f, fi) in fields" :key="fi" style="border: 1px solid var(--el-border-color-lighter); border-radius: 6px; padding: 8px; margin-bottom: 8px; background: var(--el-bg-color)">
    <div style="display: flex; gap: 6px; align-items: center; margin-bottom: 6px">
      <el-tag size="small" type="info">{{ f.tag }}</el-tag>
      <!-- 字段中文名（来自 CNMARC 字段字典，纯展示；取不到就不显示） -->
      <span v-if="fieldName(f.tag)" class="marc-field-name">{{ fieldName(f.tag) }}</span>
      <template v-if="!isControlField(f)">
        <el-input v-model="f.i1" style="width: 46px" size="small" placeholder="ind1"
          :disabled="isManaged(f)" />
        <el-input v-model="f.i2" style="width: 46px" size="small" placeholder="ind2"
          :disabled="isManaged(f)" />
      </template>
      <div style="flex: 1"></div>
      <!-- 905 由馆藏表重建（services.batch._rebuild_905）：在这里改它保存后会被
           覆盖回原样，所以整段设为只读并说明去处（2026-10-25 905 链路审查）。
           后端契约不变——仍以馆藏为真源，伪造/删除 905 都不会生效。 -->
      <el-tag v-if="isManaged(f)" size="small" type="warning"
        title="905 由馆藏记录自动生成，每在架一册一条；改条码/馆藏地点请用「馆藏列表」，改排架号/卷册号请用简编表单">
        由馆藏生成
      </el-tag>
      <el-button v-if="!isControlField(f) && !isManaged(f)" size="small" link type="primary" @click="addSub(f)">+ 子字段</el-button>
      <el-button v-if="!isManaged(f)" size="small" link type="danger" @click="fields.splice(fi, 1)">删字段</el-button>
    </div>
    <div v-if="isControlField(f)">
      <el-input v-model="f.data" size="small" placeholder="控制字段内容" />
    </div>
    <div v-for="(s, si) in f.sf" :key="si" style="display: flex; gap: 6px; margin: 4px 0">
      <el-input v-model="s[0]" style="width: 46px" size="small" placeholder="$"
        :disabled="isManaged(f)">
        <template #prepend>$</template>
      </el-input>
      <el-input v-model="s[1]" size="small" :disabled="isManaged(f)"
        :placeholder="subfieldName(f.tag, s[0]) || '子字段内容'" />
      <el-button v-if="!isManaged(f)" size="small" link type="danger" @click="f.sf!.splice(si, 1)">✕</el-button>
    </div>
  </div>
  <el-button size="small" @click="addField">+ 添加字段</el-button>
  <div style="text-align: center; margin-top: 20px">
    <el-button type="primary" size="large" :loading="saving" style="min-width: 200px"
      @click="emit('save')">保存入库</el-button>
  </div>
</template>

<script setup lang="ts">
/**
 * 「全字段 MARC」编辑面板（拆自 `RecordEdit.vue`，见）。
 *
 * 与「MARC 预览」的区别：预览是**只读**展示，这里是**可编辑**的 —— 增删字段、
 * 改指示符与内容、增删子字段。原先这段散在 `RecordEdit.vue` 三处、彼此相距上千行
 * （模板 225–254 / `isControlField` 612–621 / `addSub`+`addField` 1864–1867），
 * 现整块合成一个组件，标记与样式**逐字未改**。
 *
 * ## 状态归属：为什么 `fields` 是「传进来 + 原地改」而不是 emit 出去改
 *
 * `fields` 是**三个 tab 共享的唯一数据源**（简编表单靠 `usePanelSync` 与它双向同步、
 * MARC 预览直接读它），所以只能留在页面；搬进本组件的只是「怎么显示 + 怎么增删」。
 *
 * 本组件对传入的数组**原地增删**（`splice` / `push`）—— 与拆分前的写法完全一致，
 * 操作的是**同一份响应式数组**。之所以不改成逐字段 `emit`：那样每个输入框都要写成
 * `:model-value` + `@update:model-value`，把 `v-model` 的语义拆散（IME 组合、
 * `el-input` 的清除/回填行为都在 `v-model` 里），超出「纯搬家、零行为变更」的边界
 * （§5 验收标准 1）。Vue 3 的 props 是**浅只读**，改嵌套内容是允许的、不报错。
 *
 * ## 为什么字典留在页面、以函数下传
 *
 * 字段中文名来自 `GET /api/marc-fields`（页面在 `load()` 里 `await loadMarcFields()`
 * 拉取并缓存）。**把取字典也搬进来会改变请求时序**（子组件挂载点与 `load()` 的 await
 * 顺序不同），而且页面侧 `fieldName` / `subfieldName` 有既有测试直接断言
 * （`tests/recordEditSimpleForm.test.ts` 的「全字段 MARC：字段中文名」一组）。
 * 因此只把**查表函数**下传，取数与缓存仍归页面。
 */
import type { MarcField } from '../api'

const props = defineProps<{
  /** 当前记录的全部字段。页面持有（见上方「状态归属」），本组件**原地增删**。 */
  fields: MarcField[]
  /** 保存请求在途。页面持有 —— 顶部工具条的「保存入库」与这里共用同一个标志。 */
  saving: boolean
  /** 字段中文名查表（查不到返回空串，模板据此不渲染那个灰字）。 */
  fieldName: (tag: string) => string
  /** 子字段中文名查表（给「内容」框做占位；查不到返回空串，模板退回默认占位）。 */
  subfieldName: (tag: string, code: string) => string
}>()

/** 页面把「保存入库」的副作用留在自己手里（它还要管质检拦截、条码、强制入库）。 */
const emit = defineEmits<{ save: [] }>()

/**
 * 控制字段（001–009）没有指示符与子字段，只有 data —— 口径与后端一致
 * （`core/marc.py::is_control` = tag 以 "00" 开头）。
 * ⚠️ 后端 `to_json` 对控制字段只输出 `{tag, data}`，**不带** isControl 标记；
 * 模板历史上访问的 `f.isControl` 从未有人赋值，恒为 undefined —— 控制字段在
 * "全字段"页一直显示两个无效的指示符框、数据框永不出现（vue-tsc 接入时揪出）。
 */
function isControlField(f: MarcField): boolean {
  return f.tag.startsWith('00')
}

/**
 * 受管字段：内容由后端派生，**不可在 MARC 页编辑**。
 *
 * 目前只有 905（馆藏字段）——它由 `services.batch._rebuild_905` 按在架馆藏
 * 每册重建一条，从界面改它保存后会被覆盖回原样。设为只读并提示去处，
 * 比"改完发现白改"清楚（2026-10-25 905 链路审查 P1）。
 */
function isManaged(f: MarcField): boolean {
  return f.tag === '905'
}

function addSub(f: MarcField) { (f.sf ??= []).push(['a', '']) }
/** 新增字段默认 `300`（附注项）；指示符给**一个空格**，与后端定长指示符口径一致。 */
function addField() {
  props.fields.push({ tag: '300', i1: ' ', i2: ' ', sf: [['a', '']] })
}
</script>

<style scoped>
.marc-field-name { color: var(--el-text-color-secondary); font-size: 12px; }
</style>
