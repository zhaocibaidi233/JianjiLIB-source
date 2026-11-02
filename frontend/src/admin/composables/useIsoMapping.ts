/**
 * ISO 导入的字段映射：纯逻辑（无 API 调用、无 DOM）。
 *
 * ## 为什么单独抽出来
 *
 * `ImportExport.vue` 1,609 行里，"映射该怎么比、字段该排哪、下拉里写什么"这三件事
 * 全是纯函数，却和四步向导的状态混在一个文件里。抽出来之后：
 *
 * - 这些规则可以单独测（不挂载组件、不 mock 接口）；
 * - 视图只剩"把状态接上去"，改界面不用碰规则。
 *
 * ## 纪律
 *
 * **只搬不改**：函数体逐字搬过来，只把对 `isoPreview` 的隐式依赖改成显式参数。
 * 任何行为修改单独提交，否则出问题分不清是搬错还是改错。
 */

/** 两个映射表是否完全相同（键序无关）。 */
export function sameMapping(
  a: Record<string, string>,
  b: Record<string, string>,
): boolean {
  const ka = Object.keys(a).sort()
  const kb = Object.keys(b).sort()
  if (ka.length !== kb.length) return false
  return ka.every((k, i) => k === kb[i] && a[k] === b[k])
}

/**
 * 字段在"待调整"列表里的排序权重：已映射置顶。
 *
 * 用户要看的正是"系统把哪些字段自动搬到了哪里"，所以已映射的排最前；
 * 其余按"标准字段"（2）与"未识别字段"（1）分组，同组内按出现次数降序（调用方排）。
 */
export function fieldPriority(
  stat: { key: string; known: boolean },
  mapping: Record<string, string>,
): number {
  if (mapping[stat.key]) return 0
  return stat.known ? 2 : 1
}

/** 目标字段 key → 中文名（`010$a` → `ISBN`），给"原样保留（…）"的说明用。 */
export function targetFieldLabel(
  key: string,
  targetFields: { key: string; label?: string }[],
): string {
  const hit = targetFields.find((f) => f.key === key)
  return hit?.label || ''
}

/**
 * 「映射到」下拉里那个空值选项该写什么。
 *
 * 原来写的是「不导入」——**这是误导**：没被映射的字段并不会被丢掉，它原样留在
 * 记录里（映射只负责"把源搬到目标"，不负责删除）。标准字段更是本来就会被保留，
 * 所以直接把它自己的名字写出来，用户才看得见"标准字段映射到了哪"。
 */
export function keepAsIsLabel(
  stat: { key: string; known: boolean },
  targetFields: { key: string; label?: string }[],
): string {
  if (!stat.known) return '原样保留（不搬运）'
  const label = targetFieldLabel(stat.key, targetFields)
  return `原样保留（${stat.key}${label ? ' · ' + label : ''}）`
}

/** 后台任务类型 / 状态的中文名与色调（列表里要一眼看出是什么任务、跑成没跑成）。 */
export function jobTypeLabel(type: string): string {
  if (type === 'import-iso') return '机读目录导入'
  if (type === 'batch-cataloging') return '批量套录'
  return type
}

export function jobStatusLabel(status: string): string {
  if (status === 'pending') return '等待中'
  if (status === 'running') return '执行中'
  if (status === 'done') return '已完成'
  return '失败'
}

export function jobTone(status: string): string {
  if (status === 'done') return 'ok'
  if (status === 'error') return 'danger'
  if (status === 'running') return 'warn'
  return 'muted'
}

/** 任务结果里这些键要展示成中文标签（顺序即展示顺序）。 */
export const RESULT_LABELS: Record<string, string> = {
  imported: '导入',
  added: '新增',
  updated: '更新',
  skipped: '跳过',
  no_isbn: '无 ISBN',
  no_barcode: '无条码',
  record_count: '记录总数',
  error_count: '错误',
  with_quality_errors: '质检错误',
  total: '总数',
  to_resource: '入资源库',
  in_cataloging: '编目中',
  already_in_resource: '资源库已有',
  missed_count: '未命中',
}

/** 把任务结果摊成「标签 + 值」的行，只保留出现过的键。 */
export function resultRows(result: Record<string, unknown> | null) {
  if (!result) return []
  return Object.entries(RESULT_LABELS)
    .filter(([key]) => key in result)
    .map(([key, label]) => ({ label, value: result[key] }))
}
