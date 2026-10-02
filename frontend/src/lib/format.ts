import { TIMEZONE } from './config'

const dateTime = new Intl.DateTimeFormat('en-GB', {
  timeZone: TIMEZONE,
  weekday: 'short',
  day: 'numeric',
  month: 'short',
  year: 'numeric',
  hour: 'numeric',
  minute: '2-digit',
  hour12: true,
})
const dateOnly = new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'long', year: 'numeric', timeZone: 'UTC' })
const shortTime = new Intl.DateTimeFormat('en-GB', { timeZone: TIMEZONE, day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit', hour12: true })

export const formatDateTime = (iso?: string | null) => (iso ? dateTime.format(new Date(iso)) : '-')
export const formatShort = (iso?: string | null) => (iso ? shortTime.format(new Date(iso)) : '-')
/** Calendar dates (YYYY-MM-DD) carry no time zone; format them as they are. */
export const formatDate = (value?: string | null) => (value ? dateOnly.format(new Date(`${value.slice(0, 10)}T00:00:00Z`)) : '-')
export const formatMoney = (amount?: number | null, currency = 'PKR') =>
  amount === null || amount === undefined ? '-' : `${currency} ${Number(amount).toLocaleString('en-US')}`
export const formatScore = (score?: number | string | null) =>
  score === null || score === undefined ? '-' : Number(score).toFixed(Number(score) % 1 ? 1 : 0)
export const humanize = (code?: string | null) =>
  code ? code.toLowerCase().replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase()) : '-'
