<template>
  <!-- 价格计算器：整值替换之外，还能直接敲算式（如 `135/5` = 单本价）。
       结果做成**浮层**（`.price-result`，position: absolute）叠在框右下角，
       不占布局高度 —— 否则露出结果那一行会把录入栏整体撑高。
       提交时机：回车 / 失焦；未提交前算式**绝不**进 `010$d`（两道守卫在页面脚本里）。 -->
  <!-- 五个监听器全挂在**本组件自己的根元素**上，且都挑**会冒泡**的事件
       （focusin / focusout / mousedown / mouseup / keyup）：
       ① `el-input` 把 `focus` / `blur` 声明成了组件事件（且带
          `evt instanceof FocusEvent` 校验），只能在 Element Plus 内部那套
          capture 监听里被触发 —— 挂在它上面等于把"全选"这件小事绑在别人家的
          实现细节上；
       ② 会冒泡的事件挂在容器上，用**普通鼠标/键盘事件**就能从头验到尾
          （`tests/priceInput.test.ts` 就是这么测的，不 stub 任何东西）。
       行为与拆分前一致：焦点进入本框（输入框是唯一可聚焦后代）→ 全选；
       焦点离开本框 → 提交。 -->
  <div class="price-wrap" @focusin="onFocus" @focusout="emit('commit')"
    @keyup.enter="emit('enter')" @mousedown="onMouseDown" @mouseup="onMouseUp">
    <!-- `data-field="price"` 仍落在**原生 input** 上（Element Plus 把透传属性绑给内层 input）：
         录入栏的 ↑/↓ 导航靠 `closest('[data-field]')` 认字段，页面级测试也靠这个选择器取 DOM 值。 -->
    <el-input ref="inputRef" v-model="draft" style="width: 100%" data-field="price">
      <template #prepend><span class="unit-fixed">{{ currency }}</span></template>
    </el-input>
    <div v-if="result" class="price-result">{{ result }}</div>
    <div v-if="error" class="suggest-line price-error">算式无法计算</div>
  </div>
</template>

<script setup lang="ts">
/**
 * 价格输入框（拆自 `RecordEdit.vue` 录入栏「价格」那一格， 阶段 4a）。
 *
 * ## 零状态
 *
 * 4 个 props + 3 个 emit（其中 1 个是 `v-model`），组件自己不持有任何**数据**状态：
 * `modelValue` 是页面顶层的 `priceDraft`（那个"草稿"见 `RecordEdit.vue` 的长注释），
 * 求值结果与失败标志也都是页面算好的只读值。
 *
 * ## 唯二的两处"内部东西"
 *
 * 1. **焦点进入即全选**：编目时价格基本是整值替换（39.80 → 45.00），全选后直接敲新数字。
 *    实现上有个经典坑——鼠标点进来时焦点先到、紧接着的 mouseup 会把刚做的全选收成插入符，
 *    全选就白做了。所以用两个标志位拦掉那一次 mouseup；键盘进入（Tab / 回车跳格）
 *    不经过 mousedown，不受影响。这两个标志位是**瞬时 DOM 行为**、不是数据，
 *    且天然属于"这个框自己的事"，因此随组件搬进来（留在页面反而要把它拆成
 *    focus / mousedown / mouseup 三个 emit，那才是把复杂度换个地方藏）。
 * 2. **原生 input 的 ref**：只用于全选，以及 `defineExpose({ focus })` 供页面的
 *    ↑/↓ 导航与回车跳格链（`usePanelNav`）把光标送进来 —— 和 `el-input` 一样按 `.focus()` 调用。
 *
 * ⚠️ **与拆分前的一处（唯一的）实现差别**：监听器从 `el-input` 搬到了本组件的根 `div`，
 * 事件从 `focus` / `blur` 换成会冒泡的 `focusin` / `focusout`（原因见模板注释）。
 * 触发时机相同（本框内唯一可聚焦的元素就是那个输入框），但这条要在浏览器里过一遍
 *  的第 3 项（敲 `135/5` → 回车 → 结果浮层 + 失焦提交）。
 *
 * ## 样式为什么有两份
 *
 * `.unit-fixed`（prepend 里的货币码小字）与 `.suggest-line`（行内小字形态）在
 * `RecordEdit.vue` 里另有使用者（简编表单的单位后缀、排架号警告行），scoped 样式不跨组件生效，
 * 故本组件自带一份 —— 与 `HoldingList.vue` 的 `.preview-line` 同理。
 */
