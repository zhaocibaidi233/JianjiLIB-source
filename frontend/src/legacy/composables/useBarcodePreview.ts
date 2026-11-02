import { ref, watch } from 'vue'
import type { ComputedRef, Ref } from 'vue'
import { http } from '../api'

/** 条码解析结果（RecordEdit 的 parseBarcodeInput：+N/-N 相对号、位数校验都在里面）。 */
interface ParsedInput {
  codes: string[]
  error?: string
}

/**
 * 条码框下方的预览与冲突检测（RecordEdit 录入栏）。
 *
 * 250ms 去抖只压"发请求前"，压不住"已发出的旧响应后到"—— 慢的旧响应会把
 * 冲突提示写回旧的一批条码。所以除了去抖，还有"最新值获胜"的序号守卫：
 * 每次请求带自增序号，回包时序号不是最新就丢弃。
 */
export function useBarcodePreview(opts: {
  /** 条码输入框的值（支持 +N / -N / 条码号）——响应式源 */
  barcodeInputValue: Ref<string>
  recordIdOrNull: ComputedRef<number | null>
  currentLibId: ComputedRef<number | undefined>
  /** 条码解析（RecordEdit 提供：依赖 lastBarcode 状态与条码位数设置，不在此重复实现） */
  parseBarcodeInput: (input: string) => ParsedInput
}) {
  const { barcodeInputValue, recordIdOrNull, currentLibId, parseBarcodeInput } = opts

  const barcodePreviewText = ref('')
  const barcodeConflicts = ref<string[]>([])
  // ⚠️ 用 undefined 不用 null：strict 下 clearTimeout 的重载只收
  // `Timeout | undefined`（DOM/Node 类型合流后的签名），`| null` 直接编译错。
  let barcodeWatchTimer: ReturnType<typeof setTimeout> | undefined
  let barcodeReqSeq = 0

  watch(barcodeInputValue, () => {
    clearTimeout(barcodeWatchTimer)
    barcodeWatchTimer = setTimeout(refreshBarcodePreview, 250)
  })

  async function refreshBarcodePreview() {
    barcodePreviewText.value = ''
    barcodeConflicts.value = []
    const parsed = parseBarcodeInput(barcodeInputValue.value)
    if (parsed.error) {
      if (parsed.error !== 'empty') barcodePreviewText.value = parsed.error
      return
    }
    if (!parsed.codes.length) return
    const seq = ++barcodeReqSeq             // "最新值获胜"：慢的旧响应不覆盖新预览
    try {
      const d = (await http.get('/holdings/preview', {
        params: { count: parsed.codes.length, start: parsed.codes[0],
                  // 未入库记录没有 id（临时副本的路由参数是字面量 "temp"）
                  record_id: recordIdOrNull.value ?? undefined,
                  library_id: currentLibId.value } })).data
      if (seq !== barcodeReqSeq) return     // 期间又有新输入，本次结果作废
      barcodeConflicts.value = (d.conflicts ?? []).filter((c: string) => parsed.codes.includes(c))
      barcodePreviewText.value = `将生成：${parsed.codes.join('  ')}`
    } catch { /* 预览失败静默 */ }
  }

  /** 换记录复位（RecordEdit.load() 的状态清空：清状态 + 取消挂起的去抖）。 */
  function resetBarcodePreview() {
    clearTimeout(barcodeWatchTimer)
    barcodePreviewText.value = ''
    barcodeConflicts.value = []
  }

  /** 组件卸载时清挂起的去抖（watch 本体随 setup 自动停止）。 */
  function dispose() {
    clearTimeout(barcodeWatchTimer)
    barcodeWatchTimer = undefined
  }

  return { barcodePreviewText, barcodeConflicts, refreshBarcodePreview, resetBarcodePreview, dispose }
}
