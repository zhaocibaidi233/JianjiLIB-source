/**
 * 简编表单的**纯逻辑**：字段绑定表 + 表单 ↔ MARC 双向转换。
 *
 * 抽出来的理由与 `recordRoute.ts` / `isbd.ts` 一样：这段逻辑出错的代价是
 * **静默丢字段**（保存后数据没了，界面上还看不出），必须能脱离组件单测。
 *
 * 数据流：
 *   MARC fields ──populateSimple()──> simple（界面上的扁平 key-value）
 *   simple      ──buildFromSimple()─> MARC fields（保存载荷）
 *
 * 关键约定：简编表单只覆盖 `BINDINGS` 里列出的字段；**其余字段与子字段必须原样保留**
 * —— 而且**原样、原位**：首个实例内的子字段相对序也不重排（P1-1，2026-10-08）。
 */
import type { MarcField } from './api'
import { stripIsbdTail } from './isbd'

export interface Binding {
  tag: string
  code: string
  label: string
  /**
   * 在 24 栅格里的占宽（见 `colSpan()`）。默认 24 = 整行。
   * 占位极短的字段（价格/页码/尺寸/年份/版次…）给 6 或 8，与同排字段**拼满一行**——
   * 目标是整张表单**不滚动就能看全**。
   *
   * ⚠️ **声明顺序 = 新建字段时的子字段排布顺序**（"规范序"）。
   *
   * 2026-10-08 之前它还是「存量字段重排的依据」（`orderSubfields()` 按它排序）；
   * P1-1 修掉之后**存量字段的子字段序一律原样保留**，本表只管**新建**字段
   * （见 `buildFromSimple()` 的第二段循环）。所以这里乱了**不会**再动存量数据，
   * 但新建的字段会跟着乱 —— 同一 tag 内仍请保持 CNMARC 规范序
   * （200 = a→d→e→f→g→h→i），不能为了排版打乱。
   */
  span?: number
  /** 灰色固定单位，界面上只显数值，MARC 里存完整值（如 CNY45.00 / 240页 / 20cm） */
  unit?: string
  unitPos?: 'prepend' | 'append'
  type?: 'textarea'
  /**
   * 候选来源。**必须"不抢输入"**：
   *   - `select`  —— 值是代码、编目员背不下来（语种），用下拉选择
   *   - 其余      —— 编目员心里有值，只在输入框**下方**给一排可点的候选，
   *                  **不做下拉**：下拉会把回车变成"选第一条"，把刚敲的值悄悄换掉
   *                  （分类号曾因此踩过，见 README 业务规则）。
   */
  suggest?: 'select-language' | 'publisher' | 'place' | 'edition' | 'size' | 'responsibility'
  /**
   * 有 `options` 的字段**渲染成下拉**（模板据 `b.options` 判定）。
   * 下拉一律 `allow-create`，保留自由录入——CNMARC 该子字段本质是自由文本，
   * **不能锁死枚举**（如装订 `010$b` 允许枚举外的写法）。
   */
  options?: readonly string[]
  /** 所属视觉分区（对应 `GROUPS` 里的 `key`）。**仅用于分区渲染**，不参与 `buildFromSimple()` */
  group?: string
  /** 简要级次必备字段（200$a / 215$a / 690$a / 101$a）：标签前加红星 */
  required?: boolean
  /**
   * 数值右对齐（金额 / 页码 / 尺寸 / 年份，对标老系统 `字段定义文件` 的 MiddleRight）。
   *
   * 注：`hint`（字段级灰字提示）字段在 2026-10-05 被移除过一次；当时那条"记录编辑页不留框内
   * 灰字"的约定 **2026-10-06 已作废**（用户：那只是之前特殊情况用的）。所以现在**想加回灰字
   * 提示是允许的** —— 但要走 `placeholder`，别再引入 `hint` 这种"额外多一行"的机制。
   */
  align?: 'right'
}

