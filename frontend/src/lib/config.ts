// Where the portal talks to. Set at build time (VITE_*); the defaults match the local Docker stack.
export const API_URL = (import.meta.env.VITE_API_URL ?? 'http://localhost:8000').replace(/\/$/, '')
export const INTAKE_URL = import.meta.env.VITE_INTAKE_URL ?? 'http://localhost:5678/webhook/applications'
export const COMPANY = 'NovaTech Solutions'
export const TIMEZONE = 'Asia/Karachi'
/** Development only: where outgoing emails can be read (Mailpit). Leave unset in production builds. */
export const DEV_MAILBOX_URL = import.meta.env.VITE_DEV_MAILBOX_URL || null
