// axios 实例、鉴权头、错误翻译**全部收敛到 `lib/api.ts`（唯一实现）**：
// 过去这里是第二套 axios（60s 超时、双鉴权头、独立错误翻译），与 lib 层的
// 超时、文案、401 处理互不一致。`http` 现在就是 lib 的那个实例——超时 60s、
// 双鉴权头、同一份 ApiError；已知慢接口的按请求覆盖（120s/180s）保持不变。
// 这带来一个**刻意的行为变化**：legacy 页面的 401 现在也会触发统一登出
// （`jianji:unauthorized` → 弹回登录页），与 lib 层页面一致——过去 legacy 页
// 遇到会话过期只会弹一条原始报错，用户得自己手动刷新。
import { ApiError, http } from '../lib/api'

export { http }

/**
 * 把异常翻译成能定位问题的中文提示（35+ 个调用面的兼容入口）。
 * 翻译本体在 `lib/api.ts` 的响应拦截器里（超时 / 代理空 5xx / 校验数组 /
 * 401 / 403 都已生成可读文案），这里只兜底：ApiError 直接用它的 detail，
 * 其它异常透出 message。
 */
export function apiErrorText(e: unknown, what = 'AI 调用'): string {
  if (e instanceof ApiError) return e.detail || `${what}失败`
  if (e instanceof Error) return e.message
  return `${what}失败`
}

export interface MarcField { tag: string; i1?: string; i2?: string; data?: string; sf?: [string, string][] }
export interface RecordPayload {
  leader?: string | null; fields: MarcField[]
  force?: boolean          // 质检 ERROR 时兜底强制入库（编目员确认后保留待修数据）
  library_id?: number
  // 图书库以条码号为核心：新建记录必须带条码，与记录同一事务写入
  barcodes?: string[]
  location?: string        // 随记录新建的馆藏的馆藏地点
  book_no?: string         // 手动书次号（索书号 "分类号/书次号" 的后半段）
  // 排架号与卷册号（**种级** · P0）。POST：不传 = 空；PUT：不传 = 不修改，'' = 清空。
  volume?: string
  volume_seq?: number | null
  shelf_no?: string
}
export interface Finding {
  rule_id: string; level: string; message: string; tag: string
  rule_name?: string   // 规则通俗名，如「出版年格式」（后端 Rule.name）
  hint?: string        // 面向非专业用户的说明与修复建议
  plain?: string       // 一句话的通俗问题（界面主行）；缺省时回退到 message
}
export interface RecordDetail {
  id: number; leader: string; fields: MarcField[]; source: string; status: string
  search: { isbn_norm?: string; title_norm?: string; title_pinyin_initials?: string; author_norm?: string; year?: number; class_no?: string }
  findings: Finding[]; mrk: string
}
export interface Candidate {
  source: string; key: string; title?: string; author?: string; publisher?: string
  year?: string; class_no?: string; isbn?: string
  record: { leader: string; fields: MarcField[] } | null
}

// ---------- 记录 ----------
export const listRecords = (params: any) => http.get('/records', { params }).then(r => r.data)
export const getRecord = (id: number) => http.get(`/records/${id}`).then(r => r.data)
export const createRecord = (payload: RecordPayload) => http.post('/records', payload).then(r => r.data)
export const updateRecord = (id: number, payload: any) => http.put(`/records/${id}`, payload).then(r => r.data)
export const deleteRecord = (id: number) => http.delete(`/records/${id}`).then(r => r.data)
/** 记录筛选条件：与后端 `/records` 的查询参数一一对应（列表与批量删除共用）。 */
export interface RecordFilter {
  library_id?: number | null
  q?: string
  isbn?: string
  title?: string
  author?: string
  barcode?: string
  created_by?: string
  source?: string
  class_no?: string
  no_barcode?: boolean
  active_only?: boolean
  created_from?: string
  created_to?: string
}

/**
 * 批量删除记录。两种用法：
 *   - `{ ids }`：勾选删除；
 *   - `{ filter, expected_total }`：按筛选条件**整批删**（清掉一次错误导入的整批数据）。
 *     后端会拿 `expected_total` 与自身匹配数比对，不一致直接 409 拒绝（防误删）。
 */
