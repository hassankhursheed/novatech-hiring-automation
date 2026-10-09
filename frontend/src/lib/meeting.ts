/** How the candidate joins the interview. Candidates only receive it after booking a time. */
export interface MeetingValues {
  mode: 'ONLINE' | 'ONSITE'
  meeting_url: string
  meeting_id: string
  meeting_passcode: string
  meeting_notes: string
}

export interface Meeting {
  mode: string
  meeting_url: string | null
  meeting_id: string | null
  meeting_passcode: string | null
  meeting_notes: string | null
}

const LINK = /^https?:\/\/\S+$/

export const meetingValues = (m?: Partial<Meeting> | null): MeetingValues => ({
  mode: m?.mode === 'ONSITE' ? 'ONSITE' : 'ONLINE',
  meeting_url: m?.meeting_url ?? '',
  meeting_id: m?.meeting_id ?? '',
  meeting_passcode: m?.meeting_passcode ?? '',
  meeting_notes: m?.meeting_notes ?? '',
})

export const validLink = (url: string) => LINK.test(url.trim())

export const meetingComplete = (v: MeetingValues) =>
  v.mode === 'ONLINE' ? validLink(v.meeting_url) : v.meeting_notes.trim().length > 0

/** What the API expects: blank fields as null. */
export const meetingPayload = (v: MeetingValues) => ({
  mode: v.mode,
  meeting_url: v.mode === 'ONLINE' ? v.meeting_url.trim() || null : null,
  meeting_id: v.mode === 'ONLINE' ? v.meeting_id.trim() || null : null,
  meeting_passcode: v.mode === 'ONLINE' ? v.meeting_passcode.trim() || null : null,
  meeting_notes: v.meeting_notes.trim() || null,
})

export const hasMeeting = (m?: Partial<Meeting> | null) => Boolean(m?.meeting_url || (m?.mode === 'ONSITE' && m?.meeting_notes))
