import { useMutation, useQuery } from '@tanstack/react-query'
import { useEffect, useRef, useState, type FormEvent } from 'react'
import { Navigate, useLocation, useNavigate } from 'react-router-dom'
import { Alert, Button, Card, Field, Input, Loading, cx } from '../../components/ui'
import { api, ApiError, explain } from '../../lib/api'
import { COMPANY, DEV_MAILBOX_URL } from '../../lib/config'
import { humanize } from '../../lib/format'
import { readLinkToken, saveSession, useStaffSession, type StaffProfile } from '../../lib/session'

interface Session { token: string; expires_at: string; staff: StaffProfile }
interface DemoAccount { full_name: string; email: string; roles: string[]; job_title: string | null }
interface Options { email_link: boolean; password: boolean; demo_accounts?: DemoAccount[] }

export default function Login() {
  const session = useStaffSession()
  const navigate = useNavigate()
  const location = useLocation()
  const from = (location.state as { from?: string } | null)?.from ?? '/staff'
  const [linkToken, setLinkToken] = useState(() => readLinkToken('staff-login'))
  const options = useQuery({ queryKey: ['sign-in-options'], queryFn: () => api<Options>('/v1/auth/staff/options'), staleTime: 60_000 })
  const [method, setMethod] = useState<'password' | 'link' | null>(null)
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')

  const signedIn = (data: Session) => {
    saveSession(data)
    navigate(from, { replace: true })
  }
  const exchange = useMutation({
    mutationFn: (token: string) => api<Session>('/v1/auth/staff/session', { method: 'POST', token }),
    onSettled: () => sessionStorage.removeItem('link:staff-login'), // a sign-in link is spent either way
    onSuccess: signedIn,
  })
  const passwordLogin = useMutation({
    mutationFn: () => api<Session>('/v1/auth/staff/password-login', { body: { email, password } }),
    onSuccess: signedIn,
  })
  const requestLink = useMutation({ mutationFn: () => api<{ status: string }>('/v1/auth/staff/login-link', { body: { email } }) })

  // A sign-in link works once: guard against React running this effect twice in development.
  const started = useRef(false)
  const { mutate } = exchange
  useEffect(() => {
    if (linkToken && !started.current) {
      started.current = true
      mutate(linkToken)
    }
  }, [mutate, linkToken])

  // A sign-in link pasted into an already open portal tab only changes the URL fragment.
  useEffect(() => {
    const onHash = () => {
      const token = readLinkToken('staff-login')
      if (token) {
        started.current = false
        setLinkToken(token)
      }
    }
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  if (session && !linkToken) return <Navigate to={from} replace />
  if (exchange.isPending) return <Loading label="Signing you in" />
  if (options.isLoading) return <Loading />

  const passwordEnabled = Boolean(options.data?.password)
  const active = method ?? (passwordEnabled ? 'password' : 'link')
  const passwordError = passwordLogin.error instanceof ApiError && passwordLogin.error.code === 'INVALID_CREDENTIALS'
    ? 'Email or password is incorrect.'
    : passwordLogin.error ? explain(passwordLogin.error) : null

  return (
    <div className="mx-auto grid max-w-5xl gap-8 lg:grid-cols-[1fr_1.1fr]">
      <section className="rounded-2xl bg-brand-700 p-8 text-white">
        <p className="text-sm font-medium uppercase tracking-wide text-brand-100">{COMPANY}</p>
        <h1 className="mt-2 text-3xl font-semibold">HR &amp; Recruitment portal</h1>
        <p className="mt-4 text-brand-100">Review applications, schedule and score interviews, approve offers and track onboarding. Every decision is recorded with your name.</p>
        <ul className="mt-6 space-y-2 text-sm text-brand-50">
          <li>• Work queues show what needs a person today</li>
          <li>• Full history and automation timeline for every application</li>
          <li>• Failed automation steps can be replayed safely</li>
        </ul>
      </section>

      <div className="space-y-6">
        {exchange.error && <Alert tone="error">{explain(exchange.error)}</Alert>}
        <Card>
          <h2 className="text-lg font-semibold text-slate-900">Sign in</h2>
          {passwordEnabled && (
            <div className="mt-4 grid grid-cols-2 rounded-lg bg-slate-100 p-1 text-sm">
              {(['password', 'link'] as const).map((m) => (
                <button key={m} type="button" onClick={() => setMethod(m)}
                  className={cx('rounded-md py-1.5 font-medium', active === m ? 'bg-white text-slate-900 shadow-sm' : 'text-slate-600')}>
                  {m === 'password' ? 'Email & password' : 'Email me a link'}
                </button>
              ))}
            </div>
          )}

          {active === 'password' ? (
            <form className="mt-5 space-y-4" onSubmit={(e: FormEvent) => { e.preventDefault(); passwordLogin.mutate() }}>
              <Field label="Work email" required>
                <Input type="email" required autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="name@company.com" />
              </Field>
              <Field label="Password" required>
                <Input type="password" required autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} />
              </Field>
              {passwordError && <Alert tone="error">{passwordError}</Alert>}
              <Button type="submit" className="w-full" busy={passwordLogin.isPending}>Sign in</Button>
            </form>
          ) : requestLink.isSuccess ? (
            <div className="mt-5 space-y-4">
              <Alert tone="success" title="Check your inbox">{requestLink.data.status} The link works once and expires in 15 minutes.</Alert>
              {DEV_MAILBOX_URL && (
                <Alert tone="info" title="Development mode">
                  Emails are not delivered to real inboxes here. Open the test inbox at{' '}
                  <a className="font-medium underline" href={DEV_MAILBOX_URL} target="_blank" rel="noreferrer">{DEV_MAILBOX_URL}</a>{' '}
                  and click the link in the newest “Your sign-in link” email.
                </Alert>
              )}
              <Button variant="ghost" onClick={() => requestLink.reset()}>Use another email</Button>
            </div>
          ) : (
            <form className="mt-5 space-y-4" onSubmit={(e: FormEvent) => { e.preventDefault(); requestLink.mutate() }}>
              <p className="text-sm text-slate-600">We email you a one-time sign-in link. No password needed.</p>
              <Field label="Work email" required>
                <Input type="email" required autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="name@company.com" />
              </Field>
              {requestLink.error && <Alert tone="error">{explain(requestLink.error)}</Alert>}
              <Button type="submit" className="w-full" busy={requestLink.isPending}>Email me a sign-in link</Button>
            </form>
          )}
        </Card>

        {passwordEnabled && options.data?.demo_accounts && (
          <Card title="Test accounts (demo installation)">
            <p className="mb-3 text-xs text-slate-500">
              Every account uses the demo password set in <code className="rounded bg-slate-100 px-1">STAFF_DEMO_PASSWORD</code> (.env).
              Click an account to fill in its email. Sign in as different people to see each role.
            </p>
            <ul className="divide-y divide-slate-100">
              {options.data.demo_accounts.map((a) => (
                <li key={a.email}>
                  <button type="button" onClick={() => { setMethod('password'); setEmail(a.email) }}
                    className={cx('flex w-full items-center justify-between gap-3 rounded-lg px-2 py-2 text-left text-sm hover:bg-slate-50', email === a.email && 'bg-brand-50')}>
                    <span><span className="font-medium text-slate-900">{a.full_name}</span><span className="block text-xs text-slate-500">{a.email}</span></span>
                    <span className="text-right text-xs text-slate-600">{a.roles.map(humanize).join(', ')}</span>
                  </button>
                </li>
              ))}
            </ul>
          </Card>
        )}
      </div>
    </div>
  )
}