export const batchDeleteRecords = (
  payload: { ids: number[] } | { filter: RecordFilter; expected_total: number },
) => http.post('/records/batch-delete', payload).then(r => r.data)

/** 记录筛选项候选值（来源 / 编目员）。 */
export const recordFacets = (libraryId?: number | null) =>
  http.get('/records/facets', { params: { library_id: libraryId ?? undefined } })
    .then(r => r.data as { sources: string[]; operators: string[] })
export const dedup = (params: any) => http.get('/dedup', { params }).then(r => r.data)

// 反向提取最大书次号（对标参考系统「提取清单最大号」）：
// 从**粘贴的索书号列表**或**上传的清单**里按分类号取最大书次号，抬高该图书库的
// 书次号计数器。用途是"从别的系统接手一批数据"——库里已经用到 300 号，
// 我们的计数器还是 0，下一本自动取号就从 1 开始、撞上历史号。
// `dryRun=true` 只预览（每个分类号"现在几 / 会被抬到几"），确认了再落；
// 抬号**只升不降**，重复点不会把号改小。
export interface BookNoExtractItem {
  class_no: string; max_no: number; count: number; sample: string
  current: number; raised: boolean
}
export interface BookNoExtractResult {
  library_id: number; library: string; dry_run: boolean; applied: number
  items: BookNoExtractItem[]
  skipped: { line: string; reason: string }[]
  skipped_count: number; line_total: number; usable: number; truncated: boolean
  sheet?: string | null; sheets?: string[]; read_rows?: number
}
// ---------- 导出格式预设（909 / 985 / 905…） ----------
// 导出时把我方字段**补写**一份到下游厂家认的字段上（源字段保留，不搬运）。
// ⚠️ 不传 `preset_id` = 原样导出标准 CNMARC —— 不套"默认预设"，免得给某一套设了默认
// 就悄悄改掉所有导出方。
/** 导出格式预设。
 *  - `mapping`：`我们的字段$子字段` → 下游字段（**可给数组**：一个源补写到多个目标，
 *    如条码同时进 `909$a`/`909$b`/`985$a`/`985$h`）。后端归一后**一律是数组**，
 *    但兼容只写字符串的旧格式。
 *  - `const`：下游要、我们没有的子字段，填死值（如 `982$a` = 学生用书）。
 *    目标 tag 没有任何源时会**新建一条**只装常量的字段。 */
export interface ExportPreset {
  id: string
  name: string
  mapping: Record<string, string | string[]>
  const?: Record<string, string>
}
export interface ExportPresetsValue { presets: ExportPreset[]; default_id: string }
export interface ExportPresetsPayload {
  value: ExportPresetsValue
  /** 可作映射**源**的我方字段（后端 `io/iso.py::target_field_options()`，唯一真源）。 */
  source_fields?: { key: string; tag: string; code: string; label: string }[]
}
// ---------- 套录 ----------
// 只保留 catalogSearch：编目页「套录」框用它。
// 入参 `q` 是通用检索词（ISBN / 题名 / 责任者），由后端判断并按需回退；
// 也仍接受 isbn / title / author 三个精确参数。
// 原 catalogPrepare / genCallNo 封装已随独立套录页一并移除——
// prepare 仍被编目页与资源库页直接调用（http.post('/cataloging/prepare')）。
//
// 检索顺序由后端分层短路：**图书库 → 资源库 → Z39.50**（前一层命中就不查下一层）。
// 返回的候选里 `record_id` 非空 = 命中的是**已入库记录**，前端应打开该条目编辑，
// 而不是再建一条重复记录。`library_id` 用来把图书库这一层限定在当前书库。
export const catalogSearch = (body: any) =>
  http.post('/cataloging/search', body, { timeout: 120000 }).then(r => r.data)

