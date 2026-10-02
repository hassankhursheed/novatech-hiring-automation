import { useMutation, useQuery, useQueryClient, type QueryKey } from '@tanstack/react-query'
import { api, ApiError, type RequestOptions } from './api'
import { saveSession, useStaffSession } from './session'

/** Staff API call with the session token; an expired or revoked session signs the user out. */
export function useStaffApi() {
  const session = useStaffSession()
  return async <T,>(path: string, options: RequestOptions = {}): Promise<T> => {
    try {
      return await api<T>(`/v1/staff${path}`, { ...options, token: session?.token })
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) saveSession(null)
      throw error
    }
  }
}

export function useStaffQuery<T>(key: QueryKey, path: string, options: { refetchInterval?: number } = {}) {
  const call = useStaffApi()
  return useQuery({ queryKey: ['staff', ...key], queryFn: () => call<T>(path), ...options })
}

/** A staff action (POST) that refreshes every staff view afterwards. */
export function useStaffAction<V>(path: (vars: V) => string, body?: (vars: V) => Record<string, unknown>) {
  const call = useStaffApi()
  const client = useQueryClient()
  return useMutation({
    mutationFn: (vars: V) => call(path(vars), { method: 'POST', body: body ? body(vars) : {} }),
    onSuccess: () => client.invalidateQueries({ queryKey: ['staff'] }),
  })
}

export interface ApplicationRow {
  application_id: string
  application_code: string
  status: string
  status_changed_at: string
  created_at: string
  application_score: number | null
  interview_score: number | null
  final_score: number | null
  ai_recommendation: string | null
  review_reason: string | null
  full_name: string
  email: string
  position_code: string
  position_title: string
  stage: string
  awaits_human: boolean
}
