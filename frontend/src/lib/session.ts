import { useState, useSyncExternalStore } from 'react'

/**
 * Tokens never live in the URL longer than one page load: a link's token is read from the URL fragment
 * (#token=...), removed from the address bar, and kept in sessionStorage for this tab only.
 */
export function readLinkToken(scope: string): string | null {
  const key = `link:${scope}`
  const match = window.location.hash.match(/token=([A-Za-z0-9._-]+)/)
  if (match) {
    sessionStorage.setItem(key, match[1])
    window.history.replaceState(null, '', window.location.pathname + window.location.search)
    return match[1]
  }
  return sessionStorage.getItem(key)
}

export function useLinkToken(scope: string): string | null {
  const [token] = useState(() => readLinkToken(scope))
  return token
}

export interface StaffProfile {
  staff_id: string
  full_name: string
  email: string
  roles: string[]
  job_title?: string
}

interface StoredSession {
  token: string
  expires_at: string
  staff: StaffProfile
}

const SESSION_KEY = 'staff-session'
const listeners = new Set<() => void>()
let cached: StoredSession | null | undefined

function read(): StoredSession | null {
  if (cached !== undefined) return cached
  try {
    const raw = sessionStorage.getItem(SESSION_KEY)
    const parsed = raw ? (JSON.parse(raw) as StoredSession) : null
    cached = parsed && new Date(parsed.expires_at) > new Date() ? parsed : null
  } catch {
    cached = null
  }
  return cached
}

export function saveSession(session: StoredSession | null): void {
  if (session) sessionStorage.setItem(SESSION_KEY, JSON.stringify(session))
  else sessionStorage.removeItem(SESSION_KEY)
  cached = session
  listeners.forEach((notify) => notify())
}

export function useStaffSession(): StoredSession | null {
  return useSyncExternalStore(
    (notify) => {
      listeners.add(notify)
      return () => listeners.delete(notify)
    },
    read,
  )
}

export const hasRole = (staff: StaffProfile | undefined, ...roles: string[]) =>
  Boolean(staff?.roles.some((role) => roles.includes(role)))