/**
 * 快速录入：**ISBN 一次请求**完成 查重 → 分级套录 → 联机源回存资源库 → 临时副本。
 *
 * 与 `catalogSearch` 的分工：那个是"多源检索 + 候选弹窗，题名/责任者也能搜"，
 * 这个是"只认 ISBN、命中即用、miss 就明说"的直取通道（后端 `api/quick.py`）。
 *
 * 返回三态：
 *   - `{status:'exists', record_id, detail}` —— 图书库已有（同书异校不算重）
 *   - `{status:'miss',   hint}`             —— 全部源未命中，转手工编目
 *   - `{status:'ok', record, hit_source, auto_applied}` —— 可编目的临时副本
 *
 * ⚠️ `status==='ok'` 时后端**已经跑过** `localize_record`（重编 001、801 改写、
 * 9xx 剥离、分类号截取），所以结果**不能再送** `/cataloging/prepare`：
 * 那会重编一次 001 并再塞一条 035，把数据改坏（localize 不幂等）。
 */
export const quickEntry = (isbn: string, libraryId?: number | null) =>
  http.post('/quick-entry', { isbn, agency: 'LOCAL', library_id: libraryId ?? undefined },
    { timeout: 120000 }).then(r => r.data)
// 注：分类推荐 / 主题词标引原走大模型（/records/{id}/ai/classify|subjects），
// 2026-10-07 从前端移除（改为纯规则工具）；2026-10-08 后端接口与 app/ai/provider.py
// 也一并清理（前端零调用）。要恢复见 backend/app/api/ai.py 顶部说明。

// ---------- 馆藏 ----------
export const addHoldings = (recordId: number, copies: number, location?: string,
                            barcodeStart?: string, barcodes?: string[]) =>
  http.post(`/records/${recordId}/holdings`, {
    copies, location, barcode_start: barcodeStart || null, barcodes: barcodes ?? null,
  }).then(r => r.data)
export const listHoldings = (recordId: number) =>
  http.get(`/records/${recordId}/holdings`).then(r => r.data)

// 条码管理（全馆视角，跨记录）
export const searchHoldings = (params: Record<string, any>) =>
  http.get('/holdings', { params }).then(r => r.data)
export const holdingLocations = (libraryId?: number) =>
  http.get('/holdings/locations', { params: { library_id: libraryId } }).then(r => r.data)
export const updateHolding = (id: number, payload: { barcode?: string; location?: string }) =>
  http.put(`/holdings/${id}`, payload).then(r => r.data)
export const deleteHolding = (id: number) =>
  http.delete(`/holdings/${id}`).then(r => r.data)
export const batchDeleteHoldings = (ids: number[]) =>
  http.post('/holdings/batch-delete', { ids }).then(r => r.data)
export const batchUpdateLocation = (ids: number[], location: string | null) =>
  http.post('/holdings/batch-location', { ids, location }).then(r => r.data)

// 注销 / 恢复（对标老系统「图书注销/图书恢复」）
// 批量操作逐条处理：返回 {succeeded, failed}，一条不合法不影响其余条。
export const withdrawHoldings = (ids: number[], reason: string, withdrawNo?: string) =>
  http.post('/holdings/withdraw',
            { ids, reason, withdraw_no: withdrawNo || null }).then(r => r.data)
export const restoreHoldings = (ids: number[]) =>
  http.post('/holdings/restore', { ids }).then(r => r.data)

// 停流通 / 另库保存（契约 D14）：**不是注销** —— 该册仍是馆藏资产（进资产台账），
// 只是不进 905、不可借、不可转库。与注销/恢复同一形状：逐条处理，
// 返回 {succeeded, failed}，失败原因（如"外借中不能停流通"）在 failed[].reason 里。
export const suspendHoldings = (
  ids: number[],
  payload: { reason: string; review_batch_no?: string; approval_ref?: string; destination?: string },
) => http.post('/holdings/suspend', { ids, ...payload }).then(r => r.data)
export const releaseHoldings = (ids: number[]) =>
  http.post('/holdings/release', { ids }).then(r => r.data)

export const listWithdrawn = (params: Record<string, any>) =>
  http.get('/holdings/withdrawn', { params }).then(r => r.data)

// ---------- 字典与反查（老系统的参考数据，随包发布） ----------
// ISBN 出版者前缀 -> 出版者 + 出版地（1392 条）。定位是**预填建议**不是校验：
// 前缀表不完整，也可能被更短的出版者代码抢中，所以调用方不能静默覆盖已有值。
export const lookupPublisherByIsbn = (isbn: string) =>
  http.get('/lookup/publisher', { params: { isbn } }).then(r => r.data)
