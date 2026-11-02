<template>
  <!-- 馆藏条码栏：并入录入卡片且始终显示——录完条码就能在下方立即核对 -->
  <el-divider style="margin: 14px 0 8px" />
  <!-- 卷册号 / 排架号都是**种级**，塞进每册一行会重复显示同一值（噪音），
       故只在馆藏卡片头部给一行摘要（空值沿用 `—` 占位）。 -->
  <div class="holding-meta-line" style="margin-bottom: 8px">
    卷册号：{{ volume || '—' }}<template v-if="shelfNoEnabled"> ｜ 排架号：{{ shelfNo || '—' }}</template>
  </div>
  <div class="holding-head">
    <span class="holding-title">馆藏（{{ holdings.length }} 册）</span>
    <div style="flex: 1"></div>
    <el-button v-if="holdings.length" size="small" link type="primary"
      @click="emit('print')">打印书标 →</el-button>
  </div>
  <el-table :data="holdings" size="small" border
    :row-class-name="rowClassName">
    <template #empty>
      <div class="holding-empty" aria-label="当前没有馆藏">
        <el-icon><Tickets /></el-icon>
      </div>
    </template>
    <el-table-column label="条码" width="104" align="center">
      <template #default="{ row }">
        <!-- 已注销 / 已遗失的册不能直接改（后端会 400）：它们已不属于馆藏，
             改了会让注销账与实物对不上。要改先去条码管理页恢复。 -->
        <el-input v-if="!row.status || row.status === 'AVAILABLE'"
          v-model="row.barcode" class="holding-input" size="small" @change="emit('save', row)" />
        <span v-else style="font-family: monospace">{{ row.barcode }}</span>
      </template>
    </el-table-column>
    <el-table-column label="馆藏地点" align="center">
      <template #default="{ row }">
        <div v-if="!row.status || row.status === 'AVAILABLE'" class="holding-location">
          <el-input v-model="row.location" class="holding-input" size="small" placeholder="未指定" aria-label="馆藏地点"
            @change="emit('save', row)" />
          <span v-if="shelfNo" class="holding-shelf">{{ shelfNo }}</span>
        </div>
        <span v-else style="color: var(--el-text-color-secondary)">
          {{ holdingLocationLabel(row.location, shelfNo) }}
        </span>
      </template>
    </el-table-column>
    <el-table-column label="状态" width="86" align="center">
      <template #default="{ row }">
        <el-tag v-if="!row.status || row.status === 'AVAILABLE'" type="success" size="small">
          在架
        </el-tag>
        <el-tag v-else-if="row.status === 'WITHDRAWN'" type="info" size="small">已注销</el-tag>
        <el-tag v-else-if="row.status === 'LOST'" type="danger" size="small">已遗失</el-tag>
        <!-- 停流通/另库保存（D14）：不是注销、也不是遗失，必须单独标识（否则会落到
             下面的通用兜底标签，馆员分不清"不能借"到底是哪一种不能借） -->
        <el-tag v-else-if="row.status === 'SUSPENDED'" type="warning" effect="dark" size="small">
          {{ holdingStatusLabel(row.status) }}
        </el-tag>
        <el-tag v-else type="warning" size="small">{{ holdingStatusLabel(row.status) }}</el-tag>
      </template>
    </el-table-column>
    <el-table-column label="" width="44" align="center">
      <template #default="{ row }">
        <el-button size="small" link type="danger" @click="emit('delete', row)">✕</el-button>
      </template>
    </el-table-column>
  </el-table>
  <div v-if="inactiveCount" style="margin-top: 6px; color: var(--el-text-color-secondary); font-size: 12px">
    其中 {{ inactiveCount }} 册已注销或已遗失，不计入馆藏统计；
    恢复或改条码请到「条码管理」页。
  </div>
</template>

<script setup lang="ts">
/**
 * 「馆藏列表」侧栏块（拆自 `RecordEdit.vue`，见  §3 块 F）。
 *
 * 原先散在 `RecordEdit.vue` 两处彼此相距上千行：模板 371–422（分割线 +
 * 卷册号/排架号摘要 + 「馆藏 N 册」头 + `el-table` 四列 + 已注销提示）与
 * `inactiveHoldingCount` computed（脚本 822–824）。现整块合成一个组件，
 * 标记与样式**逐字未改**。
 *
 * ## 状态归属
 *
 * `holdings` 是**记录级的唯一数据源**（保存入库、条码续号、记录字段同步都动它），
 * 只能留在页面；本组件只负责「怎么显示 + 怎么改一行」。与 `MarcPane` 同一原则，
 * `el-input` 仍用 `v-model` 对 `row.barcode` / `row.location` **原地改** ——
 * 与拆分前逐字一致（改成 `:model-value` + emit 会拆散 `v-model` 语义）。
 *
 * `inactiveCount` 是**本块私有**的派生值（原先的 `inactiveHoldingCount` computed 只在这
 * 一处用），随块搬进来，页面少一个 prop；`volume` / `shelfNo` / `shelfNoEnabled`
 * 是页面 `extra` 与开关的只读投影。
 *
 * ## 三个动作 emit 回页面
 *
 * - `save`  —— 确认条码/地点时**除了落库这一行，还要同步保存记录本身**（否则录入面板里
 *              改过的分类号/书次号/价格会丢），这条链路要动 `fields` / `findings` /
 *              `populateSimple`，副作用属页面；
 * - `delete` —— 要弹二次确认，最后一条条码还要**就地接到「删除整条记录」**并回列表页；
 * - `print` —— 走路由 `/labels?ids=<id>`（组件不引 `useRouter`，可独立测）。
 */
