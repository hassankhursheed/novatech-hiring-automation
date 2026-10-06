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

Without `staff.csv` the sample NovaTech staff from `db/seed/010_novatech_config.sql` (addresses on the reserved
`novatech.example` domain, which cannot receive mail) stay in place.
