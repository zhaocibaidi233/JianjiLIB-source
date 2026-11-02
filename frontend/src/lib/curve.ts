/**
 * 折线图的平滑路径（Catmull-Rom → 三次贝塞尔）加**单调钳制**。
 *
 * 无钳制的 Catmull-Rom 在尖峰远高于邻点时会让曲线冲出实际值（控制点由
 * 邻点差分推出，峰越尖冲得越高），视觉上画出"没发生过的数据"——借还数
 * 是要被馆员读的，所以每段曲线的控制点 y 一律钳回本段两端点之间。
 * 贝塞尔曲线必落在控制点凸包内，钳住控制点即保证曲线不过冲（x 方向均匀
 * 步进下控制点 x 本就不会越段，无需处理）。
 */
export function smoothPath(points: Array<[number, number]>): string {
  if (points.length < 2) return points.length ? `M ${points[0][0]},${points[0][1]}` : ''
  let d = `M ${points[0][0]},${points[0][1]}`
  for (let i = 0; i < points.length - 1; i += 1) {
    const p0 = points[i - 1] || points[i]
    const p1 = points[i]
    const p2 = points[i + 1]
    const p3 = points[i + 2] || p2
    const lo = Math.min(p1[1], p2[1])
    const hi = Math.max(p1[1], p2[1])
    const clamp = (y: number) => Math.min(hi, Math.max(lo, y))
    const cp1x = p1[0] + (p2[0] - p0[0]) / 6
    const cp1y = clamp(p1[1] + (p2[1] - p0[1]) / 6)
    const cp2x = p2[0] - (p3[0] - p1[0]) / 6
    const cp2y = clamp(p2[1] - (p3[1] - p1[1]) / 6)
    d += ` C ${cp1x},${cp1y} ${cp2x},${cp2y} ${p2[0]},${p2[1]}`
  }
  return d
}