import { computed } from 'vue'
import { Tickets } from '@element-plus/icons-vue'
import { holdingLocationLabel, holdingStatusLabel } from '../../lib/displayLabels'

interface HoldingRow {
  id: number
  barcode?: string
  location?: string
  status?: string
}

const props = defineProps<{
  /** 该记录下的全部馆藏（含已注销/已遗失）。页面持有；本组件对 `row` **原地改**。 */
  holdings: HoldingRow[]
  /** 卷册号（种级，记录级列），页面的 `extra.volume` 只读投影。 */
  volume: string
  /** 排架号（种级，记录级列），页面的 `extra.shelfNo` 只读投影。 */
  shelfNo: string
  /** 排架号开关（按图书库设置）。关掉时摘要行不显示「排架号」。 */
  shelfNoEnabled: boolean
}>()

/** 三个动作的副作用都在页面（见文件头注释），组件只负责"点了"。 */
const emit = defineEmits<{
  /** 确认某行的条码/地点（页面 `saveHolding(row)`）。 */
  save: [row: HoldingRow]
  /** 删除某行（页面 `delHolding(row)`，含二次确认与"最后一条"分支）。 */
  delete: [row: HoldingRow]
  /** 点「打印书标 →」（页面跳 `/labels?ids=<id>`）。 */
  print: []
}>()

/**
 * 已注销 / 已遗失的册数（这些册不计入馆藏统计、不进 905，也不能直接改号）。
 *
 * 停流通（SUSPENDED）**不算**在内：它不进 905，但仍是馆藏资产、计入馆藏总量
 * （契约 D14）。若把它一起算进来，下面那句"不计入馆藏统计"就成了假话。
 */
const inactiveCount = computed(
  () => props.holdings.filter(
    h => h.status && h.status !== 'AVAILABLE' && h.status !== 'SUSPENDED').length)

/** 非「在架」的行整行置灰（`:deep(.row-inactive)` 见本组件样式）。 */
function rowClassName({ row }: { row: HoldingRow }): string {
  return row.status && row.status !== 'AVAILABLE' ? 'row-inactive' : ''
}
</script>

<style scoped>
/* 与录入栏的条码预览行同一形态。该类在页面（录入面板的条码预览）也有一份 ——
   scoped 样式不跨组件生效，故本组件自带一份；改形态时两处要一起改。 */
.holding-location { display: flex; align-items: center; justify-content: center; gap: 6px; min-width: 0; }
.holding-location .el-input { flex: 0 1 200px; min-width: 0; }
:deep(.holding-input .el-input__inner) { text-align: center; }
.holding-shelf {
  flex: none;
  padding: 0 6px;
  line-height: 22px;
  border: 1px solid var(--line);
  border-radius: 5px;
  background: var(--surface-soft);
  font-size: 11px;
  color: var(--muted);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.holding-meta-line {
  padding: 5px 8px;
  border: 1px solid var(--line);
  border-radius: 7px;
  background: var(--surface-soft);
  font-family: Consolas, Menlo, monospace;
  font-size: 11.5px; color: var(--muted); line-height: 1.6;
  word-break: break-all;
}
.holding-head { display: flex; align-items: center; margin-bottom: 6px; }
.holding-title { color: var(--ink); font-size: 13px; font-weight: 600; }
:deep(.el-divider--horizontal) { border-top-color: var(--primary-soft); }
:deep(.el-table) { --el-table-border-color: var(--line); }
:deep(.el-table th.el-table__cell) {
  background: var(--primary-softer);
  color: var(--ink);
  font-weight: 600;
}
:deep(.el-table__empty-block) { min-height: 96px; background: var(--surface-soft); }
.holding-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  color: var(--muted);
  font-size: 12px;
}
.holding-empty .el-icon { font-size: 20px; color: var(--primary); opacity: 0.6; }
/* 已注销 / 已遗失的行：`row-class-name` 把类挂在 `el-table` 内部的 `<tr>` 上，
   scoped 样式够不到，必须 `:deep()` 穿透。 */
:deep(.row-inactive) { background: var(--surface-soft); color: var(--muted); }
</style>