export const lookupPlaces = (limit = 100) =>
  http.get('/lookup/places', { params: { limit } }).then(r => r.data)
export const lookupPlaceByPublisher = (publisher: string) =>
  http.get('/lookup/place', { params: { publisher } }).then(r => r.data)
export const lookupLanguages = (q?: string, limit = 50) =>
  http.get('/lookup/languages', { params: { q: q || undefined, limit } }).then(r => r.data)
// ---------- 统一字典中心 ----------
// 老系统「系统字典」的 7 类：馆藏地点 / 货币类型 / 文献类型 / 获得方式 /
// 出版物语种 / 付款方式 / 责任方式。内置默认随包带（只读），用户增删存 settings。
// 条目形态由 kind 决定：simple = 字符串；coded = { code, name }（值是码、显示是名）。
export type DictKind = 'simple' | 'coded'
export interface DictItem { code?: string; name?: string }
export interface DictTypeMeta {
  key: string
  label: string
  kind: DictKind
  items: Array<string | DictItem>
  count: number
  builtin_count: number
  customized: boolean
}
export const getDictionary = (key: string) =>
  http.get(`/dictionaries/${key}`).then(r => r.data as DictTypeMeta)
// 「本馆默认币种」= 定价 `010$d` 的货币码（编目页定价框的前缀、码洋统计的口径）。
// 路径是**两段**的 `/currencies/default`：单段的 `/default-currency` 会被 `/{key}` 接走
// （后端刻意避开了那个路由顺序坑）。
export interface DefaultCurrency { code: string; name: string }
export const getDefaultCurrency = () =>
  http.get('/dictionaries/currencies/default').then(r => r.data as DefaultCurrency)
// ---------- CNMARC 字段字典（2026-10-08 起字典驱动） ----------
// 字段号 → 中文名 / 子字段说明（110 个字段，含"必备/控制字段"标记）。
// 由 `scripts/import_marc_fields.py` 从老系统字段字典文件转出，随包发布、只读。
// 用途：把「全字段 MARC」里的字段号翻译成中文名 —— **纯展示**，不参与保存逻辑
// （保存仍以 fields 为准）。
export interface MarcSubfieldDef { code: string; name: string }
export interface MarcFieldDef {
  tag: string
  name: string
  required: boolean
  control: boolean
  /** 该条是否在生成时被修正过（XML 抄错/@误写等），见 scripts/import_marc_fields.py */
  amended: boolean
  subfields: MarcSubfieldDef[]
}
export interface MarcFieldsPayload { count: number; note: string; fields: MarcFieldDef[] }
export const getMarcFields = () =>
  http.get('/marc-fields').then(r => r.data as MarcFieldsPayload)

// ---------- 图书库 ----------
export const listBookLibraries = () => http.get('/book-libraries').then(r => r.data)

// 排架号开关（**按图书库**）。语义是「这个库要不要在界面上看到排架号」，
// 不是「数据里有没有排架号」：关闭时后端仍收 shelf_no、905 $c 仍输出历史值。
export interface ShelfNoSetting { library_id: number; enabled: boolean }
export const getShelfNoSetting = (libraryId?: number | null) =>
  http.get('/book-libraries/settings/shelf-no',
           { params: { library_id: libraryId ?? undefined } }).then(r => r.data as ShelfNoSetting)
export const putShelfNoSetting = (libraryId: number | null, enabled: boolean) =>
  http.put('/book-libraries/settings/shelf-no',
           { library_id: libraryId, enabled }).then(r => r.data)

export const saveSource = (payload: any, id?: number) =>
  id ? http.put(`/sources/${id}`, payload).then(r => r.data) : http.post('/sources', payload).then(r => r.data)
export const deleteSource = (id: number) => http.delete(`/sources/${id}`).then(r => r.data)
// 资源库记录的筛选 / 批量删除 / 导出
export interface ResourceFilter {
  q?: string
  source_tag?: string
  has_isbn?: boolean | null
  class_no?: string
  year?: number | null
}
// ---------- 用户与角色 ----------
// 登录状态本身在 src/auth.ts（唯一状态源）；这里只放用户管理页要用的 CRUD。
// 权限档位 `none|read|write` 由后端 `core/perm.py` 定义，前端不做二次解释。
export interface UserRow {
  id: number
  username: string
  display_name: string
  enabled: boolean
  role_id: number
  role_name: string
  role_title: string
  permissions: Record<string, string>
  levels: Record<string, string>
  is_admin: boolean
  last_login_at?: string | null
  created_at?: string
}
export interface RoleRow {
  id: number
  name: string
  title: string
  permissions: Record<string, string>
  levels: Record<string, string>
  builtin: boolean
  note?: string | null
  user_count?: number
}