/**
 * 装订（`010$b`）的常见取值候选。
 *
 * 仅作下拉**快捷输入**，配合 `allow-create` 仍可手打枚举外的写法——
 * `010$b` 在 CNMARC 里本质是自由文本，锁死枚举会挡住少见装帧。
 */
export const BINDING_OPTIONS = ['精装', '平装', '线装', '活页装', '套装'] as const

/**
 * 简编表单字段（：**23 栏**）。
 *
 * **声明顺序 = 两个用途**：① 模板按此渲染；② **新建**字段时按此拼子字段。
 * **存量字段的子字段序不再受它影响**（2026-10-08 起原样保留，见 `buildFromSimple()`）。
 * 因此**同一 tag 的子字段仍请按 CNMARC 规范序排列**（如 200:`a→d→e→f→g`）——
 * 但理由变了：2026-10-08 之前乱了会**重排存量记录**（的回归风险点），
 * 现在存量不再受影响，乱的后果只剩「**新建**的字段子字段序不好看」。
 * 跨 tag 的顺序按 §5.1 的分区。
 *
 * 排版约定：每组内 `span` 之和为 **24 的整数倍**，
 * 即"一行填满、不留半行空位"—— 这是「不滚动看全表单」的前提，有单测守着。
 *
 * 分区只是**视觉分组**：`BINDINGS` 仍是扁平数组，`buildFromSimple()` 一行不改。
 */
