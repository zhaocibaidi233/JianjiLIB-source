import axios, { AxiosError, type AxiosRequestConfig } from 'axios'

const TOKEN_KEY = 'jianji.token'
const OPERATOR_KEY = 'jianji.operator'
const READER_TOKEN_KEY = 'jianji.reader.token'

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}

export function setToken(token: string | null) {
  if (token) localStorage.setItem(TOKEN_KEY, token)
  else localStorage.removeItem(TOKEN_KEY)
}

export function getOperator(): string {
  return localStorage.getItem(OPERATOR_KEY) || ''
}

export function setOperator(name: string) {
  localStorage.setItem(OPERATOR_KEY, name)
}

export function getReaderToken(): string | null {
  return localStorage.getItem(READER_TOKEN_KEY)
}

export function setReaderToken(token: string | null) {
  if (token) localStorage.setItem(READER_TOKEN_KEY, token)
  else localStorage.removeItem(READER_TOKEN_KEY)
}

export class ApiError extends Error {
  status: number
  detail: string
  raw: unknown

  constructor(status: number, detail: string, raw?: unknown) {
    super(detail)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
    this.raw = raw
  }
}

function detailOf(payload: unknown): string {
  if (!payload) return ''
  if (typeof payload === 'string') return payload
  if (typeof payload !== 'object') return String(payload)
  const data = payload as Record<string, unknown>
  const detail = data.detail ?? data.message
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        if (typeof item === 'string') return item
        if (item && typeof item === 'object') {
          const row = item as Record<string, unknown>
          const loc = Array.isArray(row.loc) ? row.loc.slice(1).join('.') : ''
          const msg = typeof row.msg === 'string' ? row.msg : ''
          return loc ? `${loc}：${msg}` : msg
        }
        return ''
      })
      .filter(Boolean)
      .join('；')
  }
  // 结构化 detail：`{message, hint, ...}`。乐观锁 409、905 冲突等都用这个形状——
  // 不认它的话用户只看到 "Request failed with status code 409"（2026-10-25）。
  if (detail && typeof detail === 'object') {
    const row = detail as Record<string, unknown>
    const msg = typeof row.message === 'string' ? row.message : ''
    const hint = typeof row.hint === 'string' ? row.hint : ''
    if (msg) return hint ? `${msg}：${hint}` : msg
  }
  return ''
}

export const http = axios.create({
  baseURL: '/api',
  // 60s：与旧 legacy 层同一默认（后端卡死时页面不至于挂两分钟）。已知慢的
  // 接口（套录 / 转库 / 快速录入）各自按请求覆盖 120s/180s。原来这里是 30s，
  // 两层合并后必须取宽容的那个——否则 legacy 靠默认超时的接口会集体变脆。
  timeout: 60_000,
})

export const readerHttp = axios.create({
  baseURL: '/api',
  timeout: 30_000,
})

http.interceptors.request.use((config) => {
  const token = getToken()
  if (token) {
    // 同一个 token **两种头都发**，因为它可能是两种东西：
    //   * 登录令牌 —— 后端只认 `Authorization: Bearer`（services/auth.py）；
    //   * 部署级共享密钥 JIANJI_ACCESS_TOKEN（老部署手工设置）—— 只认
    //     `X-Access-Token`（main.py 的 AccessTokenMiddleware）。
    // ⚠️ 只发 X-Access-Token 踩过坑：登录令牌被拿去和共享密钥比对（没设该变量
    // 就跳过），会话解析又找不到头 → 每条请求 401。两个头都发两种部署都能工作。
    config.headers.Authorization = `Bearer ${token}`
    config.headers['X-Access-Token'] = token
  }
  const operator = getOperator()
  if (operator) {
    try {
      // HTTP 头字段值只能是 ASCII，中文署名必须 percent-encode（后端 unquote 还原）
      config.headers['X-Operator'] = encodeURIComponent(operator)
    } catch {
      /* localStorage 里是坏值时忽略署名，不阻断请求 */
    }
  }
  return config
})

