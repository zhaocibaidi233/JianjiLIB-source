import { ElMessage } from 'element-plus'
import { computed, ref } from 'vue'
import type { Ref } from 'vue'
import { apiErrorText, http } from '../api'
import type { MarcField } from '../api'
import type { ReplacementRequest } from './useReplacementGuard'

/** 从 useReplacementGuard 注入的令牌机三件套（开启 / 四重校验 / 仅比序号）。 */
export interface OptimizeGuard {
  beginReplacement: () => ReplacementRequest
  replacementIsCurrent: (request: ReplacementRequest) => boolean
  isLatestSeq: (request: ReplacementRequest) => boolean
}

/**
 * 规则优化一族（RecordEdit 的「一键规范 / 自动规范」）。
 *
 * 管三件事：
 *   1. **模式开关**（`optimizeMode`：auto / manual）——服务端设置，进页面读取、
 *      胶囊点击持久化；拿不到按 auto 降级（纯规则修复不依赖大模型，默认开）。
 *   2. **`runOptimize`** ——调 `/ai/optimize-local` 规范**当前编辑内容**，
 *      经替换令牌机四重守卫，结果由组件侧 `installResult` 落进编辑态。
 *   3. **改动明细**（`optApplied`）——「已优化 N 项」那排绿标签的数据源。
 *
 * ⚠️ **一律走 `/ai/optimize-local`（不落库）**，已入库记录也一样。原先已入库
 * 记录走 `POST /records/{id}/ai/optimize` —— 那个接口是**就地保存**的
 * （`orm.fields = …; db.commit()`），于是"打开一条记录看一眼"就会把规范化结果
 * 写进数据库。2026-10-05 用户裁定改为：**只改界面，保存时才落库**（与套录路径、
 * 质检弹窗的"自动修复"一致）。顺带好处：规范的是当前编辑内容（含面板里还没
 * 保存的改动），而不是库里那份旧数据。未入库的（新建 / 套录临时副本）本来就没
 * 有 record_id，也必须走这个接口（历史坑：temp 路由参数是字面量 "temp"，拼进
 * /records/{id}/… 会 422）。
 *
 * 编辑态的整稿写入（findings / fields / leader / simple 重灌、价格草稿保护、
 * 临时底稿回写）**刻意不在本 composable**：那是组件形状的状态，硬收进来只会
 * 把 ref 原样漏回去——经 `installResult` 回调留在 RecordEdit
 * （与 installCatalogDraft 同类）。
 */
export function useOptimize(opts: {
  /** 转圈状态。组件持有：工具条、质检门槛按钮、替换守卫的 onReset 三方共用。 */
  optimizing: Ref<boolean>
  guard: OptimizeGuard
  /** 优化请求的载荷：当前 leader + 当前编辑态字段（保存载荷同一出口）。 */
  getPayload: () => { leader: string | null; fields: MarcField[] }
  /** 把优化结果安装进编辑态（见类注释末段）。 */
  installResult: (d: any) => void
  /** 新建空表单（无可规范内容）。 */
  isNew: () => boolean
  /** 套录 / 资源库临时副本（未入库）。 */
  isTemp: () => boolean
}) {
  const { optimizing, guard, getPayload, installResult, isNew, isTemp } = opts

  // 改动明细标签（录入栏下方那排绿标签）
  const optApplied = ref<string[]>([])
  // 规范模式：auto（进入编辑页自动执行，默认）/ manual（仅手动）。
  const optimizeMode = ref<'auto' | 'manual'>('auto')
  const optimizeAuto = computed({
    get: () => optimizeMode.value === 'auto',
    set: (v: boolean) => { optimizeMode.value = v ? 'auto' : 'manual' },
  })

  // 已入库记录才有"打开即规范 / 开开关即补跑"的资格；新建空表单与临时副本
  // 分别由 autoOptimizeAfterLoad 的 tempPayload 分支和保存路径接手。
  const persistedRecord = () => !isNew() && !isTemp()

  /** 进页面读服务端模式；拿不到按 auto（默认开）降级，不打断编目。 */
  async function loadOptimizeMode() {
    try { optimizeMode.value = ((await http.get('/settings/optimize')).data.mode) || 'auto' }
    catch { optimizeMode.value = 'auto' }
  }

  /**
   * `load()` 尾部的自动规范策略：auto 模式下静默跑一遍——
   * 临时底稿与已入库记录都跑（临时副本的自动规范与手动 / 质检修复走
   * **完全相同**的替换协议），全新空表单没有可规范的内容，跳过。
   *
   * 返回**是否真的把结果装进了编辑态**：`load()` 靠它决定要不要重立保存基线
   * （见 RecordEdit 里那段注释）。
   */
  async function autoOptimizeAfterLoad(hasTempPayload: boolean): Promise<boolean> {
    if (optimizeMode.value !== 'auto') return false
    if (hasTempPayload || persistedRecord()) return await runOptimize(true)
    return false
  }

  /** 胶囊点击：先翻转状态，再按既有口径持久化（`saveOptimizeMode` 读的就是 optimizeMode）。 */
  function toggleOptimize() {
    optimizeMode.value = optimizeMode.value === 'auto' ? 'manual' : 'auto'
    saveOptimizeMode()
  }

  async function saveOptimizeMode() {
    try {
      await http.put('/settings/optimize', { mode: optimizeMode.value })
      ElMessage.success(optimizeMode.value === 'auto'
        ? '已开启：打开记录 / 载入套录底稿时自动规范'
        : '已关闭自动规范')
      // 刚开启就对已入库记录补跑一遍（非静默，让用户当场看到效果）
      if (optimizeMode.value === 'auto' && persistedRecord()) await runOptimize()
    } catch { ElMessage.error('设置保存失败') }
  }

  /**
   * 规则规范（纯规则修复，不依赖大模型）。URL 口径见类注释。
   *
   * `silent=true` 供"打开页面自动跑"用：不弹提示（否则每次打开记录都弹一次，
   * 很吵），改动明细仍以标签形式挂在录入栏下方（`optApplied`）。
   *
   * 返回**结果是否已装进编辑态**（被替换守卫作废时返回 false）：
   * 打开记录那条路径据此重立保存基线，避免"看一眼就被判成未保存"。
   */
  async function runOptimize(silent = false): Promise<boolean> {
    const request = guard.beginReplacement()
    optimizing.value = true
    try {
      const d = (await http.post('/ai/optimize-local', getPayload())).data
      if (!guard.replacementIsCurrent(request)) return false
      optApplied.value = d.applied ?? []
      installResult(d)
      if (silent) return true
      if (optApplied.value.length) ElMessage.success(`已优化 ${optApplied.value.length} 项`)
      else ElMessage.info('未发现可自动修复的问题')
      return true
    } catch (e: any) {
      if (!silent && guard.replacementIsCurrent(request)) ElMessage.error(apiErrorText(e))
      return false
    } finally {
      if (guard.isLatestSeq(request)) optimizing.value = false
    }
  }

  return { optimizeMode, optimizeAuto, optApplied, loadOptimizeMode,
           autoOptimizeAfterLoad, toggleOptimize, runOptimize }
}
