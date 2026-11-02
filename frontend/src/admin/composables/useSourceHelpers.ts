/**
 * 套录源与全局设置里的纯逻辑。
 *
 * ## 为什么单独抽出来
 *
 * `Settings.vue` 1,600 行里有相当一部分是**与组件状态无关**的规则：
 * 优先级怎么归三档、源类型怎么显示中文、连接串怎么拼、深拷贝怎么做。
 * 它们和 9 个分区的表单状态混在一个文件里，改界面时不敢动、想复用时拿不走。
 *
 * ## 纪律
 *
 * **只搬不改**：函数体逐字取自原文件。搬之前先读原实现——上一轮
 * （`ImportExport.vue`）我凭记忆重写 `jobTypeLabel` 等三个函数，文案与色调全错，
 * 这条教训适用于每一次搬迁。
 */

/** 套录源（与后端 `GET /api/sources` 的返回一致）。 */
export interface Source {
  id: number
  name: string
  type: string
  enabled: boolean
  config: Record<string, unknown>
  last_ok_at: string | null
  last_fail_at: string | null
}

/** 源类型的三档（顺序即下拉顺序）。 */
export const SOURCE_TYPES = [
  { key: 'local', label: '本地资源库' },
  { key: 'z3950', label: 'Z39.50' },
  { key: 'pg', label: '外部 PostgreSQL' },
]

/** 深拷贝（表单编辑用：改动草稿不能影响列表里的原对象）。 */
export function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T
}

/**
 * 优先级只给三档。
 *
 * 原来是一个 1~999 的数字框——数字大小对编目员没有意义，
 * 「高/中/低」才是他们实际要做的决策。存量里的手填值（1 / 100 / 999）
 * 按最近一档归一，不会出现空白。
 */
export function priorityLevel(raw: unknown): number {
  const n = Number(raw)
  if (!Number.isFinite(n)) return 20
  if (n <= 15) return 10
  if (n <= 25) return 20
  return 30
}

export function sourceTypeLabel(type: string): string {
  return SOURCE_TYPES.find((item) => item.key === type)?.label || type
}

/** 列表里显示的「连到哪」：本地库不显示地址，其余拼 host · database。 */
export function sourceEndpoint(row: Source): string {
  if (row.type === 'local') return '本地资源库'
  const host = String(row.config.host || '')
  const database = String(row.config.database || '')
  return `${host || '未填写主机'}${database ? ` · ${database}` : ''}`
}