export const BINDINGS: readonly Binding[] = [
  // ── 组1 标准号与获得方式（1 行）──
  { tag: '010', code: 'a', label: 'ISBN', span: 10, group: 'std', align: 'right' },
  { tag: '010', code: 'b', label: '装订', span: 6, group: 'std', options: BINDING_OPTIONS },
  // 定价的 `unit` 只是**兜底**：真正生效的货币码是「本馆默认币种」（`SimpleOptions.currency`，
  // 见 unitOf/joinPrice）。改币种时不必动这张表。
  { tag: '010', code: 'd', label: '定价', unit: 'CNY', unitPos: 'prepend', span: 8,
    group: 'std', align: 'right' },
  // ── 组2 题名与责任者（3 行；200 子字段按 CNMARC 规范序 a→d→e→f→g）──
  //
  // ⚠️ **这里刻意不含 `200$h 分册号` / `200$i 分册名`**（2026-10-05 用户反馈后撤掉）：
  //    它们与**记录级「卷册号」**（§组8，走 `extra.volume` → 索书号 → `905$v`）在编目
  //    实务里问的是同一件事（"这是第几册/哪一卷"），一个简编表单里出现两栏纯属重复劳动。
  //    取舍依据：卷册号是**项目核心**（索书号拼接 + 真实多卷书数据验证），必须留；
  //    `200$h/$i` 属**题名范畴**的著录项，本就可空。
  //    它们**不在表单里 ≠ 会被丢掉**：作为"表单未覆盖的子字段"原样保留（规则 12），
  //    要编辑去「全字段 MARC」。
  { tag: '200', code: 'a', label: '正题名', span: 12, group: 'title', required: true },
  // 200$i 分辑名回到简编表单（2026-10-30）：多分卷书（如《认识中国植物》各省分册）
  // 套录回来 200$a 全同、只有 $i 能区分——编目员对着一排同名表单无法确认"手里这本
  // 是哪一册"。2026-10-05 撤掉它的理由是与记录级「卷册号」重复，但那个字段服务
  // 索书号拼接（905$v），与题名著录是两码事；有 $i 的多卷书这栏就是唯一对书凭证。
  { tag: '200', code: 'i', label: '分辑名', span: 12, group: 'title' },
  { tag: '200', code: 'd', label: '并列题名', span: 12, group: 'title' },
  { tag: '200', code: 'e', label: '副题名', span: 12, group: 'title' },
  // 责任者两栏挂 `suggest: 'responsibility'`：候选来自「字典中心 → 责任方式」
  // （内置 22 条：著/编/译/主编/编著…）。**候选是"追加"不是"替换"**——
  // `200$f` 是「姓名 + 责任方式」的连写（如 `张志通主编`），替换会把姓名冲掉。
  { tag: '200', code: 'f', label: '第一责任者', span: 8, group: 'title',
    suggest: 'responsibility' },
  { tag: '200', code: 'g', label: '其他责任者', span: 8, group: 'title',
    suggest: 'responsibility' },
  { tag: '101', code: 'a', label: '语种', span: 8, group: 'title', required: true,
    suggest: 'select-language' },
  // ── 组3 版本与出版发行（1 行；合计 4+4+10+6=24）──
  //    2026-10-05 用户："版次和出版地的框可以短一些，省出的空间给出版者的框加长"：
  //    版次/出版地 6→4（短值够用），出版者 6→10（社名长），出版年 6 不动。只改 span。
  { tag: '205', code: 'a', label: '版次', span: 4, group: 'pub', suggest: 'edition' },
  { tag: '210', code: 'a', label: '出版地', span: 4, group: 'pub', suggest: 'place' },
  { tag: '210', code: 'c', label: '出版者', span: 10, group: 'pub', suggest: 'publisher' },
  { tag: '210', code: 'd', label: '出版年', span: 6, group: 'pub', align: 'right' },
  // ── 组4 载体形态（1 行）──
  { tag: '215', code: 'a', label: '页数', unit: '页', unitPos: 'append', span: 6,
    group: 'phys', required: true, align: 'right' },
  { tag: '215', code: 'c', label: '图表', span: 6, group: 'phys' },
  { tag: '215', code: 'd', label: '尺寸', unit: 'cm', unitPos: 'append', span: 6,
    group: 'phys', suggest: 'size', align: 'right' },
  { tag: '215', code: 'e', label: '附件', span: 6, group: 'phys' },
  // ── 组5 丛编（1 行）──
  { tag: '225', code: 'a', label: '丛题名', span: 12, group: 'series' },
  { tag: '225', code: 'h', label: '丛编分册', span: 6, group: 'series' },
  { tag: '225', code: 'v', label: '丛编卷次', span: 6, group: 'series' },
  // ── 组6 分类与主题（1 行；整块自定义，见 RecordEdit.vue 与 `subjectRowSpans()`）──
  //    这一行 = 「索书号一行三站」〔分类号 690$a + 记录级书次号 + 记录级卷册号〕
  //    + 记录级排架号（受图书库开关控制）+ 只读主题词/责任者检索点。
  //    只有 `690$a` 是 CNMARC 子字段，其余三个都是**记录级**列，绝不进本表（README 规则 12）。
  //
  //    `span` 与 `GROUP_EXTRA_SPAN.subject` 在这里**只是记账**：两者相加恰为 24，
  //    让"每组占宽之和是 24 的倍数"的单测对组6 仍成立。模板/导航实际用的是
  //    `subjectRowSpans()` —— 改版面请改它，不要改这里的 6。
  { tag: '690', code: 'a', label: '分类号', span: 6, group: 'subject', required: true },
  // ── 组7 提要与附注（2 行）──
  { tag: '330', code: 'a', label: '内容提要', type: 'textarea', span: 24, group: 'note' },
  { tag: '300', code: 'a', label: '附注', span: 24, group: 'note' },
] as const

/** 简编表单的视觉分区。共 7 组；卷册号/排架号已内联进组6，不再有独立的「记录级信息」组。 */
export interface FieldGroup {
  key: string
  label: string
}

export const GROUPS: readonly FieldGroup[] = [
  { key: 'std', label: '标准号与获得方式' },
  { key: 'title', label: '题名与责任者' },
  { key: 'pub', label: '版本与出版发行' },
  { key: 'phys', label: '载体形态' },
  { key: 'series', label: '丛编' },
  { key: 'subject', label: '分类与主题' },
  { key: 'note', label: '提要与附注' },
] as const

