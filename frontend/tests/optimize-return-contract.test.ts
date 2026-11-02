import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ref } from 'vue'

/**
 * 「打开记录即自动规范」的返回值契约（RecordEdit.load 依赖它重立保存基线）。
 *
 * 背景（2026-10-24 用户反馈）：点进编辑页必现「200 字段 ISBD 标点已按规范补齐」
 * 且被判定为未保存。ISBD 那一条已在后端改为不回报；这里锁住"结果是否装入"的
 * 信号，load() 拿它决定要不要 markSavedBaseline()——没装上就不该动基线。
 */

const post = vi.hoisted(() => vi.fn())
vi.mock('../src/legacy/api', () => ({ http: { post }, apiErrorText: () => '请求失败' }))
vi.mock('element-plus', () => ({
  ElMessage: { success: vi.fn(), info: vi.fn(), error: vi.fn() },
}))

const { useOptimize } = await import('../src/legacy/composables/useOptimize')

function harness(opts: { install?: boolean; mode?: 'auto' | 'manual'; temp?: boolean } = {}) {
  const { install = true, mode = 'auto', temp = false } = opts
  const installed: any[] = []
  const optimizing = ref(false)
  let seq = 0
  const u = useOptimize({
    optimizing,
    // 守卫简化成"装不装"开关：真实实现的四重校验在 useReplacementGuard 里另有测试
    guard: {
      beginReplacement: () => ({ identity: 'r', seq: ++seq, version: 0 }),
      replacementIsCurrent: () => install,
      isLatestSeq: () => true,
    },
    getPayload: () => ({ leader: null, fields: [] }),
    installResult: (d: any) => installed.push(d),
    isNew: () => false,
    isTemp: () => temp,
  })
  u.optimizeMode.value = mode
  return { u, installed, optimizing }
}

beforeEach(() => {
  post.mockReset()
  post.mockResolvedValue({ data: { fields: [], leader: null,
    applied: ['200 字段 ISBD 标点已按规范补齐/清理'], findings: [] } })
})

describe('autoOptimizeAfterLoad 的返回契约', () => {
  it('结果装入 → 返回 true（load 据此重立基线，不算未保存）', async () => {
    const { u, installed } = harness()
    await expect(u.autoOptimizeAfterLoad(false)).resolves.toBe(true)
    expect(installed).toHaveLength(1)
    expect(u.optApplied.value).toHaveLength(1)
  })

  it('替换守卫作废 → 返回 false，且不安装结果', async () => {
    const { u, installed } = harness({ install: false })
    await expect(u.autoOptimizeAfterLoad(false)).resolves.toBe(false)
    expect(installed).toHaveLength(0)
  })

  it('manual 模式 → 不请求、返回 false', async () => {
    const { u } = harness({ mode: 'manual' })
    await expect(u.autoOptimizeAfterLoad(false)).resolves.toBe(false)
    expect(post).not.toHaveBeenCalled()
  })

  it('临时底稿即使无载荷也跑（走 tempPayload 分支）', async () => {
    const { u } = harness({ temp: true })
    await expect(u.autoOptimizeAfterLoad(true)).resolves.toBe(true)
    expect(post).toHaveBeenCalledTimes(1)
  })
})
