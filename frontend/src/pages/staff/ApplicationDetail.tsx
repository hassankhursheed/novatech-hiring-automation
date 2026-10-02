import { useState, type ReactNode } from 'react'
import { Link, useParams } from 'react-router-dom'
import { PageTitle } from '../../components/layout'
import { Alert, Button, Card, Empty, Field, Input, KeyValue, Loading, Pill, StatusBadge, Textarea, cx } from '../../components/ui'
import { explain } from '../../lib/api'
import { formatDate, formatDateTime, formatMoney, formatScore, formatShort, humanize } from '../../lib/format'
import { useStaffAction, useStaffQuery } from '../../lib/staff'

interface RuleResult { rule_key: string; label: string; points_possible: number; points_awarded: number; matched: string[]; evidence: string }
interface Detail {
  application_id: string
  application_code: string
  correlation_id: string
  status: string
  status_changed_at: string
  review_reason: string | null
  application_score: number | null
  ai_recommendation: string | null
  interview_score: number | null
  final_score: number | null
  expected_salary: number | null
  salary_currency: string
  available_from: string | null
  candidate: { candidate_code: string; full_name: string; email: string; phone: string | null; city: string | null }
  position: { code: string; title: string; department: string }
  history: { from_status: string | null; to_status: string; reason: string | null; actor_type: string; actor_id: string; changed_at: string }[]
  scores: { scoring_version: number; score: number; route: string; breakdown: RuleResult[]; created_at: string }[]
  ai_analyses: { status: string; provider: string | null; model: string | null; technical_strength: number | null; experience_relevance: number | null; communication_indication: number | null; missing_skills: string[] | null; summary: string | null; recommendation: string | null; fallback_reason: string | null; attempts: number; created_at: string }[]
  interviews: { interview_id: string; interview_code: string; round: number; status: string; scheduled_start: string | null; interviewer: { full_name: string } }[]
  feedback: { interview_code: string; technical_skills: number; communication: number; problem_solving: number; experience: number; team_fit: number; interview_score: number; recommendation: string; comments: string }[]
  offers: { offer_id: string; offer_code: string; revision: number; status: string; monthly_salary: number; currency: string; joining_date: string; required_approval_levels: number; approvals: { level: number; decision: string }[]; next_level: number | null; candidate_message: string | null; expires_at: string | null }[]
  employee: { employee_id: string; employee_code: string; company_email: string; joining_date: string; tasks: { task_id: string; title: string; owner_role: string; due_date: string; status: string }[] } | null
  notifications: { template_key: string; recipient: string; status: string; created_at: string }[]
  staff_transitions: { to_status: string; description: string }[]
  timeline: { occurred_at: string; source: string; workflow_name: string | null; action: string; outcome: string; from_status: string | null; to_status: string | null; actor: string | null; error_code: string | null; error_message: string | null }[]
}

