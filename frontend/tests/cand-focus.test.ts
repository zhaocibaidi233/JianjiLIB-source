import { beforeEach, describe, expect, it } from 'vitest'
import { collectFocusables, moveFocus, nextFocusIndex } from '../src/legacy/composables/candFocus'

/**
 * 候选弹窗的方向键焦点游走。
 *
 * 用户 2026-10-24：「这个页面加上方向键调整焦点」。编目员的动作是
 * 「敲 ISBN → 回车 → 选一条 → 回车」，中间不该被迫摸鼠标。
 */

describe('nextFocusIndex', () => {
  it('焦点不在列表里时，往下从第一个进、往上从最后一个进', () => {
    expect(nextFocusIndex(3, -1, true)).toBe(0)
    expect(nextFocusIndex(3, -1, false)).toBe(2)
  })

  it('往下走到底回到头，往上走到头回到尾', () => {
    expect(nextFocusIndex(3, 0, true)).toBe(1)
    expect(nextFocusIndex(3, 2, true)).toBe(0)
    expect(nextFocusIndex(3, 0, false)).toBe(2)
  })

  it('空列表返回 -1（调用方据此跳过）', () => {
    expect(nextFocusIndex(0, -1, true)).toBe(-1)
  })
})

function buildDialog(): HTMLElement {
  const root = document.createElement('div')
  root.className = 'cand-dlg'
  root.innerHTML = `
    <div class="el-dialog__body">
      <div class="cand-more"><button id="more" data-cand-focus>同时检索资源库</button></div>
      <div class="cand-item" id="c1" data-cand-focus tabindex="0">甲</div>
      <div class="cand-item" id="c2" data-cand-focus tabindex="0">乙</div>
    </div>
    <div class="el-dialog__footer">
      <button id="b-cancel">取消</button>
      <button id="b-new">作为新记录编目</button>
      <button id="b-open" disabled>打开已有条目</button>
    </div>`
  document.body.appendChild(root)
  return root
}

describe('collectFocusables', () => {
  beforeEach(() => {
    document.body.innerHTML = ''
  })

  it('按视觉顺序收集：扩展检索 → 候选 → 页脚按钮', () => {
    const root = buildDialog()
    const ids = collectFocusables(root).map((el) => el.id)
    expect(ids).toEqual(['more', 'c1', 'c2', 'b-cancel', 'b-new'])
  })

  it('跳过 disabled 的按钮', () => {
    const root = buildDialog()
    expect(collectFocusables(root).map((el) => el.id)).not.toContain('b-open')
  })

  it('root 为空时返回空数组，不抛异常', () => {
    expect(collectFocusables(null)).toEqual([])
  })
})

describe('moveFocus', () => {
  beforeEach(() => {
    document.body.innerHTML = ''
  })

  it('方向键能在候选与按钮之间移动焦点', () => {
    const root = buildDialog()
    const c1 = document.getElementById('c1')!
    c1.focus()

    expect(moveFocus(root, 'ArrowDown')).toBe(true)
    expect(document.activeElement?.id).toBe('c2')

    expect(moveFocus(root, 'ArrowDown')).toBe(true)
    expect(document.activeElement?.id).toBe('b-cancel')

    expect(moveFocus(root, 'ArrowUp')).toBe(true)
    expect(document.activeElement?.id).toBe('c2')
  })

  it('←→ 与 ↑↓ 等价', () => {
    const root = buildDialog()
    document.getElementById('c1')!.focus()
    moveFocus(root, 'ArrowRight')
    expect(document.activeElement?.id).toBe('c2')
    moveFocus(root, 'ArrowLeft')
    expect(document.activeElement?.id).toBe('c1')
  })

  it('末尾继续往下会回到第一个', () => {
    const root = buildDialog()
    document.getElementById('b-new')!.focus()
    moveFocus(root, 'ArrowDown')
    expect(document.activeElement?.id).toBe('more')
  })

  it('其它按键不拦（返回 false，让浏览器照常处理）', () => {
    const root = buildDialog()
    expect(moveFocus(root, 'Enter')).toBe(false)
    expect(moveFocus(root, 'Tab')).toBe(false)
  })

  it('弹窗没打开时什么都不做', () => {
    expect(moveFocus(null, 'ArrowDown')).toBe(false)
  })
})
