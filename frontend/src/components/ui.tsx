import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode, SelectHTMLAttributes, TextareaHTMLAttributes } from 'react'
import { humanize } from '../lib/format'

export const cx = (...classes: (string | false | null | undefined)[]) => classes.filter(Boolean).join(' ')

export function Card({ title, actions, children, className }: { title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={cx('rounded-xl border border-slate-200 bg-white shadow-sm', className)}>
      {(title || actions) && (
        <header className="flex items-center justify-between gap-4 border-b border-slate-100 px-5 py-3">
          <h2 className="text-sm font-semibold text-slate-900">{title}</h2>
          {actions}
        </header>
      )}
      <div className="p-5">{children}</div>
    </section>
  )
}

type Variant = 'primary' | 'secondary' | 'danger' | 'ghost'
const variants: Record<Variant, string> = {
  primary: 'bg-brand-600 text-white hover:bg-brand-700 disabled:bg-brand-500/50',
  secondary: 'bg-white text-slate-700 ring-1 ring-inset ring-slate-300 hover:bg-slate-50',
  danger: 'bg-red-600 text-white hover:bg-red-700 disabled:bg-red-300',
  ghost: 'text-slate-600 hover:bg-slate-100',
}

export function Button({ variant = 'primary', busy, className, children, ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; busy?: boolean }) {
  return (
    <button
      {...props}
      disabled={props.disabled || busy}
      className={cx('inline-flex items-center justify-center gap-2 rounded-lg px-4 py-2 text-sm font-medium transition disabled:cursor-not-allowed', variants[variant], className)}
    >
      {busy && <Spinner className="h-4 w-4" />}
      {children}
    </button>
  )
}

export function Spinner({ className = 'h-5 w-5' }: { className?: string }) {
  return <span aria-hidden className={cx('inline-block animate-spin rounded-full border-2 border-current border-r-transparent', className)} />
}

export function Field({ label, hint, error, required, children }: { label: string; hint?: string; error?: string; required?: boolean; children: ReactNode }) {
  return (
    <label className="block">
      <span className="text-sm font-medium text-slate-700">
        {label}
        {required && <span className="text-red-600"> *</span>}
      </span>
      <div className="mt-1">{children}</div>
      {hint && !error && <span className="mt-1 block text-xs text-slate-500">{hint}</span>}
      {error && <span className="mt-1 block text-xs text-red-600">{error}</span>}
    </label>
  )
}

const control = 'block w-full rounded-lg border-0 bg-white px-3 py-2 text-sm text-slate-900 ring-1 ring-inset ring-slate-300 placeholder:text-slate-400 focus:ring-2 focus:ring-brand-500'
export const Input = (props: InputHTMLAttributes<HTMLInputElement>) => <input {...props} className={cx(control, props.className)} />
export const Textarea = (props: TextareaHTMLAttributes<HTMLTextAreaElement>) => <textarea rows={4} {...props} className={cx(control, props.className)} />
export const Select = (props: SelectHTMLAttributes<HTMLSelectElement>) => <select {...props} className={cx(control, props.className)} />

export function Alert({ tone = 'info', title, children }: { tone?: 'info' | 'success' | 'warning' | 'error'; title?: string; children?: ReactNode }) {
  const tones = {
    info: 'bg-brand-50 text-brand-700 ring-brand-100',
    success: 'bg-emerald-50 text-emerald-800 ring-emerald-200',
    warning: 'bg-amber-50 text-amber-800 ring-amber-200',
    error: 'bg-red-50 text-red-800 ring-red-200',
  }
  return (
    <div role={tone === 'error' ? 'alert' : 'status'} className={cx('rounded-lg px-4 py-3 text-sm ring-1 ring-inset', tones[tone])}>
      {title && <p className="font-semibold">{title}</p>}
      {children && <div className={title ? 'mt-1' : undefined}>{children}</div>}
    </div>
  )
}

const stageTone: Record<string, string> = {
  INTAKE: 'bg-slate-100 text-slate-700',
  SCREENING: 'bg-sky-50 text-sky-700',
  INTERVIEW: 'bg-violet-50 text-violet-700',
  OFFER: 'bg-amber-50 text-amber-800',
  ONBOARDING: 'bg-emerald-50 text-emerald-700',
  CLOSED: 'bg-slate-100 text-slate-600',
}
const statusStage: Record<string, string> = {
  NEW: 'INTAKE', VALIDATING: 'INTAKE', VALIDATED: 'SCREENING', SCORED: 'SCREENING', SCREENING_REVIEW: 'SCREENING',
  SHORTLISTED: 'INTERVIEW', INTERVIEW_SCHEDULED: 'INTERVIEW', INTERVIEWED: 'INTERVIEW', INTERVIEW_REVIEW: 'INTERVIEW',
  SELECTED: 'OFFER', OFFER_PENDING_APPROVAL: 'OFFER', OFFERED: 'OFFER', NEGOTIATION: 'OFFER',
  ACCEPTED: 'ONBOARDING', ONBOARDING: 'ONBOARDING', ONBOARDED: 'ONBOARDING',
}
const attention = new Set(['SCREENING_REVIEW', 'INTERVIEW_REVIEW', 'OFFER_PENDING_APPROVAL', 'NEGOTIATION'])

export function StatusBadge({ status }: { status?: string | null }) {
  if (!status) return null
  const tone = attention.has(status) ? 'bg-amber-100 text-amber-900 ring-1 ring-amber-300' : stageTone[statusStage[status] ?? 'CLOSED']
  const closedBad = ['REJECTED', 'DECLINED', 'OFFER_EXPIRED', 'WITHDRAWN'].includes(status)
  return (
    <span className={cx('inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium', closedBad ? 'bg-rose-50 text-rose-700' : tone)}>
      {humanize(status)}
    </span>
  )
}

export function Pill({ children, tone = 'slate' }: { children: ReactNode; tone?: 'slate' | 'green' | 'red' | 'amber' | 'blue' }) {
  const tones = {
    slate: 'bg-slate-100 text-slate-700',
    green: 'bg-emerald-50 text-emerald-700',
    red: 'bg-rose-50 text-rose-700',
    amber: 'bg-amber-50 text-amber-800',
    blue: 'bg-brand-50 text-brand-700',
  }
  return <span className={cx('inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium', tones[tone])}>{children}</span>
}

export function Loading({ label = 'Loading' }: { label?: string }) {
  return (
    <div className="flex items-center gap-3 py-10 text-sm text-slate-500" role="status">
      <Spinner /> {label}…
    </div>
  )
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="py-8 text-center text-sm text-slate-500">{children}</p>
}

export function KeyValue({ items }: { items: [ReactNode, ReactNode][] }) {
  return (
    <dl className="grid grid-cols-1 gap-x-6 gap-y-3 sm:grid-cols-2">
      {items.map(([key, value], index) => (
        <div key={index} className="min-w-0">
          <dt className="text-xs font-medium uppercase tracking-wide text-slate-500">{key}</dt>
          <dd className="mt-0.5 truncate text-sm text-slate-900">{value ?? '-'}</dd>
        </div>
      ))}
    </dl>
  )
}
