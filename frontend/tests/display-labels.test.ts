import { describe, expect, it } from 'vitest'
import {
  holdingLocationLabel,
  holdingStatusLabel,
  readerStatusLabel,
  sourceLabel,
} from '../src/lib/displayLabels'
import { readerLevelLabel } from '../src/legacy/readerLevel'

describe('display labels', () => {
  it('maps known sources and keeps custom values', () => {
    expect(sourceLabel('demo')).toBe('演示数据')
    expect(sourceLabel('LOCAL')).toBe('本馆编目')
    expect(sourceLabel('Z39.50')).toBe('Z39.50')
    expect(sourceLabel(null)).toBe('—')
  })

  it('never exposes an unknown holding status code', () => {
    expect(holdingStatusLabel('AVAILABLE')).toBe('在架')
    expect(holdingStatusLabel('BINDING')).toBe('未知状态')
  })

  it('labels SUSPENDED as 停流通, never as 注销 or 遗失', () => {
    // 契约 D14：停流通/另库保存不是注销，也不是遗失；三者不能共用一个文案，
    // 否则馆员分不清"这册为什么不能借"。
    expect(holdingStatusLabel('SUSPENDED')).toBe('停流通')
    expect(holdingStatusLabel('SUSPENDED')).not.toBe(holdingStatusLabel('WITHDRAWN'))
    expect(holdingStatusLabel('SUSPENDED')).not.toBe(holdingStatusLabel('LOST'))
    expect(holdingStatusLabel('SUSPENDED')).not.toBe(holdingStatusLabel('AVAILABLE'))
    // 未知值仍是「未知状态」，不因为新增了 SUSPENDED 就把兜底改掉
    expect(holdingStatusLabel('SUSPEND')).toBe('未知状态')
  })

  it('maps reader-level codes', () => {
    expect(readerLevelLabel('em')).toBe('青年（14~20岁）、普通成人')
    expect(readerLevelLabel('c')).toBe('小学生（5~10岁）')
  })

  it('combines holding location with shelf number without duplicating it', () => {
    expect(holdingLocationLabel('流通库', 'S-1')).toBe('流通库 S-1')
    expect(holdingLocationLabel('3F 文学区 A-12', 'A-12')).toBe('3F 文学区 A-12')
    expect(holdingLocationLabel('3F 文学区', 'A-12')).toBe('3F 文学区 A-12')
    expect(holdingLocationLabel('流通库', null)).toBe('流通库')
    expect(holdingLocationLabel(null, 'S-1')).toBe('S-1')
    expect(holdingLocationLabel(null, null)).toBe('—')
    expect(holdingLocationLabel('2F 社科区 B-08', 'G-1')).toBe('2F 社科区 B-08')
  })

  it('maps reader status and never guesses between 正常 and 已注销', () => {
    expect(readerStatusLabel('ACTIVE')).toBe('正常')
    expect(readerStatusLabel('SUSPENDED')).toBe('暂停借阅')
    expect(readerStatusLabel('INACTIVE')).toBe('已注销')
    // 未知值不再按「非 ACTIVE 即已注销」处理（那是旧代码两页相反行为的原因）
    expect(readerStatusLabel('FROZEN')).toBe('未知状态')
    expect(readerStatusLabel(null)).toBe('未知状态')
  })
})