export const deleteUser = (id: number) => http.delete(`/users/${id}`).then(r => r.data)
/** 踢下线但保留账号（与「停用」的区别：停用后本人也登不回来）。 */
export const kickUser = (id: number) =>
  http.post(`/users/${id}/kick`).then(r => r.data as { ok: boolean; removed: number })

export const deleteRole = (id: number) => http.delete(`/roles/${id}`).then(r => r.data)

// ---------- 注册申请（的延伸：自助注册 + 管理员审批） ----------
// 申请单**不是账号**：批准那一刻后端才建 `users` 行（见 services/registration.py）。
// 所以这里的 approve 返回的是新建好的 UserRow，而不是"申请单变成已批准"那么简单。
export type RegistrationStatus = 'pending' | 'approved' | 'rejected'
export interface RegistrationRow {
  id: number
  username: string
  note?: string | null
  status: RegistrationStatus
  reject_reason?: string | null
  reviewed_by?: string | null
  reviewed_at?: string | null
  created_at: string
}
// ---------- 财产登记表 ----------
// 按**册**出账（一册一行），与统计页的"按种汇总"是两本不同的账。
export interface PropertyRow {
  barcode: string; call_no: string; class_no: string; title: string; author: string
  publisher: string; year: string; pages: string; binding: string; price: number
  isbn: string; volume: string; location: string; source: string; status: string
  registered_at: string; withdraw_no: string; withdraw_reason: string
}
export interface PropertyFilter {
  library_id?: number | null; barcode_from?: string; barcode_to?: string
  date_from?: string; date_to?: string; location?: string; class_no?: string
  status?: string; q?: string; sort?: string
}
// ---------- 图书转库 ----------
// 两步：preview（只读，列出换号与冲突）→ apply（整批一个事务，有冲突整批拒绝）。
export interface TransferPlan {
  record_id: number; title: string; isbn: string
  target: { mode: 'reuse' | 'new'; record_id: number | null }
  copies: { holding_id: number; old_barcode: string; new_barcode: string
            old_call_no: string; new_call_no: string; status: string }[]
  blocked: { holding_id: number; barcode: string; status: string }[]
  drop_source: boolean
}
export interface TransferPreview {
  from_library: { id: number; name: string }; to_library: { id: number; name: string }
  barcode_mode: string; callno_mode: string
  records: number; copies: number
  plans: TransferPlan[]
  conflicts: { kind: string; value: string; record_id: number; title: string; holding_id: number }[]
  skipped: { record_id: number; title: string; reason: string }[]
  missing: number[]
}
export interface TransferPayload {
  from_library_id: number; to_library_id: number
  ids?: number[]; filter?: Record<string, any> | null; expected_total?: number | null
  barcode_mode?: 'keep' | 'renumber'; callno_mode?: 'keep' | 'renumber'
  include_inactive?: boolean; keep_shelf_no?: boolean
}
export const transferPreview = (body: TransferPayload) =>
  http.post('/transfers/preview', body, { timeout: 120000 }).then(r => r.data as TransferPreview)
export const transferApply = (body: TransferPayload) =>
  http.post('/transfers/apply', body, { timeout: 180000 }).then(r => r.data as {
    batch_no: string; moved_copies: number; moved_records: number
    created_records: number; deleted_source_records: number
    skipped: { record_id: number; title: string; reason: string }[]
    missing: number[]; conflicts: number
  })
/** 转库台账：按册记录「从哪来、到哪去、原来什么号」。 */
export const listTransfers = (params: Record<string, any>) =>
  http.get('/transfers', { params }).then(r => r.data as {
    total: number; limit: number; offset: number
    items: {
      id: number; batch_no: string
      from_library: string | null; to_library: string | null
      from_library_id: number; to_library_id: number
      record_id: number; to_record_id: number | null; holding_id: number
      old_barcode: string; new_barcode: string
      old_call_no: string; new_call_no: string
      title: string; operator: string | null; created_at: string | null
    }[]
  })

