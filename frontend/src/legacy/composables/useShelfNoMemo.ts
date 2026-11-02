import type { ComputedRef } from 'vue'
import { stepShelfNo } from '../volume'

/** 录入面板的记录级字段子集（RecordEdit 的 extra reactive 对象）。 */
interface Extra {
  volume: string
  volumeSeq: number | null
  shelfNo: string
}

/**
 * 排架号记忆与框尾 ± 步进。
 *
 * 记忆**按图书库分别记**（换库不串），存 localStorage——与「馆藏地点」「语种」
 * 同属**个人作业习惯**，不发请求、不跨机，也不该被别的编目员顺手改掉。
 * 键里带图书库 id；`currentLibId` 可能为空（列表页还没选库）→ 用 'default' 兜底。
 */
export function useShelfNoMemo(opts: {
  extra: Extra
  currentLibId: ComputedRef<number | undefined>
}) {
  const { extra, currentLibId } = opts
  const SHELF_NO_MEMO_KEY = 'cataloging_last_shelf_no'
  const shelfNoMemoKey = (): string => `${SHELF_NO_MEMO_KEY}:${currentLibId.value ?? 'default'}`

  /** 本图书库上次保存过的排架号；读不到 / 隐私模式 → 空串（不打断编目）。 */
  function rememberedShelfNo(): string {
    try { return (localStorage.getItem(shelfNoMemoKey()) ?? '').trim() } catch { return '' }
  }

  /**
   * 保存成功后记住这次用的排架号。
   *
   * **只在非空时写**：记忆要保住"最近一次真正用过的架位"。若空值也写，用户给一批不入架的
   * 书保存一次（或临时清空一下），记忆就被抹掉了，下次预填变成空 —— 那不是"记住上次"。
   */
  function rememberShelfNo() {
    const v = (extra.shelfNo ?? '').trim()
    if (!v) return
    try { localStorage.setItem(shelfNoMemoKey(), v) } catch { /* 隐私模式：只在本次会话生效 */ }
  }

  /** 该方向能不能步进（按钮 `disabled` 用）：无结尾数字 / 会减到 0 → 不可动。 */
  const shelfNoCanStep = (delta: number): boolean => stepShelfNo(extra.shelfNo, delta) !== null

  /** 框尾 ± ：把排架号结尾的数字加减一（`G-1` ⇄ `G-2`）。不可动时静默不动。 */
  function applyShelfNoStep(delta: number) {
    const next = stepShelfNo(extra.shelfNo, delta)
    if (next !== null) extra.shelfNo = next
  }

  return { shelfNoMemoKey, rememberedShelfNo, rememberShelfNo, shelfNoCanStep, applyShelfNoStep }
}
