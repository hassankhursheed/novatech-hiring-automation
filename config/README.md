# Company configuration

Files here describe the real company and are applied by the `seed` service on every start
(`docker compose up -d seed` after a change). Files with real names and addresses are git-ignored.

## staff.csv

The people who use the HR portal and receive staff email (review alerts, interview briefs, approval requests,
sign-in links). Copy `staff.example.csv` to `staff.csv` and edit it.

| Column | Meaning |
|---|---|
| `id` | Keep the ids of the example rows: positions refer to them (hiring manager, default interviewer). Add new people with a new UUID. |
| `full_name` | Shown in emails and the portal |
| `email` | Work email (lower case); staff sign in with a one-time link sent here |
| `department`, `job_title` | Shown in the portal |
| `roles` | `|`-separated: `HR_ADMIN`, `RECRUITER`, `HIRING_MANAGER`, `INTERVIEWER`, `APPROVER_L1`, `APPROVER_L2`, `IT_ADMIN` |
| `active` | `false` removes access without deleting the history |

**Who approves offers.** Level 1: the `APPROVER_L1` staff of the offer's department (or the position's hiring
manager); level 2 (salaries above `offer.second_approval_threshold`): any `APPROVER_L2`. The person who drafted an
offer, or who approved the other level, may only approve when nobody else can; that waiver is recorded in the audit
log. To let HR approve everything, give the HR row `APPROVER_L1|APPROVER_L2` and remove those roles from the others.

**Who decides on applications.** Shortlisting, rejecting, withdrawing, offers and interview changes: `HR_ADMIN`,
`RECRUITER` and the hiring manager of the position (interview changes also the interviewer).

**Interview availability.** Each interviewer's weekly interview hours are in `hiring.interviewer_availability`
(seeded in `db/seed/010_novatech_config.sql`: Monday to Friday, 11:00, 12:00, 14:30, 15:30, 16:30, 45 minutes).

Without `staff.csv` the sample NovaTech staff from `db/seed/010_novatech_config.sql` (addresses on the reserved
`novatech.example` domain, which cannot receive mail) stay in place.