// ---------- 接口凭据与调用记录 ----------
// 外部程序（MCP 客户端 / 脚本）用的长期令牌。**权限来自它绑的角色**，不额外授权。
export interface AgentTokenRow {
  id: number; name: string; prefix: string
  role_id: number; role_name: string | null; role_title: string | null
  levels: Record<string, string>
  created_by: string | null; note: string | null
  created_at: string | null; last_used_at: string | null; revoked_at: string | null
  active: boolean
  /** 仅**建凭据这一次**返回；列表里永远不带（事后取不回来）。 */
  token?: string
}
export interface AgentAuditRow {
  id: number; token_id: number; token_name: string
  method: string; path: string; status: number
  duration_ms: number; created_at: string | null
}
// ---------- 操作日志 ----------
// 「谁在何时改了哪条记录」。与 agent 审计分工不同：那张记**请求**
// （含只读、含被拦下的），这张只记**数据真的变了** —— 只读不记、业务回滚也不记。
export interface RecordLogRow {
  id: number
  action: string; action_label: string
  operator: string
  library_id: number | null; record_id: number | null
  title: string; barcode: string; copies: number
  note: string; created_at: string | null
}
export interface RecordLogFilter {
  record_id?: number; library_id?: number; operator?: string; action?: string
  q?: string; date_from?: string; date_to?: string
}
/** 排序参数（服务端排）：`sort` 取后端白名单里的列键，`order` = `asc`/`desc`。
 *  所有"点击表头排序"的列表共用这一对；空 = 用后端默认顺序。 */
export interface ListSort {
  sort?: string
  order?: 'asc' | 'desc'
}
// ---------- 读者与流通 ----------
// 外借状态只由 loans 的 ACTIVE 行表达；holdings.status 仍是物理处置状态。
export interface ReaderRow {
  id: number; library_id: number; card_no: string; name: string
  reader_type: string; reader_type_label: string
  grade: string | null; class_name: string | null; phone: string | null
  status: string; status_label: string
  note: string | null; created_at: string | null; updated_at: string | null
  active_count?: number; overdue_count?: number
}
export interface ReaderRules {
  max_items: number; loan_days: number; max_renew: number
}
export interface LoanRow {
  id: number; library_id: number; holding_id: number; record_id: number
  barcode: string | null; title: string | null
  reader_id: number; reader_card_no: string | null; reader_name: string | null
  reader_class: string | null
  loaned_at: string | null; due_on: string | null; returned_at: string | null
  renew_count: number; last_renewed_at: string | null
  operator_loan: string | null; operator_return: string | null
  status: string; status_label: string; note: string | null
  overdue: boolean; days_overdue: number
  days_overdue_at_return: number
  fine_amount: number
  fine_status: string | null
  fine_status_label: string | null
  fine_settled_at: string | null
  fine_operator: string | null
  lost_at: string | null
  lost_operator: string | null
  lost_note: string | null
  compensation_amount: number
  compensation_status: string | null
  compensation_status_label: string | null
  compensation_settled_at: string | null
  compensation_operator: string | null
}
/** 罚款在归还时定格；`enabled=false` 时金额一律 0，界面整块隐藏。 */
export interface ReaderFines {
  enabled: boolean; unpaid: number; unpaid_count: number
  overdue_estimate: number
}
/** 丢书赔偿：独立于罚款开关，罚款关掉时赔偿仍可见。 */
export interface ReaderCompensations {
  lost_count: number; unpaid: number; unpaid_count: number
}
export interface ReaderPanel extends ReaderRow {
  rules: ReaderRules
  active_count: number; overdue_count: number
  active_loans: LoanRow[]
  fines: ReaderFines
  compensations: ReaderCompensations
}
export interface ReaderDetail extends ReaderPanel {
  stats: { total_loans: number; active: number; overdue: number }
  history: LoanRow[]
}
export interface HoldingPanel {
  holding_id: number; record_id: number; library_id: number
  barcode: string | null; call_no: string | null; location: string | null
  status: string; title: string | null; isbn: string | null
  withdraw_no: string | null; withdraw_reason: string | null
  withdrawn_at: string | null
  on_loan: boolean; loan: LoanRow | null
}
export interface ReaderFilter {
  library_id?: number; q?: string; reader_type?: string; status?: string
  grade?: string; class_name?: string
}
export interface ReaderFacets {
  grades: string[]; classes: string[]
  types: { value: string; label: string; count: number }[]
  statuses: Record<string, number>
  total: number
}
export interface LoanFilter {
  library_id?: number; status?: string; q?: string; reader_id?: number
  date_from?: string; date_to?: string
}

