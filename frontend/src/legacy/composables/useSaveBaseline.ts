import { computed, ref } from 'vue'

/**
 * 保存基线 / 脏状态机（RecordEdit 的 `savedBaseline` 一族）。
 *
 * "脏不脏"的判定只有一条：**当前编辑快照 ≠ 上次保存时的快照**（isDirty）。
 * 基线是一份 JSON 快照字符串，保存流程对它有 5 处**外科手术式直写**——
 * 第三/四刀抽 useReplacementGuard / useOptimize 时刻意把它们留在组件
 * （"收进 composable 只会把 ref 原样漏回去"），本 composable 就是那笔债的
 * 收口：每处直写都变成**有名字的操作**，口径写在各操作的注释里。
 *
 * 快照的序列化（`editSnapshot`）**刻意留在组件**——它要读全部编辑态
 * （fields / extra / location / barcode / priceDraft），经 `snapshot` 注入。
 *
 * 另一个刻意的例外：`draftPendingSave` 让"有内容的套录底稿"即使刚加载完
 * 也算脏（离开要确认）——它不是快照能比出来的差异，是**身份级**的未保存。
 */
export function useSaveBaseline(opts: {
  /** 编辑快照序列化（组件的 editSnapshot：全部编辑态 → JSON 字符串）。 */
  snapshot: () => string
}) {
  const savedBaseline = ref<string | null>(null)
  // 有内容的套录底稿尚未入库，即使刚加载也需要离开确认（只经 flagDraftPending
  // 写、isDirty 读，不对外暴露）
  const draftPendingSave = ref(false)

  const isDirty = computed(() => savedBaseline.value !== null
    && (draftPendingSave.value || opts.snapshot() !== savedBaseline.value))

  /** 保存成功 / 初载完成：以当前内容为基线。 */
  function markSavedBaseline() { savedBaseline.value = opts.snapshot() }

  /** 换记录 / 重新加载：基线清空（未加载完不算脏），底稿标记一并复位。 */
  function resetBaseline() {
    savedBaseline.value = null
    draftPendingSave.value = false
  }

  /** 套录底稿带着自己的基线过来（sessionStorage 恢复路径），不能按空表单算脏。 */
  function adoptBaseline(baseline: string) { savedBaseline.value = baseline }

  /** 底稿标记的置位 / 复位：载入或安装了套录底稿置 true，底稿入库后清掉。 */
  function flagDraftPending(v = true) { draftPendingSave.value = v }

  /**
   * 提交后按**服务端口径**重立基线。入参是调用方已改好的快照对象——
   * 保存流程的"外科手术"（清掉已入库的 barcode、换成服务端回写的
   * priceDraft、转入新记录身份的 fields/leader）都在调用方完成，
   * 这里只负责落基线这一下。
   */
  function commitBaseline(snapshotObj: unknown) {
    savedBaseline.value = JSON.stringify(snapshotObj)
  }

  /**
   * 馆藏行编辑（saveHolding）的基线手术：重立基线但**保住旧基线的 barcode**。
   * 行编辑不碰录入面板的条码框——若用快照里的 barcode，面板里待录的下一册
   * 条码会被误判成"未保存修改"。
   */
  function preserveOldBarcode() {
    const baseline = JSON.parse(opts.snapshot())
    baseline.barcode = savedBaseline.value ? JSON.parse(savedBaseline.value).barcode : ''
    savedBaseline.value = JSON.stringify(baseline)
  }

  return { savedBaseline, isDirty, markSavedBaseline, resetBaseline,
           adoptBaseline, flagDraftPending, commitBaseline, preserveOldBarcode }
}