/** 取某个分区下的字段（模板按分区渲染用）。 */
export function bindingsInGroup(key: string): readonly Binding[] {
  return BINDINGS.filter(b => b.group === key)
}

export type SimpleState = Record<string, string>

/**
 * 常见值候选（本地常量，不发请求）。
 *
 * 只做"点一下就能填"的提示，**不做下拉**——这些字段编目员心里有值，
 * 下拉抢输入比省事更麻烦。值是 CNMARC 里的**完整形态**（尺寸带 cm）。
 */
export const EDITION_SUGGESTIONS = [
  '第2版', '第3版', '第4版', '第5版', '修订版', '增订版', '第2版(修订本)', '影印本',
]
// 注意是**裸数值**：界面上尺寸框只显数值，`cm` 由 joinUnit 在拼 MARC 时补
export const SIZE_SUGGESTIONS = ['19', '20', '21', '23', '24', '26', '29', '30']

/** 表单 key：`200` + `a` -> `'200a'` */
export const simpleKey = (tag: string, code: string): string => `${tag}${code}`

/** 简编表单覆盖的字段标签（其余标签的字段一律原样保留） */
export const BOUND_TAGS: readonly string[] = [...new Set(BINDINGS.map(b => b.tag))]

/** 该字段在 24 栅格里的占宽（默认整行）。模板与单测共用，避免两处各算一遍。 */
export const colSpan = (b: Binding): number => b.span ?? 24

/**
 * 模板在某个分区内**额外占用**的栅格数。
 *
 * ⚠️ 「分类与主题」这一行现在是**整块自定义**的（见 `subjectRowSpans()`），模板不再按
 * `GROUP_EXTRA_SPAN` 追加一个只读栏。这个常量因此只剩**记账**用途：让「每组占宽之和是
 * 24 的整数倍」那条单测对组6 仍然成立（`690$a.span = 6` + 18 = 24）。
 * 真正决定组6 版面的是 `subjectRowSpans()` —— 改版面请改它，别改这里。
 */
export const GROUP_EXTRA_SPAN: Readonly<Record<string, number>> = { subject: 18 }

/**
 * 「分类与主题」一行的栅格配比（**唯一真源**）。
 *
 * 这一行 = 一个「索书号行」+ 两个内联块，与**录入栏**同构（用户："参考录入栏"）：
 *   ① `callNo`：一个 el-col 里放 **分类号 / 书次号 / 卷册号** 三格（`.callno-row`）。
 *      整块**只给一个外置标签**（"分类号"）；书次号、卷册号**不放外置标签**，只靠
 *      **框内灰色占位**（书次号 / 卷册号）说明自己是谁 —— 标签宽 86px，三栏各带一个
 *      就是 258px 白占宽度，把三个框挤没（用户："不要放外面占位置"）。
 *   ② `shelfNo`：排架号（**记录级**，受图书库开关控制）。
 *   ③ `readonly`：只读检索点区（主题词 / 责任者；不可编辑但**占栅格**）。
 *
 * 三格里 `bookNo` / `volume` 与 `shelfNo` 一样是**记录级**列，绝不进 `BINDINGS`
 * （README 规则 12）；只有 `690$a` 是 CNMARC 子字段。
 *
 * 排架号开/关 → 两种配比，**两种都恰好填满 24 栅格**（有单测守着）：
 *   排架号开：索书号行 12 | 排架号 4 | 只读 8   = 24
 *   排架号关：索书号行 14 | 只读 10             = 24
 * 半行空位＝白占一行高度，与"不滚动看全"的目标冲突，故两种配比都必须整除 24。
 *
 * 2026-10-06 用户两轮反馈：
 *   ① "分类号框和卷册号框都可以短一些，在分类号后面也加上书次号框" —— 新增书次号；
 *   ② "书次号、卷册号用灰色预输入提示显示在框里就行，参考录入栏，不要放外面占位置"
 *      —— 三格并成一个 `.callno-row`、只留一个外置标签（本轮）。
 */
