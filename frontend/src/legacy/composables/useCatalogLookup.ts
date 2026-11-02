import { computed, nextTick, ref } from 'vue'
import { moveFocus } from './candFocus'
import type { ComputedRef, Ref } from 'vue'
import type { Router } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { catalogSearch, quickEntry, apiErrorText } from '../api'

/**
 * 录入栏的「就地套录」：一个输入框吃 **ISBN / 题名 / 责任者** 三种输入。
 *
 * 检索顺序由后端分层短路：**图书库 → 资源库 → Z39.50**（前一层命中就不查下一层）。
 *   - 命中图书库：同 ISBN 可能是同书的不同卷册/版本 → **弹窗二选一**：
 *     打开已有条目（加复本）/ 作为新记录编目（另编一册，见）；
 *   - 命中资源库/Z39.50：编新书 → 走调用方注入的 `onCataloged`（未入库就地填充；
 *     已入库一律另存为新记录，绝不覆盖当前记录——踩过条码全挂到同一条记录的事故）。
 */
export function useCatalogLookup(opts: {
  router: Router
  isNew: ComputedRef<boolean>
  isTemp: ComputedRef<boolean>
  recordIdOrNull: ComputedRef<number | null>
  currentLibId: ComputedRef<number | undefined>
  /** 弹窗关闭后送光标的目标（条码框） */
  barcodeInput: Ref<any>
  /** 「打开已有条目」主按钮的模板 ref（声明在组件里，`ref="candOpenBtn"` 按名绑定） */
  candOpenBtn: Ref<any>
  /** 套录结果的唯一落点（RecordEdit 提供：就地填充 / 另存为新记录） */
  onCataloged: (record: any, title?: string, focusDelay?: number) => Promise<void>
  /**
   * 「一键套录」的落点。与 `onCataloged` **分开是必须的**：快速录入的返回记录
   * 已经过 `localize_record`，而 `onCataloged` 会再走一次 `/cataloging/prepare`
   * —— 那等于重编 001、多塞一条 035（localize 不幂等，实测会污染数据）。
   */
  onQuickRecord: (record: any, hitSource?: string) => Promise<void>
}) {
  const { router, isNew, isTemp, recordIdOrNull, currentLibId, barcodeInput,
          candOpenBtn, onCataloged, onQuickRecord } = opts

  const isbnLookup = ref('')
  const looking = ref(false)
  // 套录命中时的候选弹窗
  const candDlg = ref(false)
  const candList = ref<any[]>([])
  // 候选来自图书库（带 record_id）→ 弹窗语义变成"二选一"：打开已有条目 / 另编一册
  const candInLibrary = computed(() => candList.value.some((c: any) => c.record_id))
  // 图书库候选下当前选中的那条（点行选中，再用底部按钮决定动作）
  const candSelected = ref<any>(null)
  // 本次候选弹窗是不是「一键套录」撞了已编目记录——文案要说清「同号不同书」怎么走
  const candFromQuick = ref(false)
  // 已经手动点过「检索其他来源」：按钮只给一次，避免反复打 Z39.50
  const candHasExternal = ref(false)
  // 候选分页：page 从 0 起，每页条数由后端按全局设置给（响应里带 limit）
  const candPage = ref(0)
  const candHasMore = ref(false)
  const otherBusy = ref(false)
  // 外部源（资源库 / Z39.50）的候选条数，用于文案与按钮显隐
  const candExternalCount = computed(
    () => candList.value.filter((c: any) => !c.record_id).length)
  let pendingFocusAfterDialog = false   // 弹窗关闭后是否把光标送到条码框
  // 弹窗关闭后才执行的跳转（见 openSelectedLibrary 的注释）
  let pendingNav: { rid: number; title: string; addCopy: boolean } | null = null
  // 「选中后回车」关闭弹窗时，要临时压住套录框的回车检索。原因见 muteLookupEnter。
  let lookupEnterMutedUntil = 0

  /**
   * 临时忽略套录框上的「回车检索」。
   *
   * 场景：焦点在弹窗按钮上按回车 → 按钮点击 → 弹窗关闭 → **Element Plus 的焦点陷阱
   * 立刻把焦点还给套录输入框** → 同一次按键的 `keyup` 就落在输入框上 →
   * `@keyup.enter` 又跑一遍检索 → 弹窗刚关又弹回来。
   *
   * 真机实测（2026-10-24）：回车确认后 overlay 在 100ms 内就进入
   * `dialog-fade-enter-active`，说明是**重新打开**而不是关闭中。鼠标点击没有这个问题
   * （没有 keyup），所以这个坑只在纯键盘操作时出现——而键盘操作正是编目员的常态。
   *
   * 只拦"紧随关闭"的几百毫秒，之后正常按回车不受影响。
   */
  function muteLookupEnter(ms = 400) { lookupEnterMutedUntil = Date.now() + ms }
  /** 套录框的回车是否正处在上面那个抑制窗口里。 */
  function lookupEnterMuted() { return Date.now() < lookupEnterMutedUntil }

  /** 打开已有条目的编辑模式。**只在编目员明确选了「打开已有条目」时调用**——
   *  命中图书库本身不再自动打开（同 ISBN 可能是另一册，见 lookupByIsbn 的注释）。 */
  /**
   * 打开已有条目。
   *
   * `addCopy`：这次是**加复本**（弹窗里的「打开已有条目（加复本）」）。加复本要在
   * 编辑页的「条码」框里填号，所以直接落到 `/edit?addCopy=1` 并让条码框拿到焦点——
   * 落到只读的详情页，用户会找不到地方加条码（2026-10-24 实测反馈）。
   *
   * ⚠️ 路径必须带 `/admin` 前缀：路由树挂在 `/admin` 下，写成 `/records/{id}`
   * 会被 catch-all 兜到 `/admin`——用户以为"点了没反应"，其实是跳去了工作台。
   */
  function openLibraryRecord(c: any, opts: { addCopy?: boolean } = {}) {
    const rid = Number(c.record_id)
    if (rid === recordIdOrNull.value) {
      ElMessage.info(`《${c.title ?? '—'}》即为当前记录 #${rid}`)
      return
    }
    ElMessage.success(opts.addCopy
      ? `《${c.title ?? '—'}》已在图书库（记录 #${rid}），填条码即可加复本`
      : `《${c.title ?? '—'}》已在图书库（记录 #${rid}），已打开该条目`)
    router.push(opts.addCopy
      ? `/admin/records/${rid}/edit?addCopy=1`
      : `/admin/records/${rid}`)
  }

  /**
   * 「套录」框的唯一入口。
   *
   * 走哪条路由**后端判断**（`q` 参数）：形如 ISBN 就按 ISBN 查；否则先按题名查，
   * 没命中再按责任者查一次（Z39.50 会把题名+责任者拼成 AND，一起传会互相打架）。
   *
   * `library_id`：图书库这一层只看当前书库（同书异校各有一条，不该互相判成"已经编过"）。
   */
  async function lookupByIsbn(page = 0) {
    const q = isbnLookup.value.trim()
    if (!q) return
    if (looking.value) return       // 重入守卫：Z39.50 慢，回车连发不重复检索（也不双弹候选窗）
    looking.value = true
    try {
      // 不传 limit：由后端按「全局设置 → 资源库 → 套录命中条数」决定，
      // 前端写死一个数字会让那个设置形同虚设。
      const d = await catalogSearch({ q, library_id: currentLibId.value, page })
      const hits = (d.candidates ?? []).filter((c: any) => c.record || c.record_id)
      if (!hits.length) {
        ElMessage.warning(d.matched_by === 'author'
          ? `题名与责任者均未找到「${q}」，请手工录入`
          : `未找到「${q}」，请手工录入`)
        return
      }
      // 题名没命中、改按责任者找到的：说一声，免得编目员以为"搜错题名了"
      if (d.matched_by === 'author') {
        ElMessage.info(`未找到同名书，改按责任者「${q}」找到 ${hits.length} 条`)
      }

      // 1) 图书库命中：**不自动打开**。同 ISBN 的多卷书（上下册共用一个 ISBN 是真实情形）
      //    在库里是多条记录，直接打开第 1 条就会把"编第 2 册"误判成"重复"。统一弹窗，
      //    把该 ISBN 在库的全部记录（连同各自的卷册号）摆出来，由编目员二选一。
      const inLibrary = hits.filter((c: any) => c.record_id)
      if (inLibrary.length) {
        candList.value = inLibrary
        candSelected.value = inLibrary[0] ?? null   // 默认选中第 1 条：回车即"打开已有条目"
        if (page === 0) candFromQuick.value = false
        candPage.value = page
        candHasMore.value = hits.length >= (d.limit ?? 8)
        candDlg.value = true
        return
      }

      // 2) 资源库 / Z39.50 命中：都是"编新书"。同一本书常同时在资源库与多个外部源命中
      //    （内容基本一致），按归一 ISBN 去重，否则弹窗里会出现几条看起来一模一样的候选。
      const seen = new Set<string>()
      const uniq = hits.filter((c: any) => {
        // 用后端给的归一 ISBN（统一 13 位）：同一本书在不同源可能一个给 ISBN-10、
        // 一个给 ISBN-13，只靠前端去连字符是合并不掉的。
        const key = c.isbn_key || String(c.isbn ?? '').replace(/[^0-9Xx]/g, '').toUpperCase()
        if (!key) return true          // 无 ISBN 的不能当重复处理
        if (seen.has(key)) return false
        seen.add(key)
        return true
      })

      // 单条命中直接套录——但**只在第一页**：翻页时突然跳走会让人莫名其妙
      if (page === 0 && uniq.length === 1) {
        await onCataloged(uniq[0].record, uniq[0].title)
        return
      }
      // 多条命中：交给弹窗让用户挑（弹窗里展示完整字段）
      candList.value = uniq
      candSelected.value = null
      candPage.value = page
      candHasMore.value = hits.length >= (d.limit ?? 8)
      candDlg.value = true
    } catch (e: any) {
      ElMessage.error(apiErrorText(e, '套录'))
    } finally { looking.value = false }
  }

  /** 输入看起来是不是 ISBN：去掉连字符/空格后 10 位（末位可 X）或 13 位纯数字。 */
  const isIsbnLike = computed(() =>
    /^(?:\d{9}[\dXx]|\d{13})$/.test(isbnLookup.value.replace(/[-\s]/g, '')))

  const quickBusy = ref(false)

  /**
   * 「一键套录」：只认 ISBN，一次请求把 **查重 → 分级套录 → 联机源回存资源库
   * → 本地化临时副本** 全做完（`POST /api/quick-entry`）。
   *
   * 与 `lookupByIsbn` 的分工：那个是"题名/责任者也能搜、多源命中弹窗让你挑"的通用
   * 检索；这个是"手上有 ISBN、就想直接拿到可编目的底稿"的直取通道。界面合并为
   * 一个按钮：`primaryLookup` 按输入形态分流；Ctrl+Enter 强制走通用候选检索。
   *
   * 三种结果的处理：
   *   - `exists` → 问一句再跳（同书异校不算重，所以只说"当前图书库已有"）；
   *     不直接跳是因为编目员可能就想另编一册，跳走反而打断；
   *   - `miss`   → 明说"全部源未命中"，别让人以为按钮没反应；
   *   - `ok`     → 交给 `onQuickRecord`（**不能**复用 onCataloged，见其注释）。
   */
  /** 单按钮入口：ISBN 走直取底稿，题名/责任者走通用候选检索。 */
  async function primaryLookup() {
    if (isIsbnLike.value) return quickLookup()
    return lookupByIsbn()
  }

  async function quickLookup() {
    const isbn = isbnLookup.value.trim()
    if (!isbn || quickBusy.value || looking.value) return
    quickBusy.value = true
    try {
      const d = await quickEntry(isbn, currentLibId.value)
      if (d.status === 'exists') {
        const c = d.candidate
        if (c?.record) {
          // 走和通用套录框**同一个**候选弹窗：套录这条 / 打开这条 / 取消后手工新增。
          // 以前这里只弹「打开该条目 / 留在当前页」两个按钮——"留在当前页"是白纸
          // 重编，把已有那条的字段白白扔掉，同号不同书时尤其难受。
          candList.value = [c]
          candSelected.value = c
          candFromQuick.value = true
          candDlg.value = true
          return
        }
        // 后端没带完整记录（旧版后端）：退回二选一，至少不让流程断在这里
        const rid = Number(d.record_id)
        const title = d.detail?.title || isbn
        try {
          await ElMessageBox.confirm(`《${title}》已在当前图书库（记录 #${rid}）。`,
            '该 ISBN 已编目', {
              confirmButtonText: '打开该条目',
              cancelButtonText: '留在当前页',
              type: 'info',
            })
        } catch {
          return                       // 用户选择留下：可能是想另编一册
        }
        await router.push(`/admin/records/${rid}`)
        return
      }
      if (d.status === 'miss') {
        ElMessage.warning(d.hint || `ISBN ${isbn} 在图书库、资源库与联机源均未找到，请手工编目`)
        return
      }
      await onQuickRecord(d.record, d.hit_source)
    } catch (e: any) {
      ElMessage.error(apiErrorText(e, '快速套录'))
    } finally {
      quickBusy.value = false
    }
  }

  // 从候选弹窗里选定一条
  async function pickCandidate(c: any) {
    candDlg.value = false
    if (c.record_id) {          // 图书库候选：打开条目，不套录
      openLibraryRecord(c)
      return
    }
    // 只有"就地填充"（未入库）才由候选弹窗的 closed 事件送光标；
    // 已入库记录会另存为新记录并跳到「套录编辑」页，焦点由那边的 ?from=catalog 负责，别让这里抢。
    pendingFocusAfterDialog = isNew.value || isTemp.value
    looking.value = true
    try {
      await onCataloged(c.record, c.title, 0)
    } catch (e: any) {
      pendingFocusAfterDialog = false
      ElMessage.error(apiErrorText(e, '套录'))
    } finally { looking.value = false }
  }

  /** 弹窗里点一行：图书库候选是**选中**（还要再决定打开还是另编一册），
   *  其余源（资源库/Z39.50）沿用旧语义——点行即选用、直接套录。 */
  function onCandRowClick(c: any) {
    // 混合列表（图书库 + 外部源）：图书库行点了是**选中**（还要再决定加复本还是
    // 另编一册），外部源行点了就直接套录——外部候选没有"已有条目"可打开。
    if (c?.record_id) { candSelected.value = c; return }
    pickCandidate(c)
  }

  /**
   * 焦点落到卡片上 = 选中它（roving focus）。
   *
   * 只对**图书库候选**生效：外部源候选没有"选中态"，焦点扫过它不该产生副作用
   * （它一点就直接套录，见 `onCandRowClick`）。
   */
  function onCandFocus(c: any) {
    if (c?.record_id) candSelected.value = c
  }

  /**
   * 卡片上按回车 = **确认这条**。这是整套交互里唯一的"确定"语义：
   *
   *   - 图书库候选 → 打开已有条目（加复本）。想「另编一册」是**另一个意图**
   *     （基于这条新建，而不是采用这条），用方向键移到那个按钮再回车；
   *   - 外部源候选 → 套录这条（它没有"已有条目"可打开）。
   *
   * 为什么不让卡片回车只做"选中"：焦点在卡片上时按回车，用户心里想的是"就它了"。
   * 只改个高亮、什么都不发生，会被当成按键失灵。
   */
  function confirmCandidate(c: any) {
    if (!c) return
    if (c.record_id) {
      candSelected.value = c
      openSelectedLibrary()
    } else {
      void pickCandidate(c)
    }
  }

  /**
   * 「库里已有，但仍想看看外部源」——手动触发，不自动做。
   *
   * 为什么不自动：分层检索默认短路，图书库命中就不打 Z39.50，这是常态路径能秒回
   * 的原因。把它改成"永远全查"，等于每次撞号都多等一次外部网络往返（Z39.50 慢
   * 且不稳）。所以做成一个明确的动作：编目员觉得库内那条著录不可信（多年前手编、
   * 字段残缺），才点一下去拿外部著录来覆盖。
   */
  async function searchOtherSources(rawPage: number = 0) {
    // ⚠️ 模板里 `@click="searchOtherSources"` 会把 MouseEvent 当第一个参数传进来，
    // 直接拼进请求体会 422（page 期望整数）。这里统一收口，调用方怎么写都不会错。
    const page = Number.isFinite(rawPage) ? Math.max(0, Math.trunc(rawPage)) : 0
    const q = isbnLookup.value.trim()
    if (!q || otherBusy.value) return
    otherBusy.value = true
    try {
      const d = await catalogSearch({ q, library_id: currentLibId.value,
                                      all_tiers: true, page })
      const all = (d.candidates ?? []).filter((c: any) => c.record || c.record_id)
      candPage.value = page
      candHasMore.value = all.length >= (d.limit ?? 8)
      candHasExternal.value = true
      if (page > 0) {
        // 翻页：后端已经按窗口给好这一页，整页替换即可
        candList.value = all
        candSelected.value = all.find((c: any) => c.record_id) ?? null
        return
      }
      // 第一页：把外部候选并进当前（库内）列表，库内那几条要留着
      const external = all.filter((c: any) => !c.record_id)
      const known = new Set(
        candList.value.map((c: any) => `${c.source}|${c.key ?? c.title}`))
      let added = 0
      for (const c of external) {
        const k = `${c.source}|${c.key ?? c.title}`
        if (known.has(k)) continue
        candList.value.push(c)
        known.add(k)
        added += 1
      }
      if (!added) {
        ElMessage.info('资源库与 Z39.50 都没有其他来源的著录，可继续用库里这条')
      }
    } catch (e: any) {
      ElMessage.error(apiErrorText(e, '检索其他来源'))
    } finally {
      otherBusy.value = false
    }
  }

  /** 弹窗打开后聚焦主按钮「打开已有条目」：只有一条命中时回车即可打开（常用路径）。 */
  function onCandOpened() {
    if (!candInLibrary.value) return
    nextTick(() => {
      const el: any = (candOpenBtn.value as any)?.$el ?? candOpenBtn.value
      el?.focus?.()
    })
  }

  /**
   * 弹窗内方向键移动焦点。
   *
   * 编目员的实际动作是「敲 ISBN → 回车 → 选一条 → 回车」，中间不该被迫摸鼠标。
   * 具体"下一个是谁"的逻辑在 `candFocus.ts`（纯函数，单独可测）。
   */
  function onCandKeydown(e: KeyboardEvent) {
    if (moveFocus(document.querySelector('.cand-dlg'), e.key)) { e.preventDefault(); return }
    // 焦点落在底部按钮上时，回车 / 空格 = 按下这个按钮。
    //
    // 浏览器本来就会这么做（button 的默认动作），这里显式写出来有两个理由：
    //   1. 语义统一。卡片上「回车 = 确认这条」，按钮上「回车 = 执行这个动作」，
    //      编目员不用记"回车在哪些地方有用"。用户反馈过「选中后回车的逻辑很乱」。
    //   2. 默认动作不一定在每种环境里都发生（自动化驱动实测不触发），
    //      显式处理之后这条路径是可测的。
    // `preventDefault` 是为了掐掉浏览器的默认动作，否则会点两次。
    if (e.key === 'Enter' || e.key === ' ') {
      const active = document.activeElement as HTMLElement | null
      if (active?.tagName === 'BUTTON' && !(active as HTMLButtonElement).disabled) {
        e.preventDefault()
        active.click()
      }
    }
  }

  /** 图书库候选 → 打开选中的已有条目（加复本 / 改数据）。 */
  function openSelectedLibrary() {
    const c = candSelected.value
    if (!c?.record_id) return          // 选中的是外部源候选：没有条目可打开
    // ⚠️ 这里**只关弹窗，不跳转**。跳转放到 onCandDialogClosed。
    //
    // 为什么：Element Plus 的焦点陷阱在关闭过渡结束后会把焦点「归还」给打开前的
    // 那个元素，而那一下正好落在新页面加载完成之后——刚送进条码框的光标当场被抢走，
    // 实测 document.activeElement 变成一个 BUTTON（用户 2026-10-24 反馈「这还怎么
    // 加条码」）。无论怎么 setTimeout 补枪都是在跟它赛跑，6 次轮询也没压住。
    //
    // 改成等弹窗**彻底关完**再跳：那时焦点陷阱已经撤了，没有任何东西会再抢焦点。
    pendingNav = {
      rid: Number(c.record_id),
      title: c.title ?? '—',
      addCopy: true,                   // 这个按钮的语义就是「加复本」
    }
    muteLookupEnter()                  // 压住这次按键的 keyup（见 muteLookupEnter）
    candDlg.value = false
  }

  /** 图书库候选 → 作为**新记录**编目（同书不同卷册/版本，第 2 册起走这条）。 */
  async function catalogSelectedAsNew() {
    const c = candSelected.value
    if (!c?.record) return
    candDlg.value = false
    muteLookupEnter()                  // 同上：回车触发时 keyup 会打到套录框
    // 未入库页（新建 / 套录副本）就地填充并把光标送到条码框；已入库页一律另存为新记录
    pendingFocusAfterDialog = isNew.value || isTemp.value
    looking.value = true
    try {
      await onCataloged(c.record, c.title, 0)
    } catch (e: any) {
      pendingFocusAfterDialog = false
      ElMessage.error(apiErrorText(e, '套录'))
    } finally { looking.value = false }
  }

  /** 候选翻页：在"当前这条检索路径"上换一页，不改变检索词与来源范围。 */
  async function gotoCandPage(next: number) {
    if (next < 0) return
    if (looking.value || otherBusy.value) return
    if (candHasExternal.value) {
      await searchOtherSources(next)
      return
    }
    await lookupByIsbn(next)
  }

  // 候选弹窗完全关闭（关闭动画结束、焦点陷阱已撤）后才做后续动作
  function onCandDialogClosed() {
    candSelected.value = null
    candFromQuick.value = false
    candHasExternal.value = false
    candPage.value = 0
    candHasMore.value = false
    // 1) 「打开已有条目（加复本）」的跳转：此刻跳，新页面加载时焦点陷阱已撤，
    //    条码框的光标不会被归还动作抢走。
    if (pendingNav) {
      const nav = pendingNav
      pendingNav = null
      openLibraryRecord({ record_id: nav.rid, title: nav.title },
                        { addCopy: nav.addCopy })
      return
    }
    // 2) 留在本页的情形（新建 / 套录副本）：光标送到条码框
    if (!pendingFocusAfterDialog) return
    pendingFocusAfterDialog = false
    barcodeInput.value?.focus?.()
  }

  /** 换记录复位（RecordEdit.load() 的状态清空）。 */
  function resetLookup() {
    isbnLookup.value = ''
    candDlg.value = false
    candList.value = []
    pendingFocusAfterDialog = false
    pendingNav = null
  }

  return {
    isbnLookup, looking, candDlg, candList, candInLibrary, candSelected, candOpenBtn,
    lookupByIsbn, openLibraryRecord, pickCandidate, onCandRowClick, onCandOpened,
    onCandKeydown, onCandFocus, confirmCandidate,
    openSelectedLibrary, catalogSelectedAsNew, onCandDialogClosed, resetLookup,
    lookupEnterMuted,
    isIsbnLike, quickBusy, quickLookup, primaryLookup, candFromQuick,
    candHasExternal, candExternalCount, otherBusy, searchOtherSources,
    candPage, candHasMore, gotoCandPage,
  }
}
