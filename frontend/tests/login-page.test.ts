import { beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'

// 回归测试：管理员打开「允许自助注册」后，登录页必须出现「申请开通」入口。
// 这正是上报过的问题 —— 后端 /auth/status 已经返回 registration_open=true，
// 但前端既没把它读进状态、也没有这块 UI，于是「开关开了、页面什么都没有」。

const get = vi.fn()
const post = vi.fn()

class ApiError extends Error {
  status: number
  detail: string
  constructor(status: number, detail: string) {
    super(detail)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
  }
}

vi.mock('../src/lib/api', () => ({
  get: (...args: unknown[]) => get(...args),
  post: (...args: unknown[]) => post(...args),
  setToken: () => undefined,
  getToken: () => null,
  ApiError,
}))

const { auth, ensureAuthReady } = await import('../src/lib/auth')
const Login = (await import('../src/views/Login.vue')).default

function statusBody(registrationOpen: boolean) {
  return {
    enabled: true,
    needs_setup: false,
    authenticated: false,
    user: null,
    registration_open: registrationOpen,
  }
}

async function mountLogin() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/login', component: Login },
      { path: '/admin', component: { template: '<div />' } },
    ],
  })
  await router.push('/login')
  await router.isReady()
  const wrapper = mount(Login, { global: { plugins: [router] } })
  await ensureAuthReady(true)
  await wrapper.vm.$nextTick()
  return wrapper
}

beforeEach(() => {
  get.mockReset()
  post.mockReset()
  auth.ready = false
})

describe('登录页的「申请开通」入口', () => {
  it('is hidden when the administrator has not opened self-registration', async () => {
    get.mockResolvedValue(statusBody(false))
    const wrapper = await mountLogin()
    expect(wrapper.text()).not.toContain('申请开通')
  })

  it('appears once registration_open is true', async () => {
    get.mockResolvedValue(statusBody(true))
    const wrapper = await mountLogin()
    expect(wrapper.text()).toContain('申请开通')
  })

  it('switches to the application form and back', async () => {
    get.mockResolvedValue(statusBody(true))
    const wrapper = await mountLogin()

    await wrapper.findAll('button').find((b) => b.text() === '申请开通')!.trigger('click')
    expect(wrapper.text()).toContain('申请开通账号')
    expect(wrapper.find('#reg-username').exists()).toBe(true)

    await wrapper.findAll('button').find((b) => b.text() === '返回登录')!.trigger('click')
    expect(wrapper.text()).toContain('馆员登录')
  })

  it('refuses to submit when the two passwords differ', async () => {
    get.mockResolvedValue(statusBody(true))
    const wrapper = await mountLogin()
    await wrapper.findAll('button').find((b) => b.text() === '申请开通')!.trigger('click')

    await wrapper.find('#reg-username').setValue('zhang3')
    await wrapper.find('#reg-password').setValue('hunter22')
    await wrapper.find('#reg-password2').setValue('hunter23')
    await wrapper.find('form').trigger('submit')

    expect(post).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('两次输入的密码不一致')
  })

  it('submits the application and shows the receipt instead of logging in', async () => {
    get.mockResolvedValue(statusBody(true))
    post.mockResolvedValue({ ok: true, id: 3, message: '申请已提交，管理员批准后即用这个账号和密码登录' })
    const wrapper = await mountLogin()
    await wrapper.findAll('button').find((b) => b.text() === '申请开通')!.trigger('click')

    await wrapper.find('#reg-username').setValue('zhang3')
    await wrapper.find('#reg-password').setValue('hunter22')
    await wrapper.find('#reg-password2').setValue('hunter22')
    await wrapper.find('#reg-note').setValue('初一(3)班 语文')
    await wrapper.find('form').trigger('submit')
    await new Promise((r) => setTimeout(r, 0))
    await wrapper.vm.$nextTick()

    expect(post).toHaveBeenCalledWith('/auth/register', {
      username: 'zhang3',
      password: 'hunter22',
      note: '初一(3)班 语文',
    })
    expect(wrapper.text()).toContain('申请已提交')
    // 申请不是登录：不该跳走
    expect(auth.authenticated).toBe(false)
  })
})