export function subjectRowSpans(shelfNoEnabled: boolean) {
  return shelfNoEnabled
    ? { callNo: 12, shelfNo: 4, readonly: 8 }
    : { callNo: 14, shelfNo: 0, readonly: 10 }
}

/**
 * 候选 chip 每个字段最多显示几个。
 * 不设上限的话，「出版地」的 12 个候选会把 span=6 的窄栏撑成三四行，
 * 直接破坏"不滚动就能看全表单"的版面目标。
 */
export const CHIP_MAX = 6

// ---- 键盘导航：简编表单的"视觉行"结构（唯一真源）----

/** 简编表单的一行：元素是可编辑字段的 key（`tag+code`；记录级字段用 `'bookNo'` / `'volume'` / `'shelfNo'`）。 */
export type SimpleRow = string[]

/**
 * 按**视觉行**切分简编表单的可编辑字段（键盘导航的唯一定义）。
 *
 * 累加规则与模板的 `el-row/el-col` **同源**：按 `GROUPS` / `BINDINGS` 声明顺序累加
 * `span`，每满 24 栅格收一行。所以"↑/↓ 到上一行/下一行"与眼睛看到的行一致。
 *
 * ⚠️ 只读检索点区（组6 尾部）**不可编辑但占栅格**，必须计入累加 —— 漏掉它行会被提前切断，
 * 组6 的索书号行就会跟提要对不上行。
 *
 * 改动 span 或分区顺序时，这个函数与模板会同时变（模板用的是同一批 span 常量），
 * 不必手工同步数字。
 */
export function simpleFormRows(shelfNoEnabled: boolean): SimpleRow[] {
  const rows: SimpleRow[] = []
  let row: SimpleRow = []
  let used = 0
  /** 累加栅格，满 24 收一行（`row` 里已有的 key 原样落进这一行的快照）。 */
  const commit = (span: number) => {
    used += span
    if (used >= 24) { rows.push(row); row = []; used = 0 }
  }
  const add = (key: string | null, span: number) => {
    if (key) row.push(key)
    commit(span)
  }

  for (const g of GROUPS) {
    if (g.key === 'record') continue                 // 已撤的分区，防御性跳过
    if (g.key === 'subject') {
      // 组6 的「索书号行」把 分类号 / 书次号 / 卷册号 三格装进**同一个 el-col**
      // （同录入栏的一行三站）→ 三格共用一个栅格数，不能用 add() 逐个加（那会算三遍，
      // 行会被提前切断，方向键就跳到"看不见的地方"了）。
      row.push('690a', 'bookNo', 'volume')
      commit(subjectRowSpans(shelfNoEnabled).callNo)
      if (shelfNoEnabled) add('shelfNo', subjectRowSpans(shelfNoEnabled).shelfNo)
      add(null, subjectRowSpans(shelfNoEnabled).readonly)   // 只读区：占位不可编辑
      continue
    }
    for (const b of bindingsInGroup(g.key)) add(simpleKey(b.tag, b.code), colSpan(b))
    if (GROUP_EXTRA_SPAN[g.key]) add(null, GROUP_EXTRA_SPAN[g.key])
  }
  if (row.length) rows.push(row)
  return rows
}

/** 键盘导航方向。`next` = 回车跳下一格（行内向右，到行尾换下一行行首）。 */
export type SimpleNavDir = 'up' | 'down' | 'left' | 'right' | 'next'

/**
 * 给定当前字段与方向，返回目标字段 key（没有目标 → `null`）。
 *
 * 上下移动时列号按**目标行的长度夹取**：各组列数不同（1~4 列），
 * 夹取后"↓"总能落在最近的一格上，不会因为越界而卡住不动。
 */