export interface FineListFilter {
  library_id?: number
  status?: 'unpaid' | 'paid' | 'waived' | 'all'
  q?: string
}
export interface FineSummaryStats {
  unpaid_count: number; unpaid_amount: number; collected_today: number
}
export interface CirculationStats {
  summary: {
    today_borrowed: number; today_returned: number
    active: number; overdue: number; readers_active: number
    lost?: number
    compensation_unpaid?: number; compensation_unpaid_count?: number
    fines_today?: number; fines_unpaid?: number; fines_unpaid_count?: number
  }
  by_day: { date: string; borrowed: number; returned: number }[]
  top_titles: { title: string; count: number }[]
}
export interface ReaderTypeRule {
  label: string; loan_days: number; max_items: number
  max_renew: number; renew_days: number
}
export interface FineRules {
  enabled: boolean; per_day: number; grace_days: number; max_amount: number
}
export interface CirculationRules {
  types: Record<string, ReaderTypeRule>
  skip_weekends: boolean
  closed_dates: string[]
  block_on_overdue: boolean
  fines: FineRules
}
export interface OverdueNoticeItem {
  barcode: string | null; title: string | null
  due_on: string; days_overdue: number; renew_count: number
}
export interface OverdueNoticeReader {
  reader_id: number; card_no: string | null; name: string | null
  grade: string | null; class_name: string | null; phone: string | null
  count: number; max_days_overdue: number; items: OverdueNoticeItem[]
}
export interface OverdueNotices {
  library_id: number; library_name: string; today: string; generated_at: string
  total_readers: number; total_loans: number; readers: OverdueNoticeReader[]
}
// ---------- 盘点 ----------
export interface InventoryStats {
  expected: number; found: number; missing: number; on_loan: number
  unknown: number; other_library: number; withdrawn: number
  on_loan_scanned: number; duplicate: number; problems: number
  scans: number; progress: number
}
export interface InventorySession {
  id: number; library_id: number; name: string
  status: 'OPEN' | 'CLOSED'; status_label: string
  expected_count: number
  started_at: string | null; started_by: string | null
  closed_at: string | null; closed_by: string | null
  note: string | null; stats: InventoryStats
}
export interface InventoryScan {
  id: number; barcode: string; result: string; result_label: string
  holding_id: number | null; title: string | null
  call_no: string | null; location: string | null
  reader_name: string | null
  scanned_at: string | null; scanned_by: string | null
  message: string
}
export interface InventorySessionDetail extends InventorySession {
  recent: InventoryScan[]
}
export interface InventoryMissingRow {
  holding_id: number; record_id: number; barcode: string | null
  title: string | null; call_no: string | null; location: string | null
  status: string
}
export interface InventoryOnLoanRow {
  loan_id: number; holding_id: number; record_id: number
  barcode: string | null; title: string | null
  reader_id: number; reader_name: string | null
  reader_card_no: string | null; reader_class: string | null
  due_on: string; days_overdue: number
}
// ---------- 图书推荐 ----------
export interface RecommendationCard {
  record_id: number
  recommendation_id?: number
  title: string | null
  author: string | null
  publisher: string | null
  year: number | null
  class_no: string | null
  call_no: string | null
  isbn: string | null
  active_count: number
  available_count: number
  note?: string | null
  sort_order?: number
  loan_count?: number
  created_at?: string | null
}
export interface RecommendationsData {
  library_id: number
  library_name: string
  curated: RecommendationCard[]
  popular: RecommendationCard[]
  new_arrivals: RecommendationCard[]
}
