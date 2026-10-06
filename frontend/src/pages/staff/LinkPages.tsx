import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Narrow } from '../../components/layout'
import { ScorecardForm, type ScorecardValues } from '../../components/scorecard'
import { Alert, Button, Card, Field, KeyValue, Loading, Pill, StatusBadge, Textarea } from '../../components/ui'
import { api, explain, MISSING_LINK } from '../../lib/api'
import { formatDate, formatDateTime, formatMoney, formatScore, humanize } from '../../lib/format'
import { useLinkToken } from '../../lib/session'

// ---- interviewer scorecard ---------------------------------------------------------------------------------
interface InterviewForStaff {
  interview_code: string
  status: string
  scheduled_start: string | null
  meeting_url: string | null
  interviewer_name: string
  candidate_name: string
  application_code: string
  position_title: string
  experience_years: number | null
  skills: string[] | null
  application_score: number | null
  feedback_submitted: boolean
}

export function Feedback() {
  const token = useLinkToken('feedback')
  const client = useQueryClient()
  const view = useQuery({ queryKey: ['feedback', token], queryFn: () => api<InterviewForStaff>('/v1/portal/feedback', { token }), enabled: Boolean(token), retry: false })
  const submit = useMutation({
    mutationFn: (values: ScorecardValues) => api<{ interview_score: string }>('/v1/portal/feedback', { token, body: values }),
    onSuccess: () => client.invalidateQueries({ queryKey: ['feedback', token] }),
  })

  if (!token) return <Narrow title="Interview scorecard"><Alert tone="error">{MISSING_LINK}</Alert></Narrow>
  if (view.isLoading) return <Loading />
  if (view.error) return <Narrow title="Interview scorecard"><Alert tone="error">{explain(view.error)}</Alert></Narrow>
  const iv = view.data!

  return (
    <Narrow title={`Scorecard: ${iv.candidate_name}`} subtitle={`${iv.position_title} · ${iv.interview_code} · ${formatDateTime(iv.scheduled_start)}`}>
      <Card title="Candidate">
        <KeyValue items={[
          ['Application', iv.application_code],
          ['Screening score', formatScore(iv.application_score)],
          ['Experience', iv.experience_years !== null ? `${iv.experience_years} year(s)` : '-'],
          ['Skills', iv.skills?.join(', ') || '-'],
        ]} />
      </Card>
      {iv.feedback_submitted || submit.isSuccess ? (
        <Alert tone="success" title="Scorecard submitted">Thank you. The candidate's final score is calculated automatically and the hiring team is informed.</Alert>
      ) : iv.status !== 'CONFIRMED' ? (
        <Alert tone="warning">This interview is {humanize(iv.status).toLowerCase()}; no scorecard is needed.</Alert>
      ) : (
        <Card title="Your assessment">
          <ScorecardForm onSubmit={(values) => submit.mutate(values)} busy={submit.isPending} error={submit.error} />
        </Card>
      )}
    </Narrow>
  )
}

// ---- offer approval ------------------------------------------------------------------------------------------
interface ApprovalView {
  offer_code: string
  revision: number
  status: string
  candidate_name: string
  application_code: string
  position_title: string
  department: string
  monthly_salary: number
  currency: string
  expected_salary: number | null
  joining_date: string
  probation_months: number
  application_score: number | null
  interview_score: number | null
  final_score: number | null
  required_approval_levels: number
  approval_threshold_applied: number
  approvals: { level: number; decision: string }[]
  next_level: number | null
  can_decide: boolean
}

export function Approval() {
  const token = useLinkToken('approval')
  const client = useQueryClient()
  const [reason, setReason] = useState('')
  const view = useQuery({ queryKey: ['approval', token], queryFn: () => api<ApprovalView>('/v1/portal/approval', { token }), enabled: Boolean(token), retry: false })
  const decide = useMutation({
    mutationFn: (decision: 'APPROVED' | 'REJECTED') => api('/v1/portal/approval', { token, body: { decision, reason: reason.trim() || null } }),
    onSuccess: () => client.invalidateQueries({ queryKey: ['approval', token] }),
  })

  if (!token) return <Narrow title="Offer approval"><Alert tone="error">{MISSING_LINK}</Alert></Narrow>
  if (view.isLoading) return <Loading />
  if (view.error) return <Narrow title="Offer approval"><Alert tone="error">{explain(view.error)}</Alert></Narrow>
  const o = view.data!

  return (
    <Narrow title={`Approve offer ${o.offer_code}`} subtitle={`${o.candidate_name} · ${o.position_title} · revision ${o.revision}`}>
      <Card title="Offer" actions={<StatusBadge status={o.status === 'PENDING_APPROVAL' ? 'OFFER_PENDING_APPROVAL' : o.status} />}>
        <KeyValue items={[
          ['Monthly salary', <strong key="s">{formatMoney(o.monthly_salary, o.currency)}</strong>],
          ['Candidate expected', formatMoney(o.expected_salary, o.currency)],
          ['Joining date', formatDate(o.joining_date)],
          ['Probation', `${o.probation_months} month(s)`],
          ['Scores', `screening ${formatScore(o.application_score)} · interview ${formatScore(o.interview_score)} · final ${formatScore(o.final_score)}`],
          ['Approval levels', o.required_approval_levels === 2 ? `2 (salary above ${formatMoney(o.approval_threshold_applied, o.currency)})` : '1'],
        ]} />
        <div className="mt-4 flex flex-wrap gap-2">
          {o.approvals.map((a) => <Pill key={a.level} tone={a.decision === 'APPROVED' ? 'green' : 'red'}>Level {a.level}: {humanize(a.decision)}</Pill>)}
          {o.next_level && <Pill tone="amber">Waiting for level {o.next_level}</Pill>}
        </div>
      </Card>
      {decide.isSuccess && <Alert tone="success">Your decision was recorded.</Alert>}
      {o.can_decide && !decide.isSuccess ? (
        <Card title={`Your decision (level ${o.next_level})`}>
          <Field label="Reason" hint="Required when rejecting; it is shared with HR."><Textarea rows={3} value={reason} onChange={(e) => setReason(e.target.value)} /></Field>
          {decide.error && <div className="mt-4"><Alert tone="error">{explain(decide.error)}</Alert></div>}
          <div className="mt-5 flex gap-3">
            <Button busy={decide.isPending && decide.variables === 'APPROVED'} onClick={() => decide.mutate('APPROVED')}>Approve</Button>
            <Button variant="danger" disabled={reason.trim().length < 3} busy={decide.isPending && decide.variables === 'REJECTED'} onClick={() => decide.mutate('REJECTED')}>Reject</Button>
          </div>
        </Card>
      ) : !decide.isSuccess && (
        <Alert tone="info">{o.status === 'PENDING_APPROVAL' ? 'This level is assigned to another approver, or you already decided an earlier level.' : `This offer is ${humanize(o.status).toLowerCase()}; no decision is needed.`}</Alert>
      )}
    </Narrow>
  )
}
