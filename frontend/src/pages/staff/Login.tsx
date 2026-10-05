import { useMutation, useQuery } from '@tanstack/react-query'
import { useEffect, useRef, useState, type FormEvent } from 'react'
import { Navigate, useLocation, useNavigate } from 'react-router-dom'
import { Narrow } from '../../components/layout'
import { Alert, Button, Card, Field, Input, Loading } from '../../components/ui'
import { api, ApiError, explain } from '../../lib/api'
import { DEV_MAILBOX_URL } from '../../lib/config'
import { readLinkToken, saveSession, useStaffSession, type StaffProfile } from '../../lib/session'

interface Session { token: string; expires_at: string; staff: StaffProfile }
interface Options {
  email_link: boolean
  password: boolean
  demo_login?: { email: string; password: string; full_name: string } | null
}

export default function Login() {
  const session = useStaffSession()
  const navigate = useNavigate()
  const location = useLocation()
  const from = (location.state as { from?: string } | null)?.from ?? '/staff'
  const [linkToken, setLinkToken] = useState(() => readLinkToken('staff-login'))
  const options = useQuery({ queryKey: ['sign-in-options'], queryFn: () => api<Options>('/v1/auth/staff/options'), staleTime: 60_000 })
  const [useLink, setUseLink] = useState(false)
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

  const passwordMode = Boolean(options.data?.password) && !useLink
  const demo = options.data?.demo_login
  const passwordError = passwordLogin.error instanceof ApiError && passwordLogin.error.code === 'INVALID_CREDENTIALS'
    ? 'Email or password is incorrect.'
    : passwordLogin.error ? explain(passwordLogin.error) : null

  return (
    <Narrow
      title="Staff sign-in"
      subtitle={passwordMode ? 'Sign in with your work email and password.' : 'We email you a one-time sign-in link. No password needed.'}
    >
      {exchange.error && <Alert tone="error">{explain(exchange.error)}</Alert>}
      <Card>
        {passwordMode ? (
          <form className="space-y-4" onSubmit={(e: FormEvent) => { e.preventDefault(); passwordLogin.mutate() }}>
            <Field label="Work email" required>
              <Input type="email" required autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="name@company.com" />
            </Field>
            <Field label="Password" required>
              <Input type="password" required autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} />
            </Field>
            {passwordError && <Alert tone="error">{passwordError}</Alert>}
            <Button type="submit" busy={passwordLogin.isPending}>Sign in</Button>
          </form>
        ) : requestLink.isSuccess ? (
          <div className="space-y-4">
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
          <form onSubmit={(e: FormEvent) => { e.preventDefault(); requestLink.mutate() }} className="space-y-4">
            <Field label="Work email" required>
              <Input type="email" required autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="name@company.com" />
            </Field>
            {requestLink.error && <Alert tone="error">{explain(requestLink.error)}</Alert>}
            <Button type="submit" busy={requestLink.isPending}>Email me a sign-in link</Button>
          </form>
        )}
      </Card>

      {passwordMode && demo && (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-dashed border-slate-300 px-4 py-3 text-sm text-slate-600">
          <span>Demo HR login: <span className="font-medium text-slate-900">{demo.email}</span></span>
          <Button variant="secondary" onClick={() => { setEmail(demo.email); setPassword(demo.password) }}>Use demo login</Button>
        </div>
      )}

      {options.data?.password && (
        <p className="text-center text-sm">
          <button type="button" className="text-brand-700 hover:underline" onClick={() => { setUseLink(!useLink); requestLink.reset() }}>
            {useLink ? 'Sign in with email and password instead' : 'Sign in with an email link instead'}
          </button>
        </p>
      )}
    </Narrow>
  )
}