export function nextSimpleKey(rows: readonly SimpleRow[], cur: string,
                              dir: SimpleNavDir): string | null {
  let r = -1
  let c = -1
  for (let i = 0; i < rows.length; i++) {
    const j = rows[i].indexOf(cur)
    if (j >= 0) { r = i; c = j; break }
  }
  if (r < 0) return null

  if (dir === 'left') return c > 0 ? rows[r][c - 1] : null
  if (dir === 'right') return c < rows[r].length - 1 ? rows[r][c + 1] : null
  if (dir === 'next') {
    if (c < rows[r].length - 1) return rows[r][c + 1]
    return r < rows.length - 1 ? rows[r + 1][0] : null
  }
  const tr = r + (dir === 'down' ? 1 : -1)
  if (tr < 0 || tr >= rows.length) return null
  return rows[tr][Math.min(c, rows[tr].length - 1)]
}

// ---- 单位字段的显示/存储转换（界面只显数值，MARC 存完整值）----
export function stripUnit(raw: string, unit: string, pos?: string): string {
  let v = (raw ?? '').trim()
  if (pos === 'prepend') {          // 'CNY45.00' -> '45.00'
    if (v.toUpperCase().startsWith(unit)) v = v.slice(unit.length)
  } else if (pos === 'append') {    // '240页' -> '240' / '20cm' -> '20'
    if (v.endsWith(unit)) v = v.slice(0, -unit.length)
  }
  return v.trim()
}

export function joinUnit(num: string, unit: string, pos?: string): string {
  const v = (num ?? '').trim()
  if (!v) return ''
  return pos === 'prepend' ? `${unit}${v}` : `${v}${unit}`
}

// ---- 定价（010$d）的货币码是**可配置**的 ----

/**
 * 定价框的兜底货币码。真正生效的值来自「字典中心 → 货币类型 → 本馆默认币种」，
 * 由调用方通过 `SimpleOptions.currency` 传进来；这里只是拿不到时的兜底。
 */
export const FALLBACK_CURRENCY = 'CNY'

export interface SimpleOptions {
  /** 本馆默认币种（定价 `010$d` 的货币码）。缺省 `'CNY'`。 */
  currency?: string
}

/**
 * 字段的**界面单位**：定价（`010$d`）的货币码可配置，其余字段单位固定。
 *
 * 为什么不能把 `BINDINGS` 里那个 `unit: 'CNY'` 当准：改了默认币种的馆会看到
 * 「USD | USD12.00」这种重复前缀，而且 `stripUnit` 也剥不掉它。
 */
function unitOf(b: Binding, opts?: SimpleOptions): { unit?: string; pos?: string } {
  if (b.tag === '010' && b.code === 'd') {
    return { unit: opts?.currency || FALLBACK_CURRENCY, pos: b.unitPos }
  }
  return { unit: b.unit, pos: b.unitPos }
}

/** 形如 `USD12.00` / `CNY29.8`：2~4 个字母打头、紧跟着数字（即已带货币码）。 */
const CURRENCY_PREFIX_RE = /^[A-Za-z]{2,4}\d/

/**
 * 定价的「界面值 → MARC 值」。
 *
 * **已带货币码的原样保留**（含与本馆默认不同的外币）：把 `USD12.00` 改写成
 * `CNY12.00` 等于悄悄改掉"这是美元定价"这件事 —— 本项目最忌讳静默改数据。
 * 只有**裸数字**才补默认币种前缀。
 */
export function joinPrice(num: string, currency: string): string {
  const v = (num ?? '').trim()
  if (!v) return ''
  return CURRENCY_PREFIX_RE.test(v) ? v : `${currency}${v}`
}

