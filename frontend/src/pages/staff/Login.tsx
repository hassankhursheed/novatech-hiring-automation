import { useMutation } from '@tanstack/react-query'
import { useEffect, useRef, useState, type FormEvent } from 'react'
import { Navigate, useLocation, useNavigate } from 'react-router-dom'
import { Narrow } from '../../components/layout'
import { Alert, Button, Card, Field, Input, Loading } from '../../components/ui'
import { api, explain } from '../../lib/api'
import { readLinkToken, saveSession, useStaffSession, type StaffProfile } from '../../lib/session'

interface Session { token: string; expires_at: string; staff: StaffProfile }

export default function Login() {
  const session = useStaffSession()
  const navigate = useNavigate()
  const location = useLocation()
  const from = (location.state as { from?: string } | null)?.from ?? '/staff'
  const [email, setEmail] = useState('')
  const [linkToken, setLinkToken] = useState(() => readLinkToken('staff-login'))

  const exchange = useMutation({
    mutationFn: (token: string) => api<Session>('/v1/auth/staff/session', { method: 'POST', token }),
    onSettled: () => sessionStorage.removeItem('link:staff-login'),  // a sign-in link is spent either way
    onSuccess: (data) => {
      saveSession(data)
      navigate(from, { replace: true })
    },
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

  function submit(event: FormEvent) {
    event.preventDefault()
    requestLink.mutate()
  }

  return (
    <Narrow title="Staff sign-in" subtitle="We email you a one-time sign-in link. No password needed.">
      {exchange.error && <Alert tone="error">{explain(exchange.error)}</Alert>}
      <Card>
        {requestLink.isSuccess ? (
          <Alert tone="success" title="Check your inbox">{requestLink.data.status} The link works once and expires in 15 minutes.</Alert>
        ) : (
          <form onSubmit={submit} className="space-y-4">
            <Field label="Work email" required>
              <Input type="email" required autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="name@company.com" />
            </Field>
            {requestLink.error && <Alert tone="error">{explain(requestLink.error)}</Alert>}
            <Button type="submit" busy={requestLink.isPending}>Email me a sign-in link</Button>
          </form>
        )}
      </Card>
    </Narrow>
  )
}
