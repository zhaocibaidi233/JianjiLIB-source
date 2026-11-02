import { ref } from 'vue'

/**
 * 质检未通过（422）的处理出路（RecordEdit 保存门槛）。
 *
 * 保存被质检门槛拦下时弹出：列清 ERROR、给出返回修改 / 强制入库 / 自动修复
 * 三条路。之前只弹一句 toast 提示"可点优化"，但套录副本（isNew）下优化要求
 * 先入库，形成"保存失败→点优化→请先保存"的死循环，用户彻底卡死——所以有
 * 这台状态机而不是一条 toast。
 *
 * 重试闭包带着**打开弹窗那一刻**的记录身份：期间切了记录（identity 变化）或
 * 组件已卸载，强制入库就作废——旧记录的 force 请求打到新记录上不可接受。
 */
export function useQualityGate(opts: {
  /** 当前记录身份（RecordEdit 的 recordIdentity） */
  recordIdentity: () => string
  isDisposed: () => boolean
  /**
   * 「从门槛里点优化」走的优化流程（RecordEdit.runOptimize）。
   *
   * 返回类型写成 `Promise<unknown>` 而不是 `Promise<void>`：门槛只关心「跑完
   * 没有」，**不消费**返回值，而 `runOptimize` 的布尔返回值（是否已应用）
   * 在别处被使用。把入参类型放宽，比在调用处包一层 `.then(() => {})` 或改
   * `runOptimize` 的签名都更诚实——它本来就不该对返回值的形状提要求。
   */
  runOptimize: () => Promise<unknown>
}) {
  const gateDlg = ref(false)
  const gateErrors = ref<any[]>([])
  let gateRetry: (() => Promise<void>) | null = null

  function openGate(errs: any[], retry: () => Promise<void>) {
    const identity = opts.recordIdentity()
    gateErrors.value = errs
    gateRetry = async () => {
      if (!opts.isDisposed() && identity === opts.recordIdentity()) await retry()
    }
    gateDlg.value = true
  }

  /** 换记录 / 重新加载时清门槛：关弹窗、清错误、作废挂着的重试闭包。 */
  function resetGate() {
    gateDlg.value = false
    gateErrors.value = []
    gateRetry = null
  }

  async function forceSave() {
    if (!gateRetry) return
    gateDlg.value = false
    await gateRetry()
  }

  async function optimizeFromGate() {
    gateDlg.value = false
    await opts.runOptimize()
  }

  return { gateDlg, gateErrors, openGate, forceSave, optimizeFromGate, resetGate }
}
