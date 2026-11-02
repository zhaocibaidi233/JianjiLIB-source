<template>
  <div class="mrk-preview">
    <div v-for="(row, i) in rows" :key="i" class="mrk-row"><span
      class="mrk-tag">={{ row.tag }}</span><span class="mrk-gap">{{ '  ' }}</span><span
      v-if="!row.control" class="mrk-ind">{{ row.ind }}</span><span
      v-if="row.control" class="mrk-data">{{ row.data }}</span><template v-else><span
        v-for="(sf, j) in row.subfields" :key="j" class="mrk-sf"><span
          class="mrk-code">${{ sf[0] }}</span>{{ sf[1] }}</span></template></div>
  </div>
</template>

<script setup lang="ts">
/**
 * MARC 预览（MRK 文本形态）：把当前字段列表拍成「一行一字段」的等宽文本，
 * 与「简编表单 / 全字段 MARC」两个视图并列，纯只读。
 *
 * 为什么单独成组件：这段逻辑原先埋在 `RecordEdit.vue`（2141 行）里，
 * 而它**与页面其它部分零耦合** —— 输入只有 `fields`、输出是一段字符串，
 * 是那次拆分里风险最低的一块（见）。
 *
 * MRK 形态的口径（与原实现逐字一致，不得擅改）：
 *   - 数据字段（有 `sf` 或有指示符）：`=200  1  $a题名$f责任者`
 *     · 指示符缺省时补**一个空格**（不是空串）——MARC 的指示符位是定长两字符，
 *       空串会让 `1` 后的位置塌陷，人工核对时会看错列。
 *   - 控制字段（只有 `data`、无 `sf` 也无指示符）：`=001  93709`
 *   - 两者之间是两个空格（`=tag` 后固定 2 空格），字段之间用换行连接。
 *
 * ## 为什么拆成 span（2026-10-24 用户反馈「加点颜色更易读」）
 *
 * 原来是 `<pre>{{ mrkText }}</pre>` 一整段纯文本。现在按结构着色：字段号、
 * 指示符、子字段代码各一色，隔行加浅底，扫一屏时靠颜色就能定位。
 *
 * **文字内容与相对位置一个字符都不能变**——这个面板经常被直接复制走，
 * 改排版等于改数据。所以：
 *   - 不用 `<pre>`：`<pre>` 会把模板里的换行与缩进原样渲染出来，结构化之后全乱；
 *     改用 `white-space: pre-wrap` 的 div。
 *   - 固定空格用 `{{ '  ' }}` 插值写死——模板里的连续空白会被 Vue 压缩掉。
 *   - 各 span 之间不留空白，标签挤在一起写，避免被插入多余字符。
 */
import { computed } from 'vue'
import type { MarcField } from '../api'

const props = defineProps<{ fields: MarcField[] }>()

interface Row {
  tag: string
  ind: string
  control: boolean
  data: string
  subfields: Array<[string, string]>
}

const rows = computed<Row[]>(() => props.fields.map(f => {
  if (f.sf || f.i1 !== undefined) {
    return {
      tag: f.tag,
      // 指示符缺省补一个空格：MARC 的指示符位是定长两字符
      ind: `${f.i1 ?? ' '}${f.i2 ?? ' '}`,
      control: false,
      data: '',
      subfields: (f.sf ?? []) as Array<[string, string]>,
    }
  }
  return { tag: f.tag, ind: '', control: true, data: f.data ?? '', subfields: [] }
}))
</script>

<style scoped>
.mrk-preview {
  font-family: Consolas, Menlo, monospace;
  font-size: 12px; line-height: 1.65;
  white-space: pre-wrap; word-break: break-all;
  margin: 0; padding: 10px 0;
  background: var(--el-fill-color-lighter); border: 1px solid var(--el-border-color-lighter); border-radius: 6px;
  max-height: calc(100vh - 240px); overflow: auto;
}

/* 隔行浅底：一屏二十多行时，靠底色就能定位，不用逐行数 */
.mrk-row { padding: 0 14px; }
.mrk-row:nth-child(even) { background: rgba(23, 94, 84, 0.04); }
.mrk-row:hover { background: var(--primary-softer, #f0f6f3); }

/* 字段号：结构锚点，最该被扫到 */
.mrk-tag { color: var(--primary, #175e54); font-weight: 600; }

/* 指示符：定长两字符，单独一色并垫浅底，与后面的子字段拉开层次 */
.mrk-ind {
  color: var(--accent-deep, #9a6a18);
  background: var(--accent-soft, #faf0dd);
  border-radius: 3px;
}

/* 子字段代码（$a）：与字段号区分开，但不能抢过内容 */
.mrk-code { color: var(--info, #3c6e9f); }

/* 控制字段的定长数据（001 控制号、005 时间戳）：偏灰，它不是著录内容 */
.mrk-data { color: var(--text-secondary, #5c6b64); }
</style>
