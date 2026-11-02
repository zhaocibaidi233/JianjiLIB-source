/**
 * 卷册号 / 排架号的**前端纯逻辑**（无 IO、可单测）—— · P0。
 *
 * 与 `src/simpleForm.ts` / `src/barcodeInput.ts` 同类：只放字符串规则与常量，
 * 便于 vitest 直接覆盖，也让后端规则（backend/app/core/volume.py）有一份对称实现。
 *
 * 两条铁律：
 *   1. 候选清单**只做快捷输入，绝不限制自由录入**——`el-select filterable
 *      allow-create` + 手输都能保存（US-3）；
 *   2. 排架号**不自动生成、不校验唯一性、不参与索书号拼接**；除"新记录预填本图书库
 *      上次保存过的值"这一个例外（2026-10-06 用户裁定，见 修订段）之外，
 *      任何时候都别"好心"给默认值、别替用户猜格式。
 */

export interface VolumeOption {
  value: string
  /** 排序号；解析不出（如「上册」）为 null —— 为空时排序回退按文本序 */
  seq: number | null
}

/** 中文数字（卷/册用），与阿拉伯数字候选共用同一套 seq。 */
const CN_NUM = ['一', '二', '三', '四', '五', '六', '七', '八', '九', '十'] as const

/** 纯数字 1–30：seq 就是它自己。 */
const NUM_OPTIONS: VolumeOption[] =
  Array.from({ length: 30 }, (_, i) => ({ value: String(i + 1), seq: i + 1 }))

/** 括号数字 (1)–(10)：seq 取括号内的数。 */
const PAREN_OPTIONS: VolumeOption[] =
  Array.from({ length: 10 }, (_, i) => ({ value: `(${i + 1})`, seq: i + 1 }))

/** 第N卷 / 第N册（一~十）。 */
const CN_VOLUME_OPTIONS: VolumeOption[] = CN_NUM.flatMap((cn, i) => ([
  { value: `第${cn}卷`, seq: i + 1 } as VolumeOption,
  { value: `第${cn}册`, seq: i + 1 } as VolumeOption,
]))

/**
 * 内置候选清单（PRD §4.3）。
 *
 * P1-A 起可由 `settings` 表维护，届时读取顺序改为「远程字典 ∪ 内置常量」，
 * 调用方（只认 `VolumeOption[]`）不变。
 */
export const VOLUME_OPTIONS: readonly VolumeOption[] = [
  // 上中下（册）
  { value: '上册', seq: null }, { value: '中册', seq: null }, { value: '下册', seq: null },
  // 上中下（卷）
  { value: '上卷', seq: null }, { value: '中卷', seq: null }, { value: '下卷', seq: null },
  // 第一卷~第十卷 / 第一册~第十册
  ...CN_VOLUME_OPTIONS,
  // 纯数字 1-30
  ...NUM_OPTIONS,
  // 括号数字 (1)-(10)
  ...PAREN_OPTIONS,
  // 合订本示例（PRD §4.2：一本含上中下三册，直接写在一起，不拆记录）
  { value: '上册/中册/下册', seq: null },
]

/**
 * 排架号软建议长度（超过只提示，不阻断保存；硬上限 64 由后端 400 兜）。
 */
export const SHELF_NO_SOFT_MAX_LEN = 32

/**
 * 卷册号分隔符 —— 必须与后端 `backend/app/core/volume.py` 的 `VOLUME_SEP` 一致。
 *
 * 用 `:` 而不是 `/`：`/` 固定用于分隔分类号与书次号，且已被「附加区分号」
 * 占用（同一责任者同一分类号的不同著作，如 `I210.4/L824/2`）。
 */
export const VOLUME_SEP = ':'

/**
 * 剥掉卷册号后缀，返回基号 `分类号/书次号`（后端 `call_no_base` 的对称实现）。
 *
 * 用**后缀精确匹配**而非 `split('/')`：合订本卷册号自带斜杠
 * （`上册/中册/下册`），按分隔符切会切错。匹配不上就原样返回。
 */
export function callNoBase(callNo: string | undefined | null,
                           volume: string | null | undefined): string {
  const s = (callNo ?? '').trim()
  const v = (volume ?? '').trim()
  if (!s || !v) return s
  const suffix = VOLUME_SEP + v
  // 长度必须严格大于后缀，否则基号会被剥成空串
  return s.length > suffix.length && s.endsWith(suffix) ? s.slice(0, -suffix.length) : s
}

