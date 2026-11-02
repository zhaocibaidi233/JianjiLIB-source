/**
 * CNMARC `100$a` 第 **17-19 位**「阅读对象代码」。
 *
 * 来源：**GB/T 33286—2016《中国机读书目格式》** 7.3 的 100 字段原文
 * （「(5)阅读对象代码(字符位置 17~19)：用三位字符代码表示使用对象。
 *  **从左至右顺序填写，不足三位时用空位表示**」）：
 *
 *   a=普通青少年（不用或不能用 b、c、d、e 时用此代码） / b=学龄前儿童（0~5岁）
 *   c=小学生（5~10岁） / d=少年（9~14岁） / e=青年（14~20岁）
 *   k=科研人员 / m=普通成人 / u=不详
 *
 * ⚠️ **这是标准固定表，不是本馆可改的字典**，所以直接硬编在这里 ——
 * 与「尽量不自己编」不冲突：**编的是标准的原表**，不是我们猜的候选。
 * （2026-10-09 之前这一格是**手填**，理由正是"标准代码表我们没取到"；现在拿到了。）
 *
 * ⚠️ 该位是**定长 3 字符**且**可以多选**（实测真实数据里 `em ` = 青年+普通成人、
 * `cd ` = 小学生+少年），所以界面上是**多选**下拉，不是单选。
 */

export interface ReaderLevel {
  code: string
  name: string
}

export const READER_LEVELS: ReaderLevel[] = [
  { code: 'a', name: '普通青少年' },
  { code: 'b', name: '学龄前儿童（0~5岁）' },
  { code: 'c', name: '小学生（5~10岁）' },
  { code: 'd', name: '少年（9~14岁）' },
  { code: 'e', name: '青年（14~20岁）' },
  { code: 'k', name: '科研人员' },
  { code: 'm', name: '普通成人' },
  { code: 'u', name: '不详' },
]

/** 最多 3 位（标准：三位字符代码，从左至右顺序填写）。 */
export const READER_LEVEL_MAX = 3

/** 代码数组 → 3 位定长串（不足补空格、超长截断）。
 *
 * 口径与后端 `api/records.py::_apply_reader_level` **一致**（那边是
 * `level.strip().ljust(3)[:3]`）—— 两端都写 3 位，别一边带空格一边不带。
 */
export function joinReaderLevel(codes: string[]): string {
  return codes.join('').slice(0, READER_LEVEL_MAX).padEnd(READER_LEVEL_MAX, ' ')
}

/** 3 位定长串 → 代码数组（去掉补位空格）。
 *
 * 后端 `_reader_level_of()` 返回的是**已 strip** 的形态（`em ` → `em`），
 * 但手写进库的脏值可能带空格，所以这里再兜一层 `\s` 清理。
 */
export function splitReaderLevel(v: string | null | undefined): string[] {
  return (v ?? '').replace(/\s+/g, '').slice(0, READER_LEVEL_MAX).split('')
}

/** 阅读对象代码串 → 名称列表（未知代码保留原值）。 */
export function readerLevelLabel(v: string | null | undefined): string {
  const names = splitReaderLevel(v).map(
    (code) => READER_LEVELS.find((item) => item.code === code)?.name || code,
  )
  return names.join('、') || '—'
}
