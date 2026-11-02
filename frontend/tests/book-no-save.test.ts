import { describe, expect, it } from 'vitest'
import { bookNoForSave } from '../src/legacy/volume'

// 保存载荷的书次号口径（2026-10-25 书次号链路审查）。
// 背景：前端把 callno-preview 的**预览号**自动回填进书次号框，保存时若照值回传，
// 后端会走手动号路径——预览值在「打开表单 → 保存」窗口内可能已被别的终端占用，
// 原子取号被绕回「静默异书同号」的老问题。规则：新建/底稿未手敲 → 不传号。

describe('bookNoForSave', () => {
  it('suppresses untouched preview values on new records and temp drafts', () => {
    expect(bookNoForSave({ creating: true, touched: false, value: '8' })).toBeUndefined()
    expect(bookNoForSave({ creating: true, touched: false, value: ' 8 ' })).toBeUndefined()
    expect(bookNoForSave({ creating: true, touched: false, value: '' })).toBeUndefined()
    expect(bookNoForSave({ creating: true, touched: false, value: null })).toBeUndefined()
  })

  it('sends user-typed values (including author designations) on new records', () => {
    expect(bookNoForSave({ creating: true, touched: true, value: 'L824/2' })).toBe('L824/2')
    expect(bookNoForSave({ creating: true, touched: true, value: '8' })).toBe('8')
  })

  it('treats a cleared box as "use auto numbering" even when touched', () => {
    expect(bookNoForSave({ creating: true, touched: true, value: '' })).toBeUndefined()
  })

  it('keeps existing records idempotent: loaded numbers go back as-is', () => {
    expect(bookNoForSave({ creating: false, touched: false, value: '8' })).toBe('8')
    expect(bookNoForSave({ creating: false, touched: false, value: ' L824/2 ' })).toBe('L824/2')
    // 已有记录清空书次号 = 显式重排（后端会按 690 重算并抬计数器），照传空即"不指定"
    expect(bookNoForSave({ creating: false, touched: false, value: '' })).toBeUndefined()
  })
})
