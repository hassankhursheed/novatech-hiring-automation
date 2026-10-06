import { useState } from 'react'
import { humanize } from '../lib/format'
import { Alert, Button, Field, Select, Textarea, cx } from './ui'
import { explain } from '../lib/api'

/** The interview scorecard: 5 criteria rated 1-5, a recommendation and the evidence behind them. */
export const CRITERIA = [
  ['technical_skills', 'Technical skills'],
  ['communication', 'Communication'],
  ['problem_solving', 'Problem solving'],
  ['experience', 'Relevant experience'],
  ['team_fit', 'Team fit'],
] as const
export type Criterion = (typeof CRITERIA)[number][0]
export const RECOMMENDATIONS = ['STRONG_HIRE', 'HIRE', 'NO_HIRE', 'STRONG_NO_HIRE']

export type ScorecardValues = Record<Criterion, number> & { recommendation: string; comments: string }

export function ScorecardForm({ onSubmit, busy, error, submitLabel = 'Submit scorecard' }: {
  onSubmit: (values: ScorecardValues) => void
  busy?: boolean
  error?: unknown
  submitLabel?: string
}) {
  const [ratings, setRatings] = useState<Partial<Record<Criterion, number>>>({})
  const [recommendation, setRecommendation] = useState('')
  const [comments, setComments] = useState('')
  const complete = CRITERIA.every(([key]) => ratings[key]) && recommendation && comments.trim().length >= 3
  const total = CRITERIA.reduce((sum, [key]) => sum + (ratings[key] ?? 0), 0)

  return (
    <div className="space-y-4">
      {CRITERIA.map(([key, label]) => (
        <div key={key} className="flex flex-wrap items-center justify-between gap-3">
          <span className="text-sm font-medium text-slate-700">{label}</span>
          <div className="flex gap-1" role="radiogroup" aria-label={label}>
            {[1, 2, 3, 4, 5].map((n) => (
              <button key={n} type="button" role="radio" aria-checked={ratings[key] === n}
                onClick={() => setRatings({ ...ratings, [key]: n })}
                className={cx('h-9 w-9 rounded-lg text-sm font-medium ring-1 ring-inset', ratings[key] === n ? 'bg-brand-600 text-white ring-brand-600' : 'bg-white text-slate-700 ring-slate-300 hover:bg-slate-50')}>
                {n}
              </button>
            ))}
          </div>
        </div>
      ))}
      <p className="flex flex-wrap justify-between gap-2 text-xs text-slate-500">
        <span>1 = poor · 3 = meets expectations · 5 = exceptional</span>
        {total > 0 && <span>Interview score so far: <strong className="text-slate-700">{Math.round((total / 25) * 100)}</strong> / 100</span>}
      </p>
      <Field label="Recommendation" required>
        <Select value={recommendation} onChange={(e) => setRecommendation(e.target.value)}>
          <option value="" disabled>Choose</option>
          {RECOMMENDATIONS.map((r) => <option key={r} value={r}>{humanize(r)}</option>)}
        </Select>
      </Field>
      <Field label="Comments" required hint="Evidence for your ratings. The hiring manager and the AI assessment read this.">
        <Textarea value={comments} onChange={(e) => setComments(e.target.value)} maxLength={4000} />
      </Field>
      {error ? <Alert tone="error">{explain(error)}</Alert> : null}
      <Button disabled={!complete} busy={busy}
        onClick={() => onSubmit({ ...(ratings as Record<Criterion, number>), recommendation, comments: comments.trim() })}>
        {submitLabel}
      </Button>
    </div>
  )
}