/** 单个字段的「界面值 → MARC 值」（定价走 joinPrice，其余走 joinUnit）。 */
function marcValueOf(b: Binding, num: string, opts?: SimpleOptions): string {
  if (!num) return ''
  if (b.tag === '010' && b.code === 'd') return joinPrice(num, opts?.currency || FALLBACK_CURRENCY)
  return b.unit ? joinUnit(num, b.unit, b.unitPos) : num
}

/** 把 MARC 字段灌进表单（只取第一条匹配的字段实例）。 */
export function populateSimple(fs: MarcField[], simple: SimpleState,
                               opts?: SimpleOptions): void {
  for (const b of BINDINGS) {
    const f = fs.find(x => x.tag === b.tag && (x.sf ?? []).some(s => s[0] === b.code))
    let raw = f ? (f.sf ?? []).find(s => s[0] === b.code)![1] : ''
    // 简编表单把题名/责任者拆成多栏，展示时必须剥掉 ISBD 尾标点，
    // 否则用户会以为「/」是题名的一部分（入库时由后端清洗管线补回）。
    if (b.tag === '200' && (b.code === 'a' || b.code === 'e')) raw = stripIsbdTail(raw)
    const { unit, pos } = unitOf(b, opts)
    simple[simpleKey(b.tag, b.code)] = unit ? stripUnit(raw, unit, pos) : raw
  }
}

/**
 * 子字段该插在哪个位置：**最后一个「规范序排在它之前」的已存在子字段**之后；
 * 没有这样的前驱就插到字段最前。
 *
 * `sf` 里混着未覆盖的子字段（不在 `order` 里，`indexOf` = -1）——它们**不参与定位**，
 * 但也**绝不因此被挪动**（`j >= 0` 那道判断就是为它们设的；漏掉会得到"未覆盖子字段
 * 全被当成前驱"的错位）。
 */
function insertAt(sf: readonly [string, string][], idx: number,
                  order: readonly string[]): number {
  for (let i = sf.length - 1; i >= 0; i--) {
    const j = order.indexOf(sf[i][0])
    if (j >= 0 && j < idx) return i + 1
  }
  return 0
}

/**
 * 把表单值**就地**合并进一个已存在的字段实例（P1-1 的修法）。
 *
 * 旧写法是 `[...已绑定, ...未覆盖]` **重建**：未覆盖的子字段一律被推到末尾，
 * 于是 `200 $a$9$f` 存一次就变成 `$a$f$9` —— 真实样本
 * （`backend/tests/fixtures/sample_iso_50.iso`）**50/50 条命中**。
 *
 * 新写法**照原顺序走一遍**，三个分支各自说清：
 *   - 已绑定的子字段 → **原地**换值；值为空 = 用户清空了它 → 丢弃这一条；
 *   - 未覆盖的子字段 → **原样、原位**保留（不因为"表单不认识它"就被挪走）；
 *   - 表单填了、原实例里没有的子字段 → 插到规范序前驱之后（见 `insertAt`）。
 *
 * ⚠️ **不变式**：原实例本来就在规范序时，本函数的结果与旧写法**逐字相同** ——
 * 只有"原本乱序"的记录才会看到差别（而那里正是我们要的：不再替用户改顺序）。
 *
 * 已知未修（P2-1，本次**刻意不动**）：同一 code 的第 2 次及以后出现
 * 仍被丢弃（简编表单每栏只有一个输入框；真实样本 0 命中；且后端
 * `clean.merge_duplicate_subfields` 本来就会把**连续**同码子字段合并成一条，
 * 真要动得连它一起裁量）。
 */
function mergeIntoExisting(sf: readonly [string, string][], bs: readonly Binding[],
                           vals: Record<string, string>): [string, string][] {
  const order = bs.map(b => b.code)
  const seen: Record<string, boolean> = {}
  const out: [string, string][] = []

  for (const [code, value] of sf) {
    if (!order.includes(code)) { out.push([code, value]); continue }
    if (seen[code]) continue                       // P2-1：同一 code 只认第一次（未修）
    seen[code] = true
    const v = vals[code]
    if (v) out.push([code, v])
  }

  // 表单新填的：按 BINDINGS 声明序逐个插入（顺序处理，后一个能看见前一个插进去的位置）
  for (const b of bs) {
    if (seen[b.code] || !vals[b.code]) continue
    out.splice(insertAt(out, order.indexOf(b.code), order), 0, [b.code, vals[b.code]])
  }
  return out
}