import { computed, ref } from 'vue'

const props = defineProps<{
  /** 价格草稿（页面顶层 `priceDraft`）——可能是用户正在敲、尚未提交的算式。 */
  modelValue: string
  /** prepend 里显示的货币码（页面 `currency`）。 */
  currency: string
  /** 求值成功的浮层文本（`= 27.00`）；空串 = 不显示浮层。 */
  result: string
  /** 算式求值失败的短提示（1.5s 自动消失的旗标，页面持有）。 */
  error: boolean
}>()

const emit = defineEmits<{
  'update:modelValue': [v: string]
  /** 失焦即提交（页面 `commitPrice`：只对算式求值，失败给提示、不改值）。 */
  commit: []
  /** 回车：页面先提交再跳格到条码框（主链路「条码回车即入库」不变）。 */
  enter: []
}>()

/** `v-model` 的桥：props 只读，写入一律 emit 回页面。 */
const draft = computed({
  get: () => props.modelValue,
  set: (v: string) => emit('update:modelValue', v),
})

const inputRef = ref()

let clicked = false        // 本次点击落在价格框里
let selectPending = false  // 需要拦掉紧随其后的那一次 mouseup

function onMouseDown() { clicked = true }

function onFocus() {
  inputRef.value?.select?.()
  selectPending = clicked
  clicked = false
}

function onMouseUp(e: MouseEvent) {
  clicked = false
  if (!selectPending) return
  selectPending = false
  e.preventDefault()          // 否则 mouseup 会把全选收成插入符
}

/** 把光标送进价格框（`usePanelNav` 的 ↑/↓ 与回车跳格链按 `.focus()` 调用）。 */
function focus() { inputRef.value?.focus?.() }

defineExpose({ focus })
</script>

<style scoped>
/* 结算浮层与失败提示：`position: absolute` **不占布局高度**（否则露出的结果行会把
   录入栏整体撑高）；等宽字体、teal 系色、贴框右下角。失败提示是框下方一行小字。 */
.price-wrap { position: relative; width: 100%; }
.price-result {
  position: absolute; right: 8px; bottom: 3px; z-index: 5; pointer-events: none;
  font-family: Consolas, Menlo, monospace; font-size: 12px; line-height: 18px;
  /* teal 系：底色/描边用半透明（叠在任何主题上都成立），字色拿 emerald 与主题前景混，
     亮色≈原来的 #0f766e，暗色自动变浅（写死 #0f766e 在深底上几乎看不见）。 */
  color: color-mix(in srgb, #10b981 60%, var(--app-text));
  background: color-mix(in srgb, #10b981 14%, transparent);
  border: 1px solid color-mix(in srgb, #10b981 45%, transparent);
  border-radius: 3px; padding: 0 5px;
}
/* 失败提示在页面里是 `class="suggest-line price-error"` 两个类的组合，两个类都搬过来，
   DOM 标记与拆分前逐字一致（`suggest-line` 提供 flex + 上边距 + line-height:1 的行内小字形态）。 */
.suggest-line { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 2px; line-height: 1; }
.price-error { color: var(--el-color-warning); font-size: 12px; }
/* 复用者见文件头注释（页面简编表单的单位后缀也用这个类）。 */
.unit-fixed { color: var(--el-text-color-secondary); user-select: none; }
</style>
