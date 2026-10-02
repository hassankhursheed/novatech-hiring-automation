import { Link } from 'react-router-dom'
import { PageTitle } from '../../components/layout'
import { Alert, Button, Card, Empty, Loading, Pill } from '../../components/ui'
import { explain } from '../../lib/api'
import { formatDate, formatShort, humanize } from '../../lib/format'
import { useStaffAction, useStaffQuery } from '../../lib/staff'

interface Employee {
  employee_id: string
  employee_code: string
  full_name: string
  position: string
  department: string
  joining_date: string
  company_email: string
  application_id: string
  manager: { full_name: string } | null
  tasks: { task_id: string; title: string; owner_role: string; due_date: string; status: string }[]
}

export function OnboardingBoard() {
  const board = useStaffQuery<Employee[]>(['onboarding'], '/onboarding', { refetchInterval: 60_000 })
  const complete = useStaffAction<{ id: string }>((v) => `/onboarding-tasks/${v.id}/complete`)
  const today = new Date().toISOString().slice(0, 10)
  return (
    <>
      <PageTitle title="Onboarding" subtitle="New employees with open tasks. Owners get a reminder when a task is overdue." />
      {board.isLoading ? <Loading /> : board.error ? <Alert tone="error">{explain(board.error)}</Alert> : !board.data?.length ? <Card><Empty>Nobody is onboarding right now.</Empty></Card> : (
        <div className="grid gap-6 lg:grid-cols-2">
          {board.data.map((e) => {
            const open = e.tasks.filter((t) => t.status !== 'DONE')
            return (
              <Card key={e.employee_id} title={<Link className="hover:underline" to={`/staff/applications/${e.application_id}`}>{e.full_name} · {e.employee_code}</Link>}
                actions={<Pill tone="blue">{open.length} open</Pill>}>
                <p className="text-sm text-slate-600">{e.position} · {e.department} · joins {formatDate(e.joining_date)} · manager {e.manager?.full_name ?? '-'}</p>
                <ul className="mt-3 divide-y divide-slate-100">
                  {e.tasks.map((t) => (
                    <li key={t.task_id} className="flex items-center justify-between gap-3 py-2 text-sm">
                      <span>
                        <span className={t.status === 'DONE' ? 'text-slate-400 line-through' : 'text-slate-900'}>{t.title}</span><br />
                        <span className="text-xs text-slate-500">{humanize(t.owner_role)} · due {formatDate(t.due_date)}</span>
                        {t.status !== 'DONE' && t.due_date < today && <Pill tone="red">overdue</Pill>}
                      </span>
                      {t.status === 'DONE' ? <Pill tone="green">Done</Pill>
                        : <Button variant="secondary" busy={complete.isPending && complete.variables?.id === t.task_id} onClick={() => complete.mutate({ id: t.task_id })}>Mark done</Button>}
                    </li>
                  ))}
                </ul>
              </Card>
            )
          })}
        </div>
      )}
      {complete.error && <div className="mt-4"><Alert tone="error">{explain(complete.error)}</Alert></div>}
    </>
  )
}

interface ErrorRow {
  error_id: string
  status: string
  error_class: string
  error_code: string
  error_message: string
  workflow_name: string
  node_name: string | null
  entity_type: string | null
  correlation_id: string | null
  retry_count: number
  occurrence_count: number
  replay_workflow: string | null
  replay_count: number
  created_at: string
}

export function ErrorQueue() {
  const errors = useStaffQuery<ErrorRow[]>(['queue', 'errors'], '/queues/errors?limit=200', { refetchInterval: 30_000 })
  const replay = useStaffAction<{ id: string }>((v) => `/errors/${v.id}/replay`)
  return (
    <>
      <PageTitle title="Automation errors" subtitle="Failures that need a decision. Fix the cause, then replay: replays reuse the original keys, so they never create duplicates." />
      {replay.isSuccess && <div className="mb-4"><Alert tone="success" title="Replay finished">{String((replay.data as { detail?: string } | undefined)?.detail ?? '')}</Alert></div>}
      {replay.error && <div className="mb-4"><Alert tone="error">{explain(replay.error)}</Alert></div>}
      <Card>
        {errors.isLoading ? <Loading /> : errors.error ? <Alert tone="error">{explain(errors.error)}</Alert> : !errors.data?.length ? <Empty>No open errors. Everything is flowing.</Empty> : (
          <ul className="divide-y divide-slate-100">
            {errors.data.map((e) => (
              <li key={e.error_id} className="flex flex-wrap items-start justify-between gap-4 py-4">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-mono text-sm font-semibold text-slate-900">{e.error_code}</span>
                    <Pill tone={e.error_class === 'RETRYABLE' ? 'amber' : e.error_class === 'NON_RETRYABLE' ? 'red' : 'slate'}>{humanize(e.error_class)}</Pill>
                    <Pill>{humanize(e.status)}</Pill>
                    {e.occurrence_count > 1 && <Pill tone="amber">seen {e.occurrence_count}×</Pill>}
                  </div>
                  <p className="mt-1 text-sm text-slate-700">{e.error_message}</p>
                  <p className="mt-1 text-xs text-slate-500">
                    {e.workflow_name} / {e.node_name ?? '-'} · {humanize(e.entity_type)} · {e.correlation_id ?? 'no correlation id'} · {formatShort(e.created_at)}
                    {e.replay_count > 0 && ` · replayed ${e.replay_count}×`}
                  </p>
                </div>
                <Button variant="secondary" disabled={!e.replay_workflow} title={e.replay_workflow ? `Re-run in ${e.replay_workflow}` : 'No automatic replay; resolve manually'}
                  busy={replay.isPending && replay.variables?.id === e.error_id} onClick={() => replay.mutate({ id: e.error_id })}>
                  Replay
                </Button>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </>
  )
}
