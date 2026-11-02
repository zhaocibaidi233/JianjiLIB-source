<template>
  <el-dialog v-model="visible" title="图书转库" width="860px" :close-on-click-modal="false"
    @closed="reset">
    <el-tabs v-model="tab">
      <el-tab-pane label="转库" name="go">
        <el-alert type="info" :closable="false" show-icon style="margin-bottom: 10px"
          title="转库会移动书目与馆藏"
          description="源库不再保留；外借册不转。" />

        <div class="app-row">
          <span style="font-size: 13px; color: var(--el-text-color-regular)">从</span>
          <el-input :model-value="fromName" disabled style="width: 180px" />
          <span style="font-size: 13px; color: var(--el-text-color-regular)">转到</span>
          <el-select v-model="toLib" placeholder="选择目标图书库" style="width: 200px"
            @change="plan = null">
            <el-option v-for="l in targets" :key="l.id" :label="l.name" :value="l.id" />
          </el-select>
        </div>

        <el-divider content-position="left" style="margin: 14px 0 10px">编号处理方式</el-divider>
        <div style="display: flex; gap: 16px; flex-wrap: wrap; align-items: center">
          <span style="font-size: 13px; color: var(--el-text-color-regular)">条码</span>
          <el-radio-group v-model="barcodeMode" size="small" @change="plan = null">
            <el-radio-button value="keep">保留原号</el-radio-button>
            <el-radio-button value="renumber">按目标库重新编号</el-radio-button>
          </el-radio-group>
          <span style="font-size: 13px; color: var(--el-text-color-regular)">索书号</span>
          <el-radio-group v-model="callnoMode" size="small" @change="plan = null">
            <el-radio-button value="keep">保留原号</el-radio-button>
            <el-radio-button value="renumber">按目标库重新取号</el-radio-button>
          </el-radio-group>
        </div>
        <div style="display: flex; gap: 16px; margin-top: 8px">
          <el-checkbox v-model="includeInactive" @change="plan = null">连同已注销 / 已遗失的册</el-checkbox>
          <el-checkbox v-model="keepShelfNo" @change="plan = null">保留原排架号</el-checkbox>
        </div>

        <div class="app-row td-actions">
          <el-button :disabled="!toLib || toLib === fromId" :loading="previewing"
            @click="doPreview">预览影响</el-button>
          <div class="app-spacer" />
          <el-button type="danger" :disabled="!canApply" :loading="applying" @click="doApply">
            执行转库{{ plan ? `（${plan.copies} 册）` : '' }}
          </el-button>
        </div>

        <el-alert v-if="scopeTip" type="warning" :closable="false" show-icon
          style="margin-bottom: 10px" :title="scopeTip" />

        <template v-if="plan">
          <el-alert v-if="plan.conflicts.length" type="error" :closable="false" show-icon
            style="margin-bottom: 10px"
            :title="`有 ${plan.conflicts.length} 处编号冲突，整批不能转`"
            description="改用「重新编号」，或先在目标库修改这些号。" />
          <el-descriptions :column="3" size="small" border style="margin-bottom: 10px">
            <el-descriptions-item label="将转记录">{{ plan.records }} 条</el-descriptions-item>
            <el-descriptions-item label="将转册数">{{ plan.copies }} 册</el-descriptions-item>
            <el-descriptions-item label="并入已有同种">
              {{ plan.plans.filter((p: any) => p.target.mode === 'reuse').length }} 条
            </el-descriptions-item>
          </el-descriptions>

          <el-table :data="plan.conflicts" size="small" border v-if="plan.conflicts.length"
            style="margin-bottom: 10px" max-height="160">
            <el-table-column label="冲突" width="90">
              <template #default="{ row }">
                <el-tag size="small" type="danger">
                  {{ row.kind === 'barcode' ? '条码' : '索书号' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="value" label="号码" width="160" />
            <el-table-column prop="title" label="题名" min-width="200" show-overflow-tooltip />
          </el-table>

          <el-table :data="plan.skipped" size="small" border v-if="plan.skipped.length"
            style="margin-bottom: 10px">
            <el-table-column prop="title" label="不转的记录" min-width="220" show-overflow-tooltip />
            <el-table-column prop="reason" label="原因" min-width="240" />
          </el-table>

          <el-table :data="copies" size="small" border max-height="260">
            <el-table-column prop="title" label="题名" min-width="180" show-overflow-tooltip />
            <el-table-column label="目标" width="90">
              <template #default="{ row }">
                <el-tag size="small" :type="row.mode === 'reuse' ? 'success' : 'info'">
                  {{ row.mode === 'reuse' ? '并入' : '新建' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="old_barcode" label="原条码" width="120" />
            <el-table-column prop="new_barcode" label="新条码" width="120" />
            <el-table-column prop="old_call_no" label="原索书号" width="120" show-overflow-tooltip />
            <el-table-column prop="new_call_no" label="新索书号" width="120" show-overflow-tooltip />
          </el-table>
        </template>
      </el-tab-pane>

      <el-tab-pane label="转库记录" name="log">
        <div class="app-row td-log-bar">
          <el-input v-model="logQ" placeholder="题名或条码（新旧条码均可）" clearable
            style="width: 240px" @keyup.enter="loadLog" />
          <el-button @click="loadLog">查询</el-button>
          <div class="app-spacer" />
          <span class="app-muted">共 {{ logTotal }} 条</span>
        </div>
        <el-table :data="logRows" size="small" border v-loading="logLoading" max-height="380">
          <el-table-column prop="batch_no" label="批号" width="110" />
          <el-table-column prop="title" label="题名" min-width="160" show-overflow-tooltip />
          <el-table-column label="流向" width="170">
            <template #default="{ row }">
              {{ row.from_library }} → {{ row.to_library }}
            </template>
          </el-table-column>
          <el-table-column prop="old_barcode" label="原条码" width="110" />
          <el-table-column prop="new_barcode" label="新条码" width="110" />
          <el-table-column prop="operator" label="操作人" width="90" />
          <el-table-column prop="created_at" label="时间" width="150" />
        </el-table>
      </el-tab-pane>
    </el-tabs>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { apiErrorText, listTransfers, transferApply, transferPreview } from '../api'
import type { TransferPreview } from '../api'

/**
 * 图书转库对话框。
 *
 * **为什么必须先预览**：转库不可逆，还要改条码与索书号。用户按下之前必须看到
 * 「动多少册、哪些动不了、号会变成什么」；有冲突时执行按钮直接禁用 —— 后端还会
 * 再拒一次（整批 409），但界面不该让人先撞一次墙。
 *
 * **选书范围**跟随图书库页：逐条勾选（`ids`）或「按筛选全部」（`filter` +
 * `expected_total`），与批量删除同一套口径，不再另造一种。
 */
const props = defineProps<{
  modelValue: boolean
  fromId: number | null
  fromName: string
  libs: any[]
  /** 逐条勾选的记录 id（`byFilter` 为 false 时有效） */
  ids: number[]
  byFilter: boolean
  filter: Record<string, any>
  /** 页面显示的筛选结果条数（`byFilter` 时的 expected_total） */
  total: number
}>()
const emit = defineEmits<{ 'update:modelValue': [boolean]; done: [] }>()

const visible = computed({
  get: () => props.modelValue,
  set: (v: boolean) => emit('update:modelValue', v),
})

const tab = ref('go')
const toLib = ref<number | null>(null)
const barcodeMode = ref<'keep' | 'renumber'>('keep')
const callnoMode = ref<'keep' | 'renumber'>('keep')
const includeInactive = ref(false)
const keepShelfNo = ref(false)
const plan = ref<TransferPreview | null>(null)
const previewing = ref(false)
const applying = ref(false)

// 日志
const logQ = ref('')
const logRows = ref<any[]>([])
const logTotal = ref(0)
const logLoading = ref(false)

const targets = computed(() => props.libs.filter((l: any) => l.id !== props.fromId))
const canApply = computed(() => !!plan.value && plan.value.copies > 0
  && !plan.value.conflicts.length)
const scopeTip = computed(() => (props.byFilter
  ? `转的是当前筛选条件下的全部 ${props.total} 条（跨页），不只是本页勾选的。`
  : `转的是勾选的 ${props.ids.length} 条记录。`))

/** 明细表要的是「一册一行」，后端 plan 是「一条记录带 N 册」，这里摊平。 */
const copies = computed(() => {
  const out: any[] = []
  for (const p of plan.value?.plans ?? []) {
    for (const c of p.copies) {
      out.push({ title: p.title, mode: p.target.mode, ...c })
    }
  }
  return out
})

function payload() {
  const base: any = {
    from_library_id: props.fromId,
    to_library_id: toLib.value,
    barcode_mode: barcodeMode.value,
    callno_mode: callnoMode.value,
    include_inactive: includeInactive.value,
    keep_shelf_no: keepShelfNo.value,
  }
  if (props.byFilter) {
    base.filter = props.filter
    base.expected_total = props.total
  } else {
    base.ids = props.ids
  }
  return base
}

async function doPreview() {
  previewing.value = true
  try {
    plan.value = await transferPreview(payload())
  } catch (e: any) {
    ElMessage.error(apiErrorText(e, '转库预览'))
    plan.value = null
  } finally {
    previewing.value = false
  }
}

async function doApply() {
  const n = plan.value?.copies ?? 0
  try {
    await ElMessageBox.confirm(
      `确定把 ${n} 册书转到「${targets.value.find((l: any) => l.id === toLib.value)?.name}」？\n\n` +
      `转库后源库不再保留这些书，变更后的编号不可自动还原，原号可在转库记录中查到。`,
      '执行图书转库',
      { type: 'warning', confirmButtonText: '转库', cancelButtonText: '取消' })
  } catch { return }
  applying.value = true
  try {
    const d = await transferApply(payload())
    ElMessage.success(
      `已转 ${d.moved_copies} 册（批号 ${d.batch_no}），` +
      `目标库新建 ${d.created_records} 条、源库删除 ${d.deleted_source_records} 条`)
    plan.value = null
    visible.value = false
    emit('done')
  } catch (e: any) {
    ElMessage.error(apiErrorText(e, '执行转库'))
  } finally {
    applying.value = false
  }
}

async function loadLog() {
  logLoading.value = true
  try {
    const d = await listTransfers({ q: logQ.value || undefined, limit: 100 })
    logRows.value = d.items
    logTotal.value = d.total
  } catch (e: any) {
    ElMessage.error(apiErrorText(e, '加载转库记录'))
  } finally {
    logLoading.value = false
  }
}

function reset() {
  plan.value = null
  toLib.value = null
  barcodeMode.value = 'keep'
  callnoMode.value = 'keep'
  includeInactive.value = false
  keepShelfNo.value = false
}

watch(() => props.modelValue, async (v) => {
  if (v) { tab.value = 'go'; await loadLog() }
})
// 目标库列表变化（源库切换）时清掉已失效的选择
watch(() => props.fromId, () => { toLib.value = null; plan.value = null })
</script>

<style scoped>
/* 工具栏与上下的间隔（原靠行内 margin，.app-row 不带 margin） */
.td-actions { margin: 14px 0 10px; }
.td-log-bar { margin-bottom: 10px; }
</style>