http.interceptors.response.use(
  (response) => response,
  (error: AxiosError) => {
    const status = error.response?.status ?? 0
    const url = error.config?.url || ''
    let message: string
    if (detailOf(error.response?.data)) {
      message = detailOf(error.response?.data)
    } else if (error.code === 'ECONNABORTED' || /timeout/i.test(error.message || '')) {
      message = '请求超时：服务响应过慢，请稍后重试'
    } else if (status === 0) {
      message = '无法连接后端服务，请确认服务已启动'
    } else if (status === 401) {
      message = '登录已过期，请重新登录'
    } else if (status === 403) {
      message = '没有权限执行此操作'
    } else if (status >= 500 && !error.response?.data) {
      // ⚠️ 「5xx 且响应体为空」= **代理没连上后端**，不是后端自己报错：
      // FastAPI 的 500 一定带 JSON detail；vite dev / nginx 在上游拒连时
      // 自己回一个空 5xx。别把人指去"查看后端日志"——后端根本没跑起来。
      message = `无法连接后端服务（HTTP ${status} 空响应）：请确认后端已启动，或检查前端代理配置`
    } else {
      message = error.message || '请求失败'
    }

    if (status === 401 && !url.includes('/auth/login')) {
      setToken(null)
      window.dispatchEvent(new CustomEvent('jianji:unauthorized'))
    }
    return Promise.reject(new ApiError(status, message, error.response?.data))
  },
)

readerHttp.interceptors.request.use((config) => {
  const token = getReaderToken()
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

readerHttp.interceptors.response.use(
  (response) => response,
  (error: AxiosError) => {
    const status = error.response?.status ?? 0
    const url = error.config?.url || ''
    const message =
      detailOf(error.response?.data) ||
      (status === 0
        ? '无法连接后端服务，请稍后重试'
        : status === 401
          ? '登录已过期，请重新登录'
          : status === 403
            ? '没有权限执行此操作'
            : error.message || '请求失败')
    if (status === 401 && !url.includes('/portal/auth/login')) {
      setReaderToken(null)
      window.dispatchEvent(new CustomEvent('jianji:reader-unauthorized'))
    }
    return Promise.reject(new ApiError(status, message, error.response?.data))
  },
)

export async function get<T>(url: string, params?: Record<string, unknown>, config?: AxiosRequestConfig): Promise<T> {
  const response = await http.get<T>(url, { params, ...config })
  return response.data
}

export async function post<T>(url: string, body?: unknown, config?: AxiosRequestConfig): Promise<T> {
  const response = await http.post<T>(url, body, config)
  return response.data
}

export async function put<T>(url: string, body?: unknown, config?: AxiosRequestConfig): Promise<T> {
  const response = await http.put<T>(url, body, config)
  return response.data
}

export async function del<T>(url: string, config?: AxiosRequestConfig): Promise<T> {
  const response = await http.delete<T>(url, config)
  return response.data
}

export async function readerGet<T>(url: string, params?: Record<string, unknown>): Promise<T> {
  const response = await readerHttp.get<T>(url, { params })
  return response.data
}

export async function readerPost<T>(url: string, body?: unknown): Promise<T> {
  const response = await readerHttp.post<T>(url, body)
  return response.data
}

export async function readerDelete<T>(url: string): Promise<T> {
  const response = await readerHttp.delete<T>(url)
  return response.data
}

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.detail
  if (error instanceof Error) return error.message
  return String(error)
}

function filenameFrom(disposition: string | undefined, fallback: string): string {
  if (!disposition) return fallback
  const encoded = /filename\*=UTF-8''([^;]+)/i.exec(disposition)
  if (encoded) {
    try {
      return decodeURIComponent(encoded[1])
    } catch {
      return fallback
    }
  }
  const plain = /filename="?([^";]+)"?/i.exec(disposition)
  return plain ? plain[1] : fallback
}

export async function download(
  url: string,
  params?: Record<string, unknown>,
  fallbackName = '导出文件',
): Promise<void> {
  const response = await http.get(url, { params, responseType: 'blob' })
  saveBlob(response, fallbackName)
}

export async function downloadPost(
  url: string,
  body: unknown,
  fallbackName = '导出文件',
): Promise<void> {
  const response = await http.post(url, body, { responseType: 'blob' })
  saveBlob(response, fallbackName)
}

function saveBlob(response: { data: unknown; headers: Record<string, unknown> }, fallbackName: string) {
  const name = filenameFrom(
    String(response.headers['content-disposition'] || ''),
    fallbackName,
  )
  const href = URL.createObjectURL(response.data as Blob)
  const link = document.createElement('a')
  link.href = href
  link.download = name
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(href)
}
