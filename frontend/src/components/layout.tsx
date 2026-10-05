import type { ReactNode } from 'react'
import { Link, NavLink, Navigate, Outlet, useLocation } from 'react-router-dom'
import { COMPANY } from '../lib/config'
import { saveSession, useStaffSession } from '../lib/session'
import { cx } from './ui'

function Logo({ to = '/' }: { to?: string }) {
  return (
    <Link to={to} className="flex items-center gap-2 font-semibold text-slate-900">
      <span className="grid h-8 w-8 place-items-center rounded-lg bg-brand-600 text-sm font-bold text-white">N</span>
      <span>{COMPANY}</span>
    </Link>
  )
}

export function PublicLayout() {
  return (
    <div className="flex min-h-screen flex-col">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex h-16 max-w-5xl items-center justify-between px-4">
          <Logo />
          <nav className="flex items-center gap-5 text-sm text-slate-600">
            <Link to="/careers" className="hover:text-slate-900">Careers</Link>
            <Link to="/staff" className="hover:text-slate-900">Staff portal</Link>
          </nav>
        </div>
      </header>
      <main className="mx-auto w-full max-w-5xl flex-1 px-4 py-10">
        <Outlet />
      </main>
      <footer className="border-t border-slate-200 bg-white py-6 text-center text-xs text-slate-500">
        © {new Date().getFullYear()} {COMPANY}. Your data is used only for this recruitment process.
      </footer>
    </div>
  )
}

/** Narrow centered column for single-task pages (link pages, sign-in). */
export function Narrow({ title, subtitle, children }: { title: string; subtitle?: ReactNode; children: ReactNode }) {
  return (
    <div className="mx-auto max-w-2xl">
      <h1 className="text-2xl font-semibold text-slate-900">{title}</h1>
      {subtitle && <p className="mt-2 text-sm text-slate-600">{subtitle}</p>}
      <div className="mt-6 space-y-6">{children}</div>
    </div>
  )
}

const nav = [
  { to: '/staff', label: 'Dashboard', end: true },
  { to: '/staff/applications', label: 'Applications' },
  { to: '/staff/onboarding', label: 'Onboarding' },
  { to: '/staff/errors', label: 'Automation errors' },
]

export function StaffLayout() {
  const session = useStaffSession()
  const location = useLocation()
  if (!session) return <Navigate to="/staff/login" replace state={{ from: location.pathname }} />
  return (
    <div className="min-h-screen">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex h-16 max-w-7xl items-center justify-between gap-6 px-4">
          <div className="flex items-center gap-8">
            <Logo to="/staff" />
            <nav className="hidden gap-1 md:flex">
              {nav.map((item) => (
                <NavLink
                  key={item.to}
                  to={item.to}
                  end={item.end}
                  className={({ isActive }) =>
                    cx('rounded-lg px-3 py-2 text-sm font-medium', isActive ? 'bg-brand-50 text-brand-700' : 'text-slate-600 hover:bg-slate-100')
                  }
                >
                  {item.label}
                </NavLink>
              ))}
            </nav>
          </div>
          <div className="flex items-center gap-3 text-sm">
            <div className="text-right leading-tight">
              <p className="font-medium text-slate-900">{session.staff.full_name}</p>
              <p className="text-xs text-slate-500">{session.staff.roles.map((r) => r.replace(/_/g, ' ').toLowerCase()).join(', ')}</p>
            </div>
            <button className="rounded-lg px-3 py-2 text-slate-600 hover:bg-slate-100" onClick={() => saveSession(null)}>
              Sign out
            </button>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-7xl px-4 py-8">
        <Outlet />
      </main>
    </div>
  )
}

export function PageTitle({ title, subtitle, actions }: { title: ReactNode; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 className="text-2xl font-semibold text-slate-900">{title}</h1>
        {subtitle && <p className="mt-1 text-sm text-slate-600">{subtitle}</p>}
      </div>
      {actions}
    </div>
  )
}
