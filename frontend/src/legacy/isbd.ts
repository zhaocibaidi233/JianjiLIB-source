/**
 * ISBD 标点在**展示层**的剥离规则。
 *
 * CNMARC 的 ISBD 标点归属于**前一个子字段的末尾**：
 *   `200 $a红楼梦 /$f曹雪芹著`  → `$a` 存 `"红楼梦 / "`
 *   `200 $a中国哲学史 :$e先秦卷` → `$a` 存 `"中国哲学史 : "`
 * 后端 `core/isbd.py::unify_isbd` 按规范补这些尾标点，**数据本身是对的**。
 *
 * 但简编表单把「正题名 / 副题名 / 责任者」拆成三个独立输入框，
 * 此时尾标点在界面上是**冗余且误导**的——用户会以为 `/` 是题名的一部分。
 * 所以：**存储层遵循标准，展示层贴合 UI 语义**（入库时由清洗管线补回）。
 *
 * 只剥 ASCII 的 `:` `/`，与后端 `core/isbd.py::strip_isbd_tail` 保持一致。
 * 全角 `：`（如《北京：中轴线》）是题名内容，不剥。
 */
export function stripIsbdTail(v: string | null | undefined): string {
  let s = (v ?? '').replace(/\s+$/, '')
  while (s && (s.endsWith(':') || s.endsWith('/'))) {
    s = s.slice(0, -1).replace(/\s+$/, '')
  }
  return s
}