/**
 * 从索书号里取出**书次号**（含附加区分号）。
 *
 * 索书号 = `分类号/书次号[:卷册号]`。做法：取**第一个 `/` 之后的全部内容**，
 * 再从第一个 `VOLUME_SEP` 处截断。三点理由：
 *   1. 卷册号自身可能含 `/`（合订本 `上册/中册/下册`）——割完 `:` 再切 `/` 才对；
 *   2. **附加区分号**（同责任者同分类号的不同著作，用 `/` 加序号，如
 *      `I210.4/L824/2`）必须原样保留。早期写法 `split('/')[1]` 只取第二段，
 *      会把 `/2` 丢掉，界面一保存就变成 `I210.4/L824` —— 静默数据丢失；
 *   3. 分类号自身不含 `/`，所以"第一个 `/` 之后"就是书次号（+附加区分号+卷册号）。
 */
export function bookNoFromCallNo(callNo: string | undefined | null): string {
  const parts = (callNo ?? '').split('/')
  if (parts.length < 2) return ''
  return parts.slice(1).join('/').split(VOLUME_SEP)[0]
}

/**
 * 拼索书号 `分类号/书次号[:卷册号]` —— 后端 `core/callno.make_call_no()` 的对称实现。
 *
 * ⚠️ **暂无调用方，保留为后端规则的对称实现**（以及 volume.test.ts 的回归锚点）：
 * 编目页右下角书标预览移除后，生产代码里已无调用；但它是后端规则的对称实现、有单测锁定，
 * **不要删**。恢复"前端正向拼接索书号"的入口时直接接回。
 *
 * 规则唯一真源在后端；本条与 `callNoBase()` 是**同一约定**（前后端对称实现，改动必须两边同步）。
 *   - 分类号为空 → 返回空串（还没填分类号时不拼出 `/15` 这种怪号）；
 *   - 书次号为空 → 只返回分类号（新记录还没取书次号时，预览要能先看到分类号）；
 *   - 附加区分号（同责任者同分类号的不同著作，用 `/` 加序号，如 `I210.4/L824/2`）由 bookNo
 *     自身携带，这里**只负责拼分隔符，不切分**；
 *   - 卷册号非空才加 `VOLUME_SEP`（`:`），空 / 不传即无卷册段。
 *
 * 例：makeCallNo('I247.5', '15')        -> 'I247.5/15'
 *     makeCallNo('I247.5', '15', '上册') -> 'I247.5/15:上册'
 *     makeCallNo('I210.4', 'L824/2')    -> 'I210.4/L824/2'（附加区分号原样保留）
 */
export function makeCallNo(classNo: string | null | undefined,
                           bookNo: string | null | undefined,
                           volume?: string | null): string {
  const c = (classNo ?? '').trim()
  if (!c) return ''
  const b = (bookNo ?? '').trim()
  if (!b) return c
  const base = `${c}/${b}`
  const v = volume == null ? '' : String(volume)
  return v ? `${base}${VOLUME_SEP}${v}` : base
}

/** 排架号允许字符集（PRD §2.3）：中英文、数字、`-` `/` `.` 空格。 */
const SHELF_NO_ALLOWED = /^[A-Za-z0-9一-龥\-/. ]+$/

/**
 * 卷册号 -> 排序号。
 *
 * 命中候选直接用 `option.seq`（这样「第二卷」这类中文数字也有序）；
 * 未命中（自由录入）才按文本里的阿拉伯数字兜底。解析不出返回 null。
 */
export function deriveVolumeSeq(v: string): number | null {
  const s = (v ?? '').trim()
  if (!s) return null
  const hit = VOLUME_OPTIONS.find(o => o.value === s)
  if (hit) return hit.seq
  if (/^\d+$/.test(s)) return Number(s)
  const m = s.match(/\d+/)
  return m ? Number(m[0]) : null
}

/** 排架号软校验提示（**不阻断保存**，只在输入框下方显示灰/橙提示）。 */
export function shelfNoSoftWarning(s: string): string | null {
  const v = (s ?? '').trim()
  if (!v) return null
  if (v.length > SHELF_NO_SOFT_MAX_LEN)
    return `排架号较长（${v.length} 字），建议不超过 ${SHELF_NO_SOFT_MAX_LEN} 字`
  if (!SHELF_NO_ALLOWED.test(v)) return '含非常用字符，仅作提示，不影响保存'
  return null
}

