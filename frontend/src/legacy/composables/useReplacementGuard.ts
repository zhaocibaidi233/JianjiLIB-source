import type { Ref } from 'vue'

/** 一次替换/异步动作的令牌：开启那一刻的记录身份、全局序号、内容版本。 */
export interface ReplacementRequest {
  identity: string
  seq: number
  version: number
}

/**
 * 替换协议的令牌机（RecordEdit `beginReplacement` 一族的唯一实现）。
 *
 * RecordEdit 里所有"异步改写整份编辑态"的动作（套录安装、规则优化、保存后
 * 重载……）都遵循同一套四重守卫，缺一不可：
 *
 *   1. **身份** —— 记录没换（`recordIdentity`：路由 id + epoch + 图书库）；
 *   2. **序号** —— 没有更新的替换动作抢先后（每次 begin 自增，旧请求整体作废）；
 *   3. **版本** —— 内容没被用户改过（editVersion 由编辑态 watch 逐字驱动）；
 *   4. **存活** —— 组件没卸载。
 *
 * 旧响应晚到时 `replacementIsCurrent` 为 false，调用方必须整体丢弃——
 * 把旧数据写进新记录/新内容是不可接受的。
 *
 * ⚠️ **脏状态（savedBaseline / draftPendingSave）刻意不在本 composable 里**：
 * 保存流程对基线有 5 处外科手术式直写（如"提交后基线保住旧 barcode"），
 * 那是组件形状的状态；硬收进来只会把 ref 原样漏回去，白多一层。
 */
export function useReplacementGuard(opts: {
  recordIdentity: () => string
  isDisposed: () => boolean
  editVersion: Ref<number>
  /** 开启新替换 / 作废旧替换时的组件侧副作用（关掉 optimizing 转圈等） */
  onReset?: () => void
}) {
  let replacementSeq = 0

  function beginReplacement(): ReplacementRequest {
    opts.onReset?.()
    return { identity: opts.recordIdentity(), seq: ++replacementSeq,
             version: opts.editVersion.value }
  }

  /** 四项全对才放行（见类注释 1-4）。 */
  function replacementIsCurrent(request: ReplacementRequest): boolean {
    return !opts.isDisposed() && request.identity === opts.recordIdentity()
      && request.seq === replacementSeq && request.version === opts.editVersion.value
  }

  /**
   * 只比序号：请求期间用户敲了字（version 变了）**不算**过期——供 finally 里
   * "关掉自己那次动作的转圈"用。用 replacementIsCurrent 判会误伤，转圈永远
   * 停不下来。
   */
  function isLatestSeq(request: ReplacementRequest): boolean {
    return request.seq === replacementSeq
  }

  /** 作废所有在途替换（换记录 / 底稿替换后开启新篇）。 */
  function invalidateReplacements() {
    replacementSeq += 1
    opts.onReset?.()
  }

  return { beginReplacement, replacementIsCurrent, isLatestSeq, invalidateReplacements }
}
