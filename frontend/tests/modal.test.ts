import { afterEach, describe, expect, it } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import BaseModal from '../src/components/BaseModal.vue'
import ConfirmDialog from '../src/components/ConfirmDialog.vue'

const wrappers: ReturnType<typeof mount>[] = []

function createModal(props: Record<string, unknown> = {}) {
  const wrapper = mount(BaseModal, {
    attachTo: document.body,
    props: { open: false, title: '测试对话框', ...props },
    slots: { default: '<input aria-label="内容"><button type="button">保存</button>' },
  })
  wrappers.push(wrapper)
  return wrapper
}

afterEach(() => {
  for (const wrapper of wrappers.splice(0).reverse()) wrapper.unmount()
  document.body.innerHTML = ''
})

describe('modal lifecycle', () => {
  it('focuses an initially open panel and restores the trigger on close', async () => {
    const trigger = document.createElement('button')
    document.body.append(trigger)
    trigger.focus()
    const wrapper = createModal({ open: true })
    await flushPromises()
    expect(document.activeElement?.outerHTML).toContain('aria-label="内容"')
    expect(document.body.classList.contains('no-scroll')).toBe(true)
    await wrapper.setProps({ open: false })
    await flushPromises()
    expect(document.activeElement).toBe(trigger)
    expect(document.body.classList.contains('no-scroll')).toBe(false)
  })

  it('does not close a busy dialog via Escape, backdrop, or close button', async () => {
    const wrapper = createModal({ open: true, busy: true })
    await flushPromises()
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    document.querySelector('.overlay')!.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }))
    ;(document.querySelector('.modal-close') as HTMLButtonElement).click()
    expect(wrapper.emitted('close')).toBeUndefined()
    expect((document.querySelector('.modal-close') as HTMLButtonElement).disabled).toBe(true)
    await wrapper.setProps({ busy: false })
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    expect(wrapper.emitted('close')).toHaveLength(1)
  })

  it('only handles Escape in the top panel and keeps the underlying scroll lock', async () => {
    const lower = createModal({ open: true })
    await flushPromises()
    const upper = createModal({ open: true })
    await flushPromises()
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    expect(upper.emitted('close')).toHaveLength(1)
    expect(lower.emitted('close')).toBeUndefined()
    await upper.setProps({ open: false })
    await flushPromises()
    expect(document.body.classList.contains('no-scroll')).toBe(true)
    expect(document.querySelector('.modal')!.contains(document.activeElement)).toBe(true)
  })

  it('keeps keyboard navigation in the panel when focus starts outside', async () => {
    createModal({ open: true })
    await flushPromises()
    const outside = document.createElement('button')
    document.body.append(outside)
    outside.focus()
    const event = new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true })
    document.dispatchEvent(event)
    expect(event.defaultPrevented).toBe(true)
    expect(document.querySelector('.modal')!.contains(document.activeElement)).toBe(true)
  })

  it('passes confirmation busy state to all dismissal paths', async () => {
    const wrapper = mount(ConfirmDialog, { attachTo: document.body, props: { open: true, title: '删除', message: '确认删除', busy: true } })
    wrappers.push(wrapper)
    await flushPromises()
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    expect(wrapper.emitted('cancel')).toBeUndefined()
    expect(document.querySelector('[role="dialog"]')!.getAttribute('aria-busy')).toBe('true')
  })
})
