import { ref } from 'vue'
import type { Ref } from 'vue'
import { http } from '../api'
import { barcodeDigitsAllowed, barcodeWidth, normalizeBarcodeInput } from '../barcodeInput'
import { ElMessage } from 'element-plus'

/**
 * 条码续号 / 解析 / 收集（RecordEdit 录入栏）的唯一实现。
 *
 * 三种输入形态：`+N`（本人基准号续 N 册）、`-N`（反向 N 册）、绝对条码号。
 * 位数校验对齐**全局设置**的允许位数（前后端同一套硬规则，后端是权威）。
 * 续号基准是**当前编目员**的最后一条条码（不是全库最大值——多人并行录入时
 * 各续各的号段，见 `/holdings/last-barcode` 的 scope=operator）。
 *
 * 之前这套逻辑长在 RecordEdit 里；`useBarcodePreview` 需要 `parseBarcodeInput`
 * 作为注入依赖，抽出后 RecordEdit 改为从这里取再传下去。
 */
export function useBarcodeSequencer(opts: {
  currentLibId: Ref<number | undefined>
  barcodeInputValue: Ref<string>
  /** 条码输入框（校验失败时把焦点拉回去） */
  barcodeInput: Ref<{ focus?: () => void } | null>
}) {
  const { currentLibId, barcodeInputValue, barcodeInput } = opts

  // 全局设置里**允许的条码位数**（可多个，如 [7,8]）：手工输入的短号按它左补零，
  // 且补零后的位数必须落在其中，否则不允许保存（后端有同一套硬校验）。
  const barcodeDigits = ref<number[]>([8])
  const lastBarcode = ref('')
  // **全库**最后条码：只在"高于本人续号基准"时才有值，供条码框下方提示"别撞号"。
  // 导入进来的记录没有编目员归属——只显示本人的号会让人以为库里就那么大。
  const barcodeLibraryLast = ref('')

  let lastNum: number | null = null
  let lastWidth = 8

  async function refreshLastBarcode() {
    try {
      // 默认 scope=operator：续号基准是**当前编目员**最后一条条码（不是全库最大值）——
      // 多个编目员并行录入时各续各的号段。后端在该编目员还没录过时回落到全库最大值，
      // 并用 `scope_used` 说明用了哪个。
      const d = (await http.get('/holdings/last-barcode',
                                { params: { library_id: currentLibId.value } })).data
      lastBarcode.value = d.last ?? ''
      lastNum = d.num ?? null
      const m = (d.last ?? '').match(/(\d+)$/)
      lastWidth = m ? m[1].length : 8
      // 全库最大值**高于**本人基准号时才提示（导入的记录没有编目员归属，很容易出现
      // "本人还在 00000003、库里已经用到 0025265" 这种情况，不提示就是让人往坑里走）。
      barcodeLibraryLast.value = (d.library_num != null && d.library_num > (d.num ?? -1)
                                  && d.library_last && d.library_last !== d.last)
        ? String(d.library_last) : ''
    } catch {
      lastBarcode.value = ''; lastNum = null; lastWidth = 8; barcodeLibraryLast.value = ''
    }
  }

  /**
   * 全局设置里只允许「设置的条码位数」，不符就不给保存。
   * 返回错误文案；全部合规返回 undefined。**前后端都有这道校验**（后端是权威）。
   */
  function digitsError(codes: string[]): string | undefined {
    const bad = codes.find(c => !barcodeDigitsAllowed(c, barcodeDigits.value))
    if (!bad) return undefined
    const allowed = barcodeDigits.value.join(' / ')
    return `条码 ${bad} 为 ${barcodeWidth(bad)} 位，不在设置的条码位数（${allowed} 位）内，不能保存。` +
           `如需使用，请先到「全局设置 → 条码位数」添加该位数。`
  }

  function parseBarcodeInput(input: string): { codes: string[]; error?: string } {
    const v = input.trim()
    if (!v) return { codes: [], error: 'empty' }
    // +N：生成 N 册（last+1 .. last+N）；-N：last-1 起 N 册递减
    const rel = v.match(/^([+-])(\d+)$/)
    if (rel) {
      if (lastNum === null) return { codes: [], error: '尚无历史条码，无法使用 +/-' }
      const n = Number(rel[2])
      const dir = rel[1] === '+' ? 1 : -1
      const codes = Array.from(
        { length: n }, (_, i) => String(lastNum! + dir * (i + 1)).padStart(lastWidth, '0'))
      // 续号沿用**上一条条码的宽度**——历史条码的宽度未必还在允许集合里，一并拦下
      const err = digitsError(codes)
      return err ? { codes: [], error: err } : { codes }
    }
    // 绝对条码（单册，支持前缀+数字）：按全局设置的位数左补零
    const code = normalizeBarcodeInput(v, barcodeDigits.value)
    if (code === null) return { codes: [], error: `条码无法识别：${v}` }
    const err = digitsError([code])
    return err ? { codes: [], error: err } : { codes: [code] }
  }

  // 图书库以条码号为核心：入库前必须能解析出至少一个条码。
  // 返回 null 表示校验未通过（已给出提示），调用方应中止保存。
  function collectBarcodes(): string[] | null {
    const parsed = parseBarcodeInput(barcodeInputValue.value)
    if (parsed.error && parsed.error !== 'empty') {
      ElMessage.error(parsed.error)          // 文案（识别失败 / 位数不符）由 parse 自带
      barcodeInput.value?.focus?.()
      return null
    }
    if (!parsed.codes.length) {
      ElMessage.warning('请先在下方「馆藏」处填写条码，再保存')
      barcodeInput.value?.focus?.()
      return null
    }
    return parsed.codes
  }

  return {
    barcodeDigits, lastBarcode, barcodeLibraryLast,
    refreshLastBarcode, parseBarcodeInput, collectBarcodes,
  }
}
