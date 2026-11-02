/**
 * 记录编辑页的路由参数语义。
 *
 * 路由 `/records/:id` 的 `id` 有三种含义，混用会出真 bug：
 *   - `'new'`  → 新建记录，尚未入库
 *   - `'temp'` → 套录/资源库载入的临时副本，尚未入库
 *   - 其它     → 已入库记录的数字 id
 *
 * 踩过的坑：把 `'temp'` 直接拼进 `/records/${id}/...` 会请求
 * `/records/temp/ai/optimize`，FastAPI 的 `record_id: int` 解析失败返回 422；
 * `Number('temp')` 得到的 NaN 同理。因此记录级请求必须先经此函数判定。
 */
export type RecordRouteKind = 'new' | 'temp' | 'saved'

export interface ParsedRecordRoute {
  kind: RecordRouteKind
  /** 已入库记录的数字 id；未入库（new/temp）或非法时为 null。 */
  recordId: number | null
}

export const RECORD_ROUTE_NEW = 'new'
export const RECORD_ROUTE_TEMP = 'temp'

export function parseRecordRoute(id: string | undefined | null): ParsedRecordRoute {
  if (id === RECORD_ROUTE_NEW) return { kind: 'new', recordId: null }
  if (id === RECORD_ROUTE_TEMP) return { kind: 'temp', recordId: null }
  const n = Number(id)
  // 非法/空 id 一律按"新建"处理：宁可走创建，也不要用 NaN 去 update
  if (!Number.isInteger(n) || n <= 0) return { kind: 'new', recordId: null }
  return { kind: 'saved', recordId: n }
}

/** 该路由下的记录是否尚未入库（新建或临时副本）。 */
export function isUnsaved(parsed: ParsedRecordRoute): boolean {
  return parsed.kind !== 'saved'
}
