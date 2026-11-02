const SOURCE_LABELS: Record<string, string> = {
  demo: '演示数据',
  local: '本馆编目',
  z3950: '联机套录',
  resource: '资源库套录',
  iso: 'ISO 导入',
  excel: 'Excel 导入',
  import: '文件导入',
}

const HOLDING_STATUS_LABELS: Record<string, string> = {
  AVAILABLE: '在架',
  // 停流通 / 另库保存（契约 D14）：不是注销、也不是遗失 —— 仍是馆藏资产，
  // 进资产台账，但不进 905、不可借、不可转库。文案统一用「停流通」，
  // 只在需要区分「另库保存」的地方另加括号说明。
  SUSPENDED: '停流通',
  WITHDRAWN: '已注销',
  LOST: '已遗失',
}

const READER_STATUS_LABELS: Record<string, string> = {
  ACTIVE: '正常',
  SUSPENDED: '暂停借阅',
  INACTIVE: '已注销',
}

/** 来源代码转业务名称；自定义来源原样保留。 */
export function sourceLabel(value: string | null | undefined): string {
  const raw = (value ?? '').trim()
  if (!raw) return '—'
  return SOURCE_LABELS[raw.toLowerCase()] || raw
}

/** 馆藏状态转中文；未知状态不泄露英文代码。 */
export function holdingStatusLabel(status: string | null | undefined): string {
  const raw = (status ?? '').trim()
  if (!raw) return '未知状态'
  return HOLDING_STATUS_LABELS[raw] || '未知状态'
}

/**
 * 读者档案状态转中文。
 *
 * 这条规则原来散在两个页面里各写一遍三元表达式，而且**未知值的兜底正好相反**：
 * Readers 把不认识的状态显示成「已注销」，ReaderDetail 显示成「正常」——
 * 同一个后端值在两页能读出相反结论。收成一处后与馆藏/记录状态同口径：未知 → 未知状态。
 */
export function readerStatusLabel(status: string | null | undefined): string {
  const raw = (status ?? '').trim()
  if (!raw) return '未知状态'
  return READER_STATUS_LABELS[raw] || '未知状态'
}

const TRAILING_SHELF = /(?:^|\s)([A-Za-z]{1,4}-?\d{1,4})$/

/**
 * 馆藏地点展示值：地点 + 种级排架号。
 * 编辑、筛选与导出仍使用原始 location；这里只做读者/管理端展示。
 * 地点里已经带架位号（含与排架号冲突的旧数据）时不再重复追加。
 */
export function holdingLocationLabel(
  location: string | null | undefined,
  shelfNo: string | null | undefined,
): string {
  const loc = (location ?? '').trim().replace(/\s+/g, ' ')
  const shelf = (shelfNo ?? '').trim().replace(/\s+/g, ' ')
  if (!loc) return shelf || '—'
  if (!shelf) return loc
  if (loc.toLowerCase().endsWith(shelf.toLowerCase())) return loc
  const trailing = loc.match(TRAILING_SHELF)
  if (trailing && trailing[1].toLowerCase() !== shelf.toLowerCase()) return loc
  return `${loc} ${shelf}`
}