/**
 * 排架号「快速加减」：把**结尾的连续数字**加 `delta`，其余原样保留。
 *
 * 用途：录入栏框尾的 ± 两键 —— 同一排书架上连着编几本，`G-1` 点一下变 `G-2`，
 * 省掉重敲整串。（修订段）
 *
 *   stepShelfNo('G-1',  1) -> 'G-2'
 *   stepShelfNo('G-9',  1) -> 'G-10'   （位数不够自然增长）
 *   stepShelfNo('A-09', 1) -> 'A-10'   （**保持原补零宽度**，不变成 A-9）
 *   stepShelfNo('K3',   1) -> 'K4'
 *
 * 两类"不动"，都返回 `null`（调用方据此把按钮置灰）：
 *   1. **没有结尾数字**（`G` / `架位（一）`）—— 不替用户猜是补 `-1` 还是 `1`；
 *   2. **会减到 0 或以下**（`G-1` 再减、`G-0` 再减）—— 挡住 `G-0` / `G--1` 这种怪号。
 *
 * 首尾空白先 `trim`（框里手滑多打的空格不该让步进失效）。
 */
export function stepShelfNo(value: string, delta: number): string | null {
  const s = (value ?? '').trim()
  const m = s.match(/(\d+)$/)
  if (!m) return null
  const digits = m[1]
  const next = Number(digits) + delta
  if (!Number.isFinite(next) || next < 1) return null
  // 补零宽度按**原数字段**的长度（不是按结果长度）：'A-09' → 'A-10'；
  // 原段无前导 0 时 padStart 是 no-op，'G-9' → 'G-10' 自然变宽。
  const body = String(next).padStart(digits.length, '0')
  return s.slice(0, s.length - digits.length) + body
}

/**
 * 保存载荷该不该带 `book_no`（2026-10-25 书次号链路审查）。
 *
 * 新建/底稿入库时，书次号框里的值多半是**预览回填**的（`useBookNoSync` 自动填，
 * 不算「手敲」）：它在「打开表单 → 保存」的窗口内可能已被别的终端占用，回传
 * 等价于手动占号，会把后端的原子取号绕回「静默异书同号」的老问题（后端
 * `set_book_no` 已加同库跨记录撞号兜底，但前端从源头不传才是治本）。
 *
 * 规则：
 *   - 新建/底稿（`creating`）且用户**没手敲过**（`touched`）→ 不传号，走后端原子取号；
 *   - 手敲过（含**清空**——清空 = 用户明确要自动取号）→ 按手动号传（`set_book_no`
 *     会把计数器抬到位）；
 *   - 已有记录回传现有号是幂等操作（同号 GREATEST 不动、馆藏重排同值），照传。
 */
export function bookNoForSave(opts: {
  creating: boolean
  touched: boolean
  value: string | null | undefined
}): string | undefined {
  if (opts.creating && !opts.touched) return undefined
  const v = (opts.value ?? '').trim()
  return v || undefined
}

/**
 * 分类号按回车时对书次号做什么（纯判定，IO 由调用方执行）。
 *
 * 规则（2026-10-30 用户裁定）：
 *   - 分类号先 `trim + 大写`（与后端 `normalize_class_no` 同口径）；
 *   - 为空/非字母开头 → 清空书次号，不发请求；
 *   - 分类号没变 + 书次号有值 → 保留，不打断手敲；
 *   - 分类号没变 + 书次号被清空 → 填回进入编辑前的原号（“找回来”）；
 *   - 没有原号，或分类号改过 → 按规则预览新号。
 *
 * `loadedClassNo` / `loadedBookNo` 是**进入编辑时**的快照。比较分类号时两边都
 * 大写，避免 `i247` 与 `I247` 被误判成“改过”。
 */
export type ClassNoEnterPlan =
  | { classNo: string; action: 'keep' }
  | { classNo: string; action: 'restore'; bookNo: string }
  | { classNo: string; action: 'preview' }
  | { classNo: string; action: 'clear' }

export function planClassNoEnter(opts: {
  input: string
  currentBookNo: string
  loadedClassNo: string
  loadedBookNo: string
}): ClassNoEnterPlan {
  const classNo = (opts.input ?? '').trim().toUpperCase()
  if (!classNo || !/^[A-Z]/.test(classNo)) return { classNo, action: 'clear' }

  const current = (opts.currentBookNo ?? '').trim()
  const loadedClass = (opts.loadedClassNo ?? '').trim().toUpperCase()
  if (classNo !== loadedClass) return { classNo, action: 'preview' }
  if (current) return { classNo, action: 'keep' }

  const loadedBook = (opts.loadedBookNo ?? '').trim()
  if (loadedBook) return { classNo, action: 'restore', bookNo: loadedBook }
  return { classNo, action: 'preview' }
}
