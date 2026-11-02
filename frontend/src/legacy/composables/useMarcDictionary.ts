import { ref } from 'vue'
import { getMarcFields } from '../api'

/**
 * CNMARC 字段字典（字段号 → 中文名），只为**展示**服务。
 *
 * 「全字段 MARC」里原本只有 `200`/`905` 这样的字段号，编目员得自己记；现在把字典里的
 * 中文名挂在旁边（子字段名做输入框占位），不用再去翻手册。
 *
 * **纯展示、不参与保存**（保存仍以 `fields` 为准），且**取不到就静默降级**成一个灰色
 * 字段号 —— 与 loadCurrency / 排架号开关同款：辅助信息不能打断编目。
 * 字典是随包静态数据，**整个实例只取一次**（失败也不重试，重开页面即可）。
 */
export function useMarcDictionary() {
  const marcFieldNames = ref<Record<string, string>>({})
  const marcSubNames = ref<Record<string, string>>({})   // 键 = `tag+code`（如 '200a'）
  let marcDictLoaded = false

  async function loadMarcFields() {
    if (marcDictLoaded) return
    marcDictLoaded = true
    try {
      const d = await getMarcFields()
      const fm: Record<string, string> = {}
      const sm: Record<string, string> = {}
      for (const f of d.fields ?? []) {
        if (f.name) fm[f.tag] = f.name
        for (const sf of f.subfields ?? []) {
          if (sf.name) sm[`${f.tag}${sf.code}`] = sf.name
        }
      }
      marcFieldNames.value = fm
      marcSubNames.value = sm
    } catch { /* 字典拿不到只是少个中文名，不能挡住编目 */ }
  }

  /** 字段中文名（查不到返回空串，模板据此不渲染那个灰字）。 */
  const fieldName = (tag: string) => marcFieldNames.value[tag] || ''
  /** 子字段中文名（给「内容」输入框做占位；查不到返回空串，模板退回默认占位）。 */
  const subfieldName = (tag: string, code: string) =>
    marcSubNames.value[`${tag}${code}`] || ''

  return { loadMarcFields, fieldName, subfieldName }
}