/** 指标符：200 是「题名主要责任者」用 1，101 语种用 0，其余空格。 */
function indicators(tag: string): [string, string] {
  return [tag === '200' ? '1' : tag === '101' ? '0' : ' ', ' ']
}

/**
 * 表单 -> MARC 字段。**保存载荷的唯一出口之一**（另一条是全字段 tab 直接用 fields）。
 *
 * 核心不变式：**只重建"表单真正承载了内容"的那些字段实例，其余一律保留**。
 * 简编表单每栏只有一个输入框，所以：
 *   - 同一 tag 的**第 2 个及以后的字段实例**（合订书名、多出版者、多个 ISBN）要保留；
 *   - 表单没覆盖的**子字段**（`200$h` 分册、`225$v` 卷次、…）要保留；
 *   - 表单覆盖的子字段若被清空，则该实例可能整体失去意义（见下）。
 *
 * ⚠️ **第二条不变式（2026-10-08 起）：首个实例的子字段相对序原样保留。**
 * 用表单值**就地替换**、不重排（`mergeIntoExisting`）； 的 P1-1 由此修掉。
 * 唯一仍会动顺序的地方是末尾的**字段级** `sort(tag)` —— 那是有意的
 * （"字段按 tag 升序"便于与全字段视图对照，且 ISO 惯例如此），与子字段序无关。
 */
export function buildFromSimple(fields: MarcField[], simple: SimpleState,
                                opts?: SimpleOptions): MarcField[] {
  const out: MarcField[] = []
  const consumed: Record<string, boolean> = {}      // tag -> 是否已用表单重建过

  for (const f of fields) {
    if (!BOUND_TAGS.includes(f.tag)) { out.push(f); continue }

    // 第 2 个及以后的实例：简编表单只有一栏，**不能**用表单值覆盖它，
    // 否则合订书名 / 多出版者会被静默吞掉。原样保留（子字段序自然也不动）。
    if (consumed[f.tag]) { out.push(f); continue }
    consumed[f.tag] = true

    const bs = BINDINGS.filter(b => b.tag === f.tag)
    const vals: Record<string, string> = {}
    for (const b of bs) {
      vals[b.code] = marcValueOf(b, (simple[simpleKey(b.tag, b.code)] ?? '').trim(), opts)
    }

    // 第一个该 tag 的实例：表单值**就地**合并进去（保留原顺序，见 mergeIntoExisting）
    const sf = mergeIntoExisting(f.sf ?? [], bs, vals)
    if (!sf.length) continue                       // 表单全空且无额外子字段 -> 丢掉该实例
    const [i1, i2] = indicators(f.tag)
    out.push({ tag: f.tag, i1, i2, sf })
  }

  // 表里有值、但记录里原本没有该 tag 的实例 -> 补一个新字段
  // （新字段按 BINDINGS 声明序拼子字段 —— 这是"规范序"唯一的落点，不需要再排序）
  for (const tag of BOUND_TAGS) {
    if (consumed[tag]) continue
    const bs = BINDINGS.filter(b => b.tag === tag)
    const sf: [string, string][] = []
    for (const b of bs) {
      const v = marcValueOf(b, (simple[simpleKey(b.tag, b.code)] ?? '').trim(), opts)
      if (!v) continue
      sf.push([b.code, v])
    }
    if (!sf.length) continue
    const [i1, i2] = indicators(tag)
    out.push({ tag, i1, i2, sf })
  }

  return out.sort((a, b) => a.tag.localeCompare(b.tag))
}
