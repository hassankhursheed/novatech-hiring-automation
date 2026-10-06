import { API_URL } from './config'

/** RFC 9457 problem+json returned by the backend (code + retryable are NovaTech extensions). */
export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly retryable: boolean
  readonly correlationId?: string

  constructor(status: number, code: string, detail: string, retryable = false, correlationId?: string) {
    super(detail)
    this.status = status
    this.code = code
    this.retryable = retryable
    this.correlationId = correlationId
  }
}

type Json = Record<string, unknown> | unknown[] | null

export interface RequestOptions {
  method?: 'GET' | 'POST'
  body?: Json | FormData
  token?: string | null
  headers?: Record<string, string>
}

async function toError(response: Response): Promise<ApiError> {
  let body: Record<string, unknown> = {}
  try {
    body = (await response.json()) as Record<string, unknown>
  } catch {
    // not JSON (proxy error page, network layer): keep the HTTP status
  }
  const detail = String(body.detail ?? body.message ?? response.statusText ?? 'Request failed')
  return new ApiError(
    response.status,
    String(body.code ?? `HTTP_${response.status}`),
    detail,
    Boolean(body.retryable ?? response.status >= 500),
    body.correlation_id ? String(body.correlation_id) : undefined,
  )
}

export async function request<T>(url: string, options: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: 'application/json', ...options.headers }
  let body: BodyInit | undefined
  if (options.body instanceof FormData) {
    body = options.body
  } else if (options.body !== undefined) {
    headers['Content-Type'] = 'application/json'
    body = JSON.stringify(options.body)
  }
  if (options.token) headers.Authorization = `Bearer ${options.token}`

  let response: Response
  try {
    response = await fetch(url, { method: options.method ?? (body ? 'POST' : 'GET'), headers, body })
  } catch {
    throw new ApiError(0, 'NETWORK_ERROR', 'The service could not be reached. Check your connection and try again.', true)
  }
  if (!response.ok) throw await toError(response)
  if (response.status === 204) return undefined as T
  const type = response.headers.get('content-type') ?? ''
  return (type.includes('json') ? await response.json() : await response.blob()) as T
}

/** Backend API call (path starts with /v1/...). */
export const api = <T>(path: string, options: RequestOptions = {}) => request<T>(`${API_URL}${path}`, options)

export const MISSING_LINK = 'This page must be opened from the link in your email.'

/** A friendly sentence for an error, by stable error code. */
export function explain(error: unknown): string {
  if (!(error instanceof ApiError)) return 'Something went wrong. Please try again.'
  const messages: Record<string, string> = {
    LINK_EXPIRED: 'This link has expired. Please contact the recruitment team for a new one.',
    LINK_INVALID: 'This link is not valid. Please use the latest link from your email.',
    LINK_TOKEN_MISSING: MISSING_LINK,
    LINK_ALREADY_USED: 'This sign-in link was already used. Request a new one below.',
    CV_UNSUPPORTED: 'We could not read your CV. Please upload a PDF with selectable text, or a Word (.docx) file.',
    CV_EMPTY: 'The CV file is empty. Please choose your CV again.',
    SLOT_UNAVAILABLE: 'Someone just booked that slot. Please choose another one.',
    STAFF_AUTH_REQUIRED: 'Please sign in.',
    NETWORK_ERROR: error.message,
  }
  return messages[error.code] ?? error.message
}
