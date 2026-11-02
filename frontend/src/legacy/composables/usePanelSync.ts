import { watch } from 'vue'
import type { Ref } from 'vue'
import type { MarcField } from '../api'
import { isPlainNumber } from '../priceExpr'
import { buildFromSimple as buildFromSimplePure, joinPrice,
         populateSimple as populateSimplePure, stripUnit } from '../simpleForm'

/** 简编与常驻侧栏共享投影；全字段保存只合并侧栏相对投影基线的实际修改。 */
export function usePanelSync(opts: {
  simple: Record<string, string>
  /** 允许算式的输入草稿，不是可直接落库的 010$d。 */
  priceDraft: Ref<string>
  currency: Ref<string>
  tab: Ref<string>
  fields: Ref<MarcField[]>
}) {
  const { simple, priceDraft, currency, tab, fields } = opts
  let projecting = false
  let classBaseline = simple['690a'] ?? ''
  let priceBaseline = simple['010d'] ?? ''
  let draftBaseline = priceDraft.value

  function resetPanelBaseline() {
    classBaseline = simple['690a'] ?? ''
    priceBaseline = simple['010d'] ?? ''
    draftBaseline = priceDraft.value
  }

  function populateSimple(fs: MarcField[]) {
    projecting = true
    try {
      populateSimplePure(fs, simple, { currency: currency.value })
      priceDraft.value = stripUnit(simple['010d'] ?? '', currency.value, 'prepend').trim()
      resetPanelBaseline()
    } finally { projecting = false }
  }

  const numericDraft = () => stripUnit(priceDraft.value ?? '', currency.value, 'prepend').trim()
  const committed = (price: string) => isPlainNumber(stripUnit(price, currency.value, 'prepend').trim())
    || /^[A-Za-z]{3}\s*\d+(?:\.\d+)?$/.test(price.trim())

  function buildFromSimple(): MarcField[] {
    // 简编价格栏也不能把未求值算式写进 MARC；保留原记录的定价。
    // 草稿正处于算式输入时，不能把此前逐键同步的数字前缀当成最终金额。
    const valid = committed(simple['010d'] ?? '') && committed(priceDraft.value)
    const state = valid ? simple : { ...simple, '010d': priceBaseline }
    return buildFromSimplePure(fields.value, state, { currency: currency.value })
  }

  watch(() => simple['010d'], v => {
    if (projecting) return
    const nv = stripUnit(v ?? '', currency.value, 'prepend').trim()
    if (nv !== priceDraft.value) priceDraft.value = nv
  }, { flush: 'sync' })
  watch(priceDraft, () => {
    if (projecting) return
    const nv = numericDraft()
    // 外币显示值不经过本馆币种重写；算式仅在 commitPrice 求值后同步。
    if (isPlainNumber(nv) && nv !== (simple['010d'] ?? '')) simple['010d'] = nv
  }, { flush: 'sync' })

  function applyPanelToFields(fs: MarcField[]): MarcField[] {
    const out = JSON.parse(JSON.stringify(fs)) as MarcField[]
    const setSf = (tag: string, code: string, value: string) => {
      const f = out.find(x => x.tag === tag)
      if (!f) {
        if (!value) return
        const index = out.findIndex(x => x.tag.localeCompare(tag) > 0)
        out.splice(index < 0 ? out.length : index, 0, { tag, i1: ' ', i2: ' ', sf: [[code, value]] })
        return
      }
      const sf = (f.sf ??= [])
      const index = sf.findIndex(s => s[0] === code)
      if (!value) {
        if (index >= 0) sf.splice(index, 1)
        if (!sf.length) out.splice(out.indexOf(f), 1)
      } else if (index >= 0) sf[index]![1] = value
      else sf.push([code, value])
    }
    // 基线未变：以 MarcPane 的修改/删除为准，绝不叠加旧投影。
    if ((simple['690a'] ?? '') !== classBaseline) setSf('690', 'a', (simple['690a'] ?? '').trim())
    const pn = numericDraft()
    if (priceDraft.value !== draftBaseline && isPlainNumber(pn)) {
      setSf('010', 'd', pn ? joinPrice(pn, currency.value) : '')
    }
    return out
  }

  /** 无副作用的载荷出口，脏状态比较和异步请求可使用同一份快照。 */
  function currentPayloadFields(): MarcField[] {
    return tab.value === 'simple' ? buildFromSimple() : applyPanelToFields(fields.value)
  }

  watch(tab, (to, from) => {
    const pendingDraft = committed(priceDraft.value) ? null : priceDraft.value
    if (from === 'simple') {
      fields.value = buildFromSimple()
      populateSimple(fields.value)
    } else if (to === 'simple') {
      fields.value = applyPanelToFields(fields.value)
      populateSimple(fields.value)
    }
    if (pendingDraft !== null) priceDraft.value = pendingDraft
  }, { flush: 'sync' })

  return { populateSimple, buildFromSimple, applyPanelToFields, currentPayloadFields }
}
