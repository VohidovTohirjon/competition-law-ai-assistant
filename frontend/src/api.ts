// Same-origin by default: behind Nginx the SPA calls /api on its own host, so an
// empty (or absent) VITE_API_URL must stay empty rather than falling back to a
// developer machine address. Only the Vite dev server, which runs on a different
// port than the API, needs an absolute base URL.
export const API_URL = import.meta.env.VITE_API_URL !== undefined
  ? import.meta.env.VITE_API_URL
  : (import.meta.env.DEV ? 'http://localhost:8000' : '')

const storage = typeof localStorage !== 'undefined' && typeof localStorage.getItem === 'function'
  ? localStorage : null
let token = storage?.getItem('raqobat_token') || ''

export class ApiError extends Error {
  status?: number
  constructor(message: string, status?: number) { super(message); this.status = status }
}

type ApiOptions = RequestInit & { timeoutMs?: number }

export function setToken(value: string) {
  token = value
  if (value) storage?.setItem('raqobat_token', value)
  else storage?.removeItem('raqobat_token')
}

export async function api<T>(path: string, options: ApiOptions = {}): Promise<T> {
  const headers = new Headers(options.headers)
  if (token) headers.set('Authorization', `Bearer ${token}`)
  if (options.body && !(options.body instanceof FormData) && !(options.body instanceof URLSearchParams) && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }
  const controller = new AbortController()
  let timedOut = false
  const upstreamSignal = options.signal
  const abortFromUpstream = () => controller.abort()
  upstreamSignal?.addEventListener('abort', abortFromUpstream, { once: true })
  const timeout = window.setTimeout(() => { timedOut = true; controller.abort() }, options.timeoutMs ?? 15_000)
  const { timeoutMs: _timeoutMs, ...fetchOptions } = options
  let response!: Response
  const isReadOnly = (options.method || 'GET').toUpperCase() === 'GET'
  try {
    for (let attempt = 0; attempt < (isReadOnly ? 2 : 1); attempt += 1) {
      try {
        response = await fetch(`${API_URL}${path}`, { ...fetchOptions, headers, signal: controller.signal })
        break
      } catch (error) {
        if ((error as Error).name === 'AbortError') {
          throw new ApiError(timedOut ? 'Server belgilangan vaqtda javob bermadi. Qayta urinib ko‘ring.' : 'So‘rov bekor qilindi.')
        }
        if (attempt === 0 && isReadOnly) continue
        throw new ApiError('Backend bilan aloqa o‘rnatilmadi. Backend va ma’lumotlar bazasi ishlayotganini tekshiring.')
      }
    }
  } finally {
    window.clearTimeout(timeout)
    upstreamSignal?.removeEventListener('abort', abortFromUpstream)
  }
  if (response.status === 401) {
    setToken('')
    window.dispatchEvent(new Event('raqobat:logout'))
  }
  if (!response.ok) {
    let message = 'So‘rovni bajarib bo‘lmadi'
    try {
      // FastAPI returns a string for HTTPException and an array of field errors for a
      // 422; rendering the array directly printed the literal text "[object Object]".
      const detail = (await response.json()).detail
      if (typeof detail === 'string' && detail) message = detail
      else if (Array.isArray(detail) && detail.length) {
        const parts = detail
          .map((item: {msg?: string; loc?: (string|number)[]}) => {
            const field = (item.loc || []).filter(part => part !== 'body').join('.')
            const text = String(item.msg || '').replace(/^Value error,\s*/, '')
            return field && text ? `${field}: ${text}` : text
          })
          .filter(Boolean)
        if (parts.length) message = parts.join('; ')
      }
    } catch { /* javob JSON emas */ }
    throw new ApiError(message, response.status)
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export async function download(path: string, filename: string) {
  const headers = new Headers()
  if (token) headers.set('Authorization', `Bearer ${token}`)
  const response = await fetch(`${API_URL}${path}`, { headers })
  if (!response.ok) throw new Error('Faylni yuklab bo‘lmadi')
  const blob = await response.blob()
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url; link.download = filename; link.click()
  URL.revokeObjectURL(url)
}
