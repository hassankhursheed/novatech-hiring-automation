import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Narrow } from '../../components/layout'
import { Alert, Button, Card, Field, KeyValue, Loading, Textarea } from '../../components/ui'
import { api, explain, MISSING_LINK } from '../../lib/api'
import { formatDate, formatDateTime, formatMoney } from '../../lib/format'
import { useLinkToken } from '../../lib/session'

interface OfferView {
  offer_code: string
  revision: number
  status: string
  position_title: string
  department: string
  monthly_salary: number
  currency: string
  joining_date: string
  probation_months: number
  reporting_manager: string | null
  expires_at: string | null
  candidate_response: string | null
  candidate_first_name: string
  company: string
  document_available: boolean
}

type Response = 'ACCEPT' | 'DECLINE' | 'NEGOTIATE'
const outcome: Record<string, { tone: 'success' | 'info' | 'warning'; text: string }> = {
  ACCEPTED: { tone: 'success', text: 'You accepted this offer. Welcome aboard! Your onboarding details will arrive by email.' },
  DECLINED: { tone: 'info', text: 'You declined this offer. Thank you for letting us know.' },
  NEGOTIATION: { tone: 'info', text: 'Thanks for your message. Our HR team is reviewing it and will get back to you.' },
  EXPIRED: { tone: 'warning', text: 'This offer has expired. Please contact the recruitment team if you are still interested.' },
  SUPERSEDED: { tone: 'info', text: 'A revised offer was sent to you. Please use the link in the latest email.' },
}

export default function Offer() {
  const token = useLinkToken('offer')
  const client = useQueryClient()
  const [choice, setChoice] = useState<Response | null>(null)
  const [message, setMessage] = useState('')
  const view = useQuery({ queryKey: ['offer', token], queryFn: () => api<OfferView>('/v1/portal/offer', { token }), enabled: Boolean(token), retry: false })
  const respond = useMutation({
    mutationFn: () => api('/v1/portal/offer/respond', { token, body: { response: choice, message: message.trim() || null } }),
    onSuccess: () => client.invalidateQueries({ queryKey: ['offer', token] }),
  })
  const download = useMutation({
    mutationFn: async () => {
      const blob = await api<Blob>('/v1/portal/offer/document', { token })
      const url = URL.createObjectURL(blob)
      const a = Object.assign(document.createElement('a'), { href: url, download: `${view.data?.offer_code ?? 'offer'}.pdf` })
      a.click()
      URL.revokeObjectURL(url)
    },
  })

  if (!token) return <Narrow title="Your offer"><Alert tone="error">{MISSING_LINK}</Alert></Narrow>
  if (view.isLoading) return <Loading />
  if (view.error) return <Narrow title="Your offer"><Alert tone="error">{explain(view.error)}</Alert></Narrow>
  const o = view.data!
  const open = o.status === 'SENT'

  return (
    <Narrow title={`Offer: ${o.position_title}`} subtitle={<>Dear {o.candidate_first_name}, here is your offer from {o.company} (reference {o.offer_code}).</>}>
      {outcome[o.status] && <Alert tone={outcome[o.status].tone}>{outcome[o.status].text}</Alert>}
      <Card title="Terms" actions={o.document_available && (
        <Button variant="secondary" busy={download.isPending} onClick={() => download.mutate()}>Download offer letter (PDF)</Button>
      )}>
        <KeyValue items={[
          ['Monthly gross salary', <strong key="s">{formatMoney(o.monthly_salary, o.currency)}</strong>],
          ['Joining date', formatDate(o.joining_date)],
          ['Department', o.department],
          ['Probation', `${o.probation_months} month(s)`],
          ['Reporting to', o.reporting_manager],
          ['Valid until', formatDateTime(o.expires_at)],
        ]} />
        {download.error && <div className="mt-4"><Alert tone="error">{explain(download.error)}</Alert></div>}
      </Card>

      {open && (
        <Card title="Your answer">
          <div className="grid gap-3 sm:grid-cols-3">
            {([['ACCEPT', 'Accept the offer'], ['NEGOTIATE', 'Discuss the terms'], ['DECLINE', 'Decline']] as [Response, string][]).map(([value, label]) => (
              <label key={value} className={`cursor-pointer rounded-lg p-3 text-sm ring-1 ring-inset ${choice === value ? 'bg-brand-50 ring-brand-500' : 'ring-slate-300 hover:bg-slate-50'}`}>
                <input type="radio" name="response" className="sr-only" checked={choice === value} onChange={() => setChoice(value)} />
                <span className="font-medium text-slate-900">{label}</span>
              </label>
            ))}
          </div>
          {choice && (
            <div className="mt-4">
              <Field label={choice === 'NEGOTIATE' ? 'What would you like to discuss?' : 'Message (optional)'} required={choice === 'NEGOTIATE'}>
                <Textarea rows={3} maxLength={2000} value={message} onChange={(e) => setMessage(e.target.value)} />
              </Field>
            </div>
          )}
          {respond.error && <div className="mt-4"><Alert tone="error">{explain(respond.error)}</Alert></div>}
          <Button
            className="mt-5"
            variant={choice === 'DECLINE' ? 'danger' : 'primary'}
            disabled={!choice || (choice === 'NEGOTIATE' && message.trim().length < 3)}
            busy={respond.isPending}
            onClick={() => respond.mutate()}
          >
            Send my answer
          </Button>
        </Card>
      )}
    </Narrow>
  )
}
