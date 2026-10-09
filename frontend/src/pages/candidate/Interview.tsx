import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Narrow } from '../../components/layout'
import { Alert, Button, Card, Field, Loading, StatusBadge, Textarea, cx } from '../../components/ui'
import { api, explain, MISSING_LINK } from '../../lib/api'
import { TIMEZONE } from '../../lib/config'
import { useLinkToken } from '../../lib/session'

interface Slot { slot_id: string; starts_at: string; ends_at: string }
// The interview time and meeting details are only ever sent by email, never shown here.
interface InterviewView {
  interview_code: string
  round: number
  status: string
  application_code: string
  first_name: string
  position_title: string
  respond_by: string | null
  slots: Slot[]
}

const CLOSED_MESSAGES: Record<string, string> = {
  COMPLETED: 'Thank you for attending your interview. We will email you about the next steps.',
  CANCELLED: 'This interview is cancelled. Please check your email for the latest update.',
  EXPIRED: 'This invitation expired before a time was chosen. Please check your email or contact the recruitment team.',
  NO_SHOW: 'We missed you at the interview. Please check your email for the latest update.',
}

const dayFormat = new Intl.DateTimeFormat('en-GB', { timeZone: TIMEZONE, weekday: 'long', day: 'numeric', month: 'long' })
const timeFormat = new Intl.DateTimeFormat('en-GB', { timeZone: TIMEZONE, hour: 'numeric', minute: '2-digit', hour12: true })
// The deadline is midnight in the company timezone: every offered time is on a day before it.
const deadlineFormat = new Intl.DateTimeFormat('en-GB', { timeZone: TIMEZONE, weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' })

export default function Interview() {
  const token = useLinkToken('interview')
  const client = useQueryClient()
  const [slot, setSlot] = useState<string | null>(null)
  const [reason, setReason] = useState('')
  const view = useQuery({
    queryKey: ['interview', token],
    queryFn: () => api<InterviewView>('/v1/portal/interview', { token }),
    enabled: Boolean(token),
    retry: false,
  })
  const confirm = useMutation({
    mutationFn: () => api('/v1/portal/interview/confirm', { token, body: { slot_id: slot } }),
    onSuccess: () => client.invalidateQueries({ queryKey: ['interview', token] }),
  })
  const cancel = useMutation({
    mutationFn: () => api('/v1/portal/interview/cancel', { token, body: { reason } }),
    onSuccess: () => client.invalidateQueries({ queryKey: ['interview', token] }),
  })

  if (!token) return <Narrow title="Interview"><Alert tone="error">{MISSING_LINK}</Alert></Narrow>
  if (view.isLoading) return <Loading />
  if (view.error) return <Narrow title="Interview"><Alert tone="error">{explain(view.error)}</Alert></Narrow>
  const iv = view.data!

  const byDay = iv.slots.reduce<Record<string, Slot[]>>((days, s) => {
    const day = dayFormat.format(new Date(s.starts_at))
    ;(days[day] ??= []).push(s)
    return days
  }, {})

  return (
    <Narrow
      title={`Interview for ${iv.position_title}`}
      subtitle={<>Hi {iv.first_name}, this is your personal interview page (reference {iv.application_code}).{iv.status === 'INVITED' ? ` Times are shown in ${TIMEZONE.replace('_', ' ')} time.` : ''}</>}
    >
      {iv.status === 'INVITED' && (
        <Card title="Choose a time">
          {iv.respond_by && <p className="mb-4 text-sm text-slate-600">Please choose a time before <strong>{deadlineFormat.format(new Date(iv.respond_by))}</strong>. The meeting details are emailed to you as soon as you book.</p>}
          {iv.slots.length === 0 && <Alert tone="warning">No open times are left. The recruitment team will contact you.</Alert>}
          <div className="space-y-4">
            {Object.entries(byDay).map(([day, slots]) => (
              <div key={day}>
                <p className="text-sm font-medium text-slate-700">{day}</p>
                <div className="mt-2 flex flex-wrap gap-2">
                  {slots.map((s) => (
                    <button
                      key={s.slot_id}
                      type="button"
                      onClick={() => setSlot(s.slot_id)}
                      className={cx('rounded-lg px-3 py-2 text-sm ring-1 ring-inset', slot === s.slot_id ? 'bg-brand-600 text-white ring-brand-600' : 'bg-white text-slate-700 ring-slate-300 hover:bg-slate-50')}
                    >
                      {timeFormat.format(new Date(s.starts_at))} - {timeFormat.format(new Date(s.ends_at))}
                    </button>
                  ))}
                </div>
              </div>
            ))}
          </div>
          {confirm.error && <div className="mt-4"><Alert tone="error">{explain(confirm.error)}</Alert></div>}
          <Button className="mt-5" disabled={!slot} busy={confirm.isPending} onClick={() => confirm.mutate()}>Confirm this time</Button>
        </Card>
      )}

      {iv.status === 'CONFIRMED' && (
        <>
          <Alert tone="success" title="Your interview is booked">We have emailed you the date, the time and how to join. Please keep that email.</Alert>
          <Card title="Can't make it?">
            <p className="text-sm text-slate-600">Cancel so the time can be offered to someone else. We will send you a new invitation.</p>
            <div className="mt-4"><Field label="Reason" required><Textarea rows={2} value={reason} onChange={(e) => setReason(e.target.value)} /></Field></div>
            {cancel.error && <div className="mt-3"><Alert tone="error">{explain(cancel.error)}</Alert></div>}
            <Button variant="secondary" className="mt-4" disabled={reason.trim().length < 3} busy={cancel.isPending} onClick={() => cancel.mutate()}>Cancel interview</Button>
          </Card>
        </>
      )}

      {!['INVITED', 'CONFIRMED'].includes(iv.status) && (
        <Card>
          <div className="flex items-center gap-3"><StatusBadge status={iv.status} /><span className="text-sm text-slate-600">{CLOSED_MESSAGES[iv.status] ?? 'Please check your email for the latest update.'}</span></div>
        </Card>
      )}
    </Narrow>
  )
}