export default function ApplicationDetail() {
  const { id } = useParams()
  const detail = useStaffQuery<Detail>(['application', id], `/applications/${id}`)
  if (detail.isLoading) return <Loading />
  if (detail.error) return <Alert tone="error">{explain(detail.error)}</Alert>
  const a = detail.data!

  return (
    <>
      <PageTitle
        title={<span className="flex flex-wrap items-center gap-3">{a.candidate.full_name} <StatusBadge status={a.status} /></span>}
        subtitle={<>{a.application_code} · {a.position.title} · correlation id <span className="font-mono">{a.correlation_id}</span></>}
        actions={<Link to="/staff/applications" className="text-sm text-brand-700 hover:underline">← All applications</Link>}
      />
      {a.review_reason && ['SCREENING_REVIEW', 'INTERVIEW_REVIEW'].includes(a.status) && (
        <div className="mb-6"><Alert tone="warning" title="Why this needs a person">{a.review_reason}</Alert></div>
      )}
      <div className="grid gap-6 lg:grid-cols-3">
        <div className="space-y-6 lg:col-span-2">
          <Card title="Candidate">
            <KeyValue items={[
              ['Email', a.candidate.email], ['Phone', a.candidate.phone], ['City', a.candidate.city],
              ['Expected salary', formatMoney(a.expected_salary, a.salary_currency)], ['Available from', formatDate(a.available_from)],
              ['Scores', `screening ${formatScore(a.application_score)} · interview ${formatScore(a.interview_score)} · final ${formatScore(a.final_score)}`],
            ]} />
          </Card>
          <Screening a={a} />
          <Interviews a={a} />
          <Offers a={a} />
          {a.employee && <Onboarding employee={a.employee} />}
          <Timeline a={a} />
        </div>
        <div className="space-y-6">
          <Actions a={a} />
          <Card title="Status history">
            <ol className="space-y-3">
              {a.history.map((h, i) => (
                <li key={i} className="text-sm">
                  <div className="flex items-center justify-between gap-2"><StatusBadge status={h.to_status} /><span className="text-xs text-slate-500">{formatShort(h.changed_at)}</span></div>
                  {h.reason && <p className="mt-1 text-xs text-slate-600">{h.reason}</p>}
                  <p className="mt-0.5 text-xs text-slate-400">{humanize(h.actor_type)} {h.actor_type === 'SYSTEM' ? '' : `· ${h.actor_id.slice(0, 8)}`}</p>
                </li>
              ))}
            </ol>
          </Card>
          <Card title="Messages sent">
            {a.notifications.length === 0 ? <Empty>None yet.</Empty> : (
              <ul className="space-y-2 text-sm">
                {a.notifications.map((n, i) => (
                  <li key={i} className="flex items-start justify-between gap-2">
                    <span><span className="text-slate-900">{humanize(n.template_key.replace('.', ' '))}</span><br /><span className="text-xs text-slate-500">{n.recipient}</span></span>
                    <Pill tone={n.status === 'SENT' ? 'green' : n.status === 'FAILED' ? 'red' : 'slate'}>{humanize(n.status)}</Pill>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>
      </div>
    </>
  )
}

function Screening({ a }: { a: Detail }) {
  const score = a.scores[0]
  const ai = a.ai_analyses[0]
  return (
    <Card title="Screening">
      {!score ? <Empty>Not scored (the application went straight to review).</Empty> : (
        <>
          <p className="text-sm text-slate-700">Rule score <strong>{formatScore(score.score)}</strong> → {humanize(score.route)} (rules version {score.scoring_version})</p>
          <table className="mt-3 w-full text-sm">
            <tbody>
              {score.breakdown.map((r) => (
                <tr key={r.rule_key} className="border-b border-slate-100 last:border-0">
                  <td className="py-1.5 pr-3 text-slate-700">{r.label}</td>
                  <td className="py-1.5 pr-3 text-xs text-slate-500">{r.matched?.join(', ') || r.evidence}</td>
                  <td className={cx('py-1.5 text-right font-medium', r.points_awarded ? 'text-emerald-700' : 'text-slate-400')}>{r.points_awarded}/{r.points_possible}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
      {ai && (
        <div className="mt-5 rounded-lg bg-slate-50 p-4">
          <p className="text-xs font-medium uppercase tracking-wide text-slate-500">AI analysis · advisory only · {ai.provider ?? 'none'} {ai.model ?? ''}</p>
          {ai.status === 'FALLBACK' ? (
            <p className="mt-2 text-sm text-amber-800">Not used: {ai.fallback_reason} (after {ai.attempts} attempt{ai.attempts === 1 ? '' : 's'})</p>
          ) : (
            <>
              <p className="mt-2 text-sm text-slate-800">{ai.summary}</p>
              <div className="mt-2 flex flex-wrap gap-2 text-xs">
                <Pill tone="blue">Technical {ai.technical_strength}/10</Pill>
                <Pill tone="blue">Experience {ai.experience_relevance}/10</Pill>
                <Pill tone="blue">Communication {ai.communication_indication}/10</Pill>
                <Pill tone={ai.recommendation === 'REJECT' ? 'red' : ai.recommendation === 'SHORTLIST' ? 'green' : 'amber'}>Suggests {humanize(ai.recommendation)}</Pill>
              </div>
              {ai.missing_skills?.length ? <p className="mt-2 text-xs text-slate-600">Not evidenced: {ai.missing_skills.join(', ')}</p> : null}
            </>
          )}
        </div>
      )}
    </Card>
  )
}

function Interviews({ a }: { a: Detail }) {
  const cancel = useStaffAction<{ id: string; reason: string }>((v) => `/interviews/${v.id}/cancel`, (v) => ({ reason: v.reason }))
  const noShow = useStaffAction<{ id: string }>((v) => `/interviews/${v.id}/no-show`)
  if (!a.interviews.length) return null
  return (
    <Card title="Interviews">
      <div className="space-y-4">
        {a.interviews.map((iv) => {
          const fb = a.feedback.find((f) => f.interview_code === iv.interview_code)
          return (
            <div key={iv.interview_id} className="rounded-lg border border-slate-200 p-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <p className="text-sm font-medium text-slate-900">{iv.interview_code} · round {iv.round} · {iv.interviewer.full_name}</p>
                <Pill tone={iv.status === 'COMPLETED' ? 'green' : ['CANCELLED', 'EXPIRED', 'NO_SHOW'].includes(iv.status) ? 'red' : 'blue'}>{humanize(iv.status)}</Pill>
              </div>
              <p className="mt-1 text-xs text-slate-500">{formatDateTime(iv.scheduled_start)}</p>
              {fb && (
                <div className="mt-3 text-sm">
                  <p className="text-slate-700">Scorecard {formatScore(fb.interview_score)} · {humanize(fb.recommendation)} · tech {fb.technical_skills}, comm {fb.communication}, problem {fb.problem_solving}, exp {fb.experience}, fit {fb.team_fit}</p>
                  <p className="mt-1 text-xs italic text-slate-600">“{fb.comments}”</p>
                </div>
              )}
              {iv.status === 'CONFIRMED' && (
                <div className="mt-3 flex gap-2">
                  <Button variant="secondary" busy={noShow.isPending} onClick={() => noShow.mutate({ id: iv.interview_id })}>Mark no-show</Button>
                  <Button variant="ghost" busy={cancel.isPending} onClick={() => {
                    const reason = window.prompt('Reason for cancelling this interview?')
                    if (reason && reason.trim().length >= 3) cancel.mutate({ id: iv.interview_id, reason })
                  }}>Cancel interview</Button>
                </div>
              )}
            </div>
          )
        })}
        {(cancel.error || noShow.error) && <Alert tone="error">{explain(cancel.error ?? noShow.error)}</Alert>}
      </div>
    </Card>
  )
}

function Offers({ a }: { a: Detail }) {
  const decide = useStaffAction<{ id: string; decision: string; reason?: string }>((v) => `/offers/${v.id}/approval`, (v) => ({ decision: v.decision, reason: v.reason ?? null }))
  if (!a.offers.length) return null
  return (
    <Card title="Offers">
      <div className="space-y-3">
        {[...a.offers].reverse().map((o) => (
          <div key={o.offer_id} className="rounded-lg border border-slate-200 p-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="text-sm font-medium text-slate-900">{o.offer_code} · revision {o.revision} · {formatMoney(o.monthly_salary, o.currency)}</p>
              <Pill tone={['SENT', 'APPROVED', 'ACCEPTED'].includes(o.status) ? 'green' : ['REJECTED_BY_APPROVER', 'DECLINED', 'EXPIRED'].includes(o.status) ? 'red' : 'amber'}>{humanize(o.status)}</Pill>
            </div>
            <p className="mt-1 text-xs text-slate-500">Joining {formatDate(o.joining_date)} · {o.required_approval_levels} approval level(s){o.expires_at ? ` · expires ${formatShort(o.expires_at)}` : ''}</p>
            <div className="mt-2 flex flex-wrap gap-2">{o.approvals.map((ap) => <Pill key={ap.level} tone={ap.decision === 'APPROVED' ? 'green' : 'red'}>L{ap.level} {humanize(ap.decision)}</Pill>)}</div>
            {o.candidate_message && <p className="mt-2 text-xs italic text-slate-600">Candidate: “{o.candidate_message}”</p>}
            {o.status === 'PENDING_APPROVAL' && o.next_level && (
              <div className="mt-3 flex gap-2">
                <Button busy={decide.isPending} onClick={() => decide.mutate({ id: o.offer_id, decision: 'APPROVED' })}>Approve level {o.next_level}</Button>
                <Button variant="secondary" busy={decide.isPending} onClick={() => {
                  const reason = window.prompt('Why are you rejecting this offer? (shared with HR)')
                  if (reason && reason.trim().length >= 3) decide.mutate({ id: o.offer_id, decision: 'REJECTED', reason })
                }}>Reject</Button>
              </div>
            )}
          </div>
        ))}
        {decide.error && <Alert tone="error">{explain(decide.error)}</Alert>}
      </div>
    </Card>
  )
}

function Onboarding({ employee }: { employee: NonNullable<Detail['employee']> }) {
  const complete = useStaffAction<{ id: string }>((v) => `/onboarding-tasks/${v.id}/complete`)
  return (
    <Card title={`Onboarding · ${employee.employee_code} · ${employee.company_email}`}>
      <ul className="divide-y divide-slate-100">
        {employee.tasks.map((t) => (
          <li key={t.task_id} className="flex items-center justify-between gap-3 py-2 text-sm">
            <span><span className={t.status === 'DONE' ? 'text-slate-400 line-through' : 'text-slate-900'}>{t.title}</span><br /><span className="text-xs text-slate-500">{humanize(t.owner_role)} · due {formatDate(t.due_date)}</span></span>
            {t.status === 'DONE' ? <Pill tone="green">Done</Pill> : <Button variant="secondary" busy={complete.isPending && complete.variables?.id === t.task_id} onClick={() => complete.mutate({ id: t.task_id })}>Mark done</Button>}
          </li>
        ))}
      </ul>
      {complete.error && <div className="mt-3"><Alert tone="error">{explain(complete.error)}</Alert></div>}
    </Card>
  )
}

const DECISION_LABELS: Record<string, string> = {
  SHORTLISTED: 'Shortlist',
  REJECTED: 'Reject',
  SELECTED: 'Select for offer',
  VALIDATED: 'Re-run screening',
  SCREENING_REVIEW: 'Back to screening review',
  INTERVIEW_REVIEW: 'Send to hiring-manager review',
}

function Actions({ a }: { a: Detail }) {
  const [reason, setReason] = useState('')
  const [salary, setSalary] = useState('')
  const [joining, setJoining] = useState('')
  const transition = useStaffAction<{ to: string }>(() => `/applications/${a.application_id}/transition`, (v) => ({ to_status: v.to, reason, expected_from: a.status }))
  const offer = useStaffAction<void>(() => `/applications/${a.application_id}/offers`, () => ({
    ...(salary ? { monthly_salary: Number(salary.replace(/[^0-9]/g, '')) } : {}), ...(joining ? { joining_date: joining } : {}),
  }))
  const openOffer = a.offers.find((o) => o.status === 'NEGOTIATION')
  const closeNegotiation = useStaffAction<{ id: string }>((v) => `/offers/${v.id}/close-negotiation`, () => ({ reason }))
  const withdraw = useStaffAction<void>(() => `/applications/${a.application_id}/withdraw`, () => ({ reason }))
  const error = transition.error ?? offer.error ?? closeNegotiation.error ?? withdraw.error
  const closed = ['REJECTED', 'DECLINED', 'OFFER_EXPIRED', 'WITHDRAWN', 'ONBOARDED'].includes(a.status)
  const canOffer = ['SELECTED', 'NEGOTIATION'].includes(a.status)
  const reasonOk = reason.trim().length >= 3
  const section = (title: string, body: ReactNode) => <div className="space-y-3 border-t border-slate-100 pt-4 first:border-0 first:pt-0"><p className="text-xs font-medium uppercase tracking-wide text-slate-500">{title}</p>{body}</div>

  if (closed) return <Card title="Actions"><p className="text-sm text-slate-600">This application is closed.</p></Card>
  return (
    <Card title="Actions">
      <div className="space-y-4">
        <Field label="Reason" hint="Recorded with your name in the history."><Textarea rows={2} value={reason} onChange={(e) => setReason(e.target.value)} /></Field>
        {a.staff_transitions.length > 0 && section('Decision', (
          <div className="flex flex-wrap gap-2">
            {a.staff_transitions.filter((t) => t.to_status !== 'WITHDRAWN').map((t) => (
              <Button key={t.to_status} variant={t.to_status === 'REJECTED' ? 'danger' : 'primary'} disabled={!reasonOk} title={t.description}
                busy={transition.isPending && transition.variables?.to === t.to_status} onClick={() => transition.mutate({ to: t.to_status })}>
                {DECISION_LABELS[t.to_status] ?? humanize(t.to_status)}
              </Button>
            ))}
          </div>
        ))}
        {canOffer && section(openOffer ? 'Revise the offer' : 'Draft the offer', (
          <>
            <div className="grid grid-cols-2 gap-2">
              <Input placeholder="Monthly salary (PKR)" inputMode="numeric" value={salary} onChange={(e) => setSalary(e.target.value)} />
              <Input type="date" value={joining} onChange={(e) => setJoining(e.target.value)} />
            </div>
            <p className="text-xs text-slate-500">Empty fields use the defaults (expected salary within the band, earliest joining date). Goes to approval.</p>
            <div className="flex flex-wrap gap-2">
              <Button busy={offer.isPending} onClick={() => offer.mutate()}>{openOffer ? 'Send revision for approval' : 'Draft offer'}</Button>
              {openOffer && <Button variant="secondary" disabled={!reasonOk} busy={closeNegotiation.isPending} onClick={() => closeNegotiation.mutate({ id: openOffer.offer_id })}>Close negotiation</Button>}
            </div>
          </>
        ))}
        {section('Withdraw', <Button variant="ghost" disabled={!reasonOk} busy={withdraw.isPending} onClick={() => withdraw.mutate()}>Withdraw application</Button>)}
        {error && <Alert tone="error">{explain(error)}</Alert>}
      </div>
    </Card>
  )
}

function Timeline({ a }: { a: Detail }) {
  const [all, setAll] = useState(false)
  const items = all ? a.timeline : a.timeline.slice(-15)
  return (
    <Card title="Automation timeline" actions={a.timeline.length > 15 && <button className="text-xs text-brand-700" onClick={() => setAll(!all)}>{all ? 'Show recent' : `Show all ${a.timeline.length}`}</button>}>
      <p className="mb-3 text-xs text-slate-500">Every step any workflow or person took for this application, from <span className="font-mono">reporting.trace('{a.correlation_id}')</span>.</p>
      <ol className="space-y-2">
        {items.map((t, i) => (
          <li key={i} className="grid grid-cols-[7rem_1fr] gap-3 text-xs">
            <span className="text-slate-400">{formatShort(t.occurred_at)}</span>
            <span className={t.outcome === 'FAILURE' ? 'text-rose-700' : 'text-slate-700'}>
              <span className="font-medium">{humanize(t.action)}</span>
              {t.to_status && <> → {humanize(t.to_status)}</>}
              {t.workflow_name && <span className="text-slate-400"> · {t.workflow_name}</span>}
              {t.error_code && <span> · {t.error_code}: {t.error_message}</span>}
            </span>
          </li>
        ))}
      </ol>
    </Card>
  )
}
