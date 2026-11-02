import { ElMessage } from 'element-plus'
import type { Ref } from 'vue'
import { lookupPlaceByPublisher, lookupPublisherByIsbn } from '../api'

/**
 * 按 ISBN / 出版者**反查预填**（对标老系统：输入 ISBN 后出版者/出版地自动给出）。
 *
 * 定位是**建议**不是校验——前缀表只有 1392 条，且可能被更短的出版者代码抢中
 * （`7-100` 商务印书馆 会抢 `7-1000…`）。所以：
 *   - 字段为空才填；
 *   - 已有值且与建议不一致 → 只提示、**不覆盖**（编目员的判断优先）；
 *   - 同一个 ISBN 只查一次（失焦会多次触发）；
 *   - 反查是"锦上添花"，失败不能打断编目。
 *
 * 两个方向都带「身份 + 版本」双守卫：响应回来时记录已切换或内容已变，
 * 就不再往 `simple` 里写（旧响应覆盖新输入是不可接受的）。
 */
export function useIsbnPrefill(opts: {
  /** simpleForm 的投影（响应式对象；直接读写 `010a` / `210a` / `210c`） */
  simple: Record<string, string>
  recordIdentity: () => string
  editVersion: Ref<number>
  isDisposed: () => boolean
}) {
  const { simple, recordIdentity, editVersion, isDisposed } = opts

  // 出版者反查的"同一个号只查一次"标记（失焦会多次触发，避免重复请求与重复提示）
  let lastPublisherLookup = ''

  async function fillPublisherFromIsbn(rawIsbn: string) {
    const isbn = (rawIsbn ?? '').trim()
    if (!isbn || isbn === lastPublisherLookup) return
    lastPublisherLookup = isbn
    const identity = recordIdentity()
    const version = editVersion.value
    try {
      const d = await lookupPublisherByIsbn(isbn)
      if (isDisposed() || identity !== recordIdentity() || version !== editVersion.value || !d.found) return
      const curPub = (simple['210c'] ?? '').trim()
      const curPlace = (simple['210a'] ?? '').trim()
      if (curPub && curPub !== d.publisher) {
        ElMessage.warning(
          `ISBN 前缀指向「${d.publisher}（${d.place}）」，当前填的是「${curPub}」，请核对`)
        return
      }
      const filled: string[] = []
      if (!curPub) { simple['210c'] = d.publisher; filled.push('出版者') }
      if (!curPlace && d.place) { simple['210a'] = d.place; filled.push('出版地') }
      if (filled.length) {
        ElMessage.success(`已按 ISBN 自动填写${filled.join('、')}：${d.publisher} / ${d.place}`)
      }
    } catch {
      // 反查是"锦上添花"，失败不能打断编目
    }
  }

  /**
   * 填了出版者、出版地还空着 → 按权威表补出出版地。
   * 与「一键规范」用的是同一张表（老系统的出版者字典），
   * 但这里是在**输入当下**就补，省得编目员等到保存前才发现。
   */
  async function fillPlaceFromPublisher() {
    const name = (simple['210c'] ?? '').trim()
    if (!name || (simple['210a'] ?? '').trim()) return
    const identity = recordIdentity()
    const version = editVersion.value
    try {
      const d = await lookupPlaceByPublisher(name)
      if (isDisposed() || identity !== recordIdentity() || version !== editVersion.value) return
      if (d.found && d.place) {
        simple['210a'] = d.place
        ElMessage.success(`已按出版者补出出版地：${d.place}`)
      }
    } catch { /* 锦上添花，失败不打断编目 */ }
  }

  /** 简编表单失焦钩子：只有这两个字段需要联动。 */
  async function onSimpleBlur(b: { tag: string; code: string }) {
    if (b.tag === '010' && b.code === 'a') {
      await fillPublisherFromIsbn(simple['010a'] ?? '')
      return
    }
    if (b.tag === '210' && b.code === 'c') await fillPlaceFromPublisher()
  }

  /** 换记录 / 安装套录底稿时清「同号只查一次」标记（新记录要能重新反查）。 */
  function resetPrefill() {
    lastPublisherLookup = ''
  }

  return { fillPublisherFromIsbn, fillPlaceFromPublisher, onSimpleBlur, resetPrefill }
}
