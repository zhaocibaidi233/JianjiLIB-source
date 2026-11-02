import { describe, expect, it } from 'vitest'
import { smoothPath } from '../src/lib/curve'

/** 从路径串里取出每段 `C cp1x,cp1y cp2x,cp2y x,y` 的控制点与终点。 */
function segments(d: string) {
  return [...d.matchAll(/C\s+([\d.]+),([\d.]+)\s+([\d.]+),([\d.]+)\s+([\d.]+),([\d.]+)/g)]
    .map(m => ({
      cp1: [Number(m[1]), Number(m[2])] as [number, number],
      cp2: [Number(m[3]), Number(m[4])] as [number, number],
      to: [Number(m[5]), Number(m[6])] as [number, number],
    }))
}

describe('smoothPath（单调钳制）', () => {
  it('少於两点：单点返回 M 指令，空数组返回空串', () => {
    expect(smoothPath([])).toBe('')
    expect(smoothPath([[10, 20]])).toBe('M 10,20')
  })

  it('控制点 y 一律钳在本段两端点之间——尖峰不再过冲', () => {
    // 尖峰：第 3 点远高于两侧（屏幕坐标 y 越小越高）
    const points: Array<[number, number]> =
      [[0, 100], [100, 100], [200, 20], [300, 100], [400, 100]]
    const segs = segments(smoothPath(points))
    expect(segs).toHaveLength(points.length - 1)
    segs.forEach((s, i) => {
      // 第 i 段从 points[i] 到 points[i+1]，控制点 y 不得越出这两点
      const lo = Math.min(points[i][1], points[i + 1][1])
      const hi = Math.max(points[i][1], points[i + 1][1])
      expect(s.cp1[1]).toBeGreaterThanOrEqual(lo)
      expect(s.cp1[1]).toBeLessThanOrEqual(hi)
      expect(s.cp2[1]).toBeGreaterThanOrEqual(lo)
      expect(s.cp2[1]).toBeLessThanOrEqual(hi)
      expect(s.to).toEqual(points[i + 1])
    })
  })

  it('平直序列保持平直（钳制不引入抖动）', () => {
    const points: Array<[number, number]> =
      [[0, 50], [100, 50], [200, 50], [300, 50]]
    for (const s of segments(smoothPath(points))) {
      expect(s.cp1[1]).toBe(50)
      expect(s.cp2[1]).toBe(50)
      expect(s.to[1]).toBe(50)
    }
  })

  it('平滑段（非尖峰）钳制不改动差分控制点', () => {
    // 单调序列上差分推出的控制点本就在段内，钳制应原样保留
    const points: Array<[number, number]> =
      [[0, 80], [100, 60], [200, 40], [300, 30]]
    const segs = segments(smoothPath(points))
    // 第 2 段 cp1 = p1 + (p2 - p0) / 6 = (100,60) + ((200,40)-(0,80))/6
    expect(segs[1].cp1[0]).toBeCloseTo(100 + (200 - 0) / 6)
    expect(segs[1].cp1[1]).toBeCloseTo(60 + (40 - 80) / 6)
  })
})
