# Scenario test report

Run `448638` on 2026-10-02 20:11 UTC · AI provider `stub` · duration 9.5 min

**Scenarios: 23/23 passed** · **Bulk screening: 42/42 as expected**

Everything was driven through public interfaces (intake webhook, CV upload, signed-link portal API, staff API, ops webhooks). Evidence comes from PostgreSQL (status history, logs, error queue, notifications) and Mailpit. Waiting days was replaced by moving timers' `run_at` to now (time travel touches scheduling data only).

| # | Scenario | Result | Time |
|---|---|---|---|
| 1 | Strong candidate becomes an employee | pass | 35.2 s |
| 2 | Incomplete application goes to manual review with a reason | pass | 5.5 s |
| 3 | Duplicate candidate submission is detected | pass | 10.7 s |
| 4 | Same webhook replayed creates no duplicates | pass | 12.0 s |
| 5 | Weak candidate is rejected by the rules | pass | 8.0 s |
| 6 | A rule changed in the database is used without editing workflows | pass | 16.0 s |
| 7 | Malformed AI output is handled (one retry, then fallback to review) | pass | 7.9 s |
| 8 | A retryable failure succeeds after retries | pass | 13.2 s |
| 9 | A non-retryable failure goes to the error queue | pass | 7.7 s |
| 10 | Retries exhausted -> dead queue | pass | 49.7 s |
| 11 | A corrected item is replayed without duplicates | pass | 12.2 s |
| 12 | Interview invitation reminder, then expiry | pass | 18.8 s |
| 13 | Interview cancellation releases the slot and re-invites | pass | 20.3 s |
| 14 | Missing interview feedback: reminder, then escalation | pass | 20.4 s |
| 15 | Candidate fails the interview | pass | 19.0 s |
| 16 | Standard offer: one approval level | pass | 25.9 s |
| 17 | High salary: two approval levels by different people | pass | 28.2 s |
| 18 | Approver rejects the offer | pass | 26.2 s |
| 19 | Candidate declines the offer | pass | 31.4 s |
| 20 | Candidate negotiates; HR sends a revised offer | pass | 39.9 s |
| 21 | Unanswered offer: reminder, then expiry | pass | 43.8 s |
| 22 | Onboarding runs exactly once, even if the step is delivered twice | pass | 35.3 s |
| 23 | Overdue onboarding task is visible and its owner reminded | pass | 6.3 s |

## Bulk screening of 42 fictional applications

| Position | Outcome | Count |
|---|---|---|
| BDE | REJECTED | 3 |
| BDE | SCREENING_REVIEW | 5 |
| BDE | SHORTLISTED | 4 |
| Blockchain Wizard | INVALID | 3 |
| PY_DEV | INVALID | 3 |
| PY_DEV | REJECTED | 3 |
| PY_DEV | SCREENING_REVIEW | 5 |
| PY_DEV | SHORTLISTED | 4 |
| QA_ENG | REJECTED | 3 |
| QA_ENG | SCREENING_REVIEW | 5 |
| QA_ENG | SHORTLISTED | 4 |

| Application | Profile | Inputs | Expected | Actual | Rule score | AI (advisory) | OK |
|---|---|---|---|---|---|---|---|
| APP-2026-00126 Ahmed Raza | PY_DEV/strong | 0300-0808001, 220k, 4, 2026-11-16 | SHORTLISTED | SHORTLISTED | 100.0 SHORTLIST | REVIEW | yes |
| APP-2026-00127 Sara Iqbal | PY_DEV/strong-alias | +92 321 0808002, 2.5 lakh, 5 years, 01/11/2026 | SHORTLISTED | SHORTLISTED | 100.0 SHORTLIST | SHORTLIST | yes |
| APP-2026-00128 Bilal Shah | PY_DEV/strong | 92-333-0808003, 220k, 4, 2026-11-16 | SHORTLISTED | SHORTLISTED | 100.0 SHORTLIST | REVIEW | yes |
| APP-2026-00129 Mahnoor Aslam | PY_DEV/strong-alias | 03450808004, 2.5 lakh, 5 years, 01/11/2026 | SHORTLISTED | SHORTLISTED | 100.0 SHORTLIST | SHORTLIST | yes |
| APP-2026-00130 Hassan Mirza | PY_DEV/review | (0312) 0808005, 160,000, 2, 2026-11-16 | SCREENING_REVIEW | SCREENING_REVIEW | 65.0 REVIEW | REVIEW | yes |
| APP-2026-00131 Iqra Nadeem | PY_DEV/review | 0300-0808006, 160,000, 2, 01/11/2026 | SCREENING_REVIEW | SCREENING_REVIEW | 65.0 REVIEW | REVIEW | yes |
| APP-2026-00132 Danish Kamal | PY_DEV/review | +92 321 0808007, 160,000, 2, 2026-11-16 | SCREENING_REVIEW | SCREENING_REVIEW | 65.0 REVIEW | REVIEW | yes |
| APP-2026-00133 Fiza Rehman | PY_DEV/weak | 92-333-0808008, 90k, 1, 01/11/2026 | REJECTED | REJECTED | 0.0 REJECT | REJECT | yes |
| APP-2026-00134 Talha Javed | PY_DEV/weak | 03450808009, 90k, 1, 2026-11-16 | REJECTED | REJECTED | 0.0 REJECT | REJECT | yes |
| APP-2026-00135 Noor Fatima | PY_DEV/weak | (0312) 0808010, 90k, 1, 01/11/2026 | REJECTED | REJECTED | 0.0 REJECT | REJECT | yes |
| APP-2026-00136 Saad Anwar | PY_DEV/incomplete | 03000808011, 2026-11-01 | SCREENING_REVIEW | SCREENING_REVIEW |   | - | yes |
| APP-2026-00137 Hina Butt | PY_DEV/incomplete | 03000808012, 2026-11-01 | SCREENING_REVIEW | SCREENING_REVIEW |   | - | yes |
| APP-2026-00138 Zeeshan Haider | BDE/strong | 92-333-0808013, 1.2 lakh, 3, 2026-11-16 | SHORTLISTED | SHORTLISTED | 100.0 SHORTLIST | SHORTLIST | yes |
| APP-2026-00139 Maryam Yousaf | BDE/strong | 03450808014, 1.2 lakh, 3, 01/11/2026 | SHORTLISTED | SHORTLISTED | 100.0 SHORTLIST | SHORTLIST | yes |
| APP-2026-00140 Faraz Akhtar | BDE/strong | (0312) 0808015, 1.2 lakh, 3, 2026-11-16 | SHORTLISTED | SHORTLISTED | 100.0 SHORTLIST | SHORTLIST | yes |
| APP-2026-00141 Kiran Abbasi | BDE/strong | 0300-0808016, 1.2 lakh, 3, 01/11/2026 | SHORTLISTED | SHORTLISTED | 100.0 SHORTLIST | SHORTLIST | yes |
| APP-2026-00142 Umer Farooqi | BDE/review | +92 321 0808017, 85000, 1, 2026-11-16 | SCREENING_REVIEW | SCREENING_REVIEW | 65.0 REVIEW | REVIEW | yes |
| APP-2026-00143 Laiba Sheikh | BDE/review | 92-333-0808018, 85000, 1, 01/11/2026 | SCREENING_REVIEW | SCREENING_REVIEW | 65.0 REVIEW | REVIEW | yes |
| APP-2026-00144 Junaid Malik | BDE/review | 03450808019, 85000, 1, 2026-11-16 | SCREENING_REVIEW | SCREENING_REVIEW | 65.0 REVIEW | REVIEW | yes |
| APP-2026-00145 Ayesha Riaz | BDE/weak | (0312) 0808020, 60k, 1, 01/11/2026 | REJECTED | REJECTED | 0.0 REJECT | REJECT | yes |
| APP-2026-00146 Hamid Chaudhry | BDE/weak | 0300-0808021, 60k, 1, 2026-11-16 | REJECTED | REJECTED | 0.0 REJECT | REJECT | yes |
| APP-2026-00147 Sana Qamar | BDE/weak | +92 321 0808022, 60k, 1, 01/11/2026 | REJECTED | REJECTED | 0.0 REJECT | REJECT | yes |
| APP-2026-00148 Waqas Gill | BDE/incomplete | 03000808023, 2026-11-01 | SCREENING_REVIEW | SCREENING_REVIEW |   | - | yes |
| APP-2026-00149 Rabia Hashmi | BDE/incomplete | 03000808024, 2026-11-01 | SCREENING_REVIEW | SCREENING_REVIEW |   | - | yes |
| APP-2026-00150 Kamran Baig | QA_ENG/strong | (0312) 0808025, 1.6 lakh, 3, 2026-11-16 | SHORTLISTED | SHORTLISTED | 100.0 SHORTLIST | SHORTLIST | yes |
| APP-2026-00151 Sobia Latif | QA_ENG/strong | 0300-0808026, 1.6 lakh, 3, 01/11/2026 | SHORTLISTED | SHORTLISTED | 100.0 SHORTLIST | SHORTLIST | yes |
| APP-2026-00152 Imran Sadiq | QA_ENG/strong | +92 321 0808027, 1.6 lakh, 3, 2026-11-16 | SHORTLISTED | SHORTLISTED | 100.0 SHORTLIST | SHORTLIST | yes |
| APP-2026-00153 Amna Zubair | QA_ENG/strong | 92-333-0808028, 1.6 lakh, 3, 01/11/2026 | SHORTLISTED | SHORTLISTED | 100.0 SHORTLIST | SHORTLIST | yes |
| APP-2026-00154 Asad Mehmood | QA_ENG/review | 03450808029, 120k, 2, 2026-11-16 | SCREENING_REVIEW | SCREENING_REVIEW | 70.0 REVIEW | REVIEW | yes |
| APP-2026-00155 Zoya Khalid | QA_ENG/review | (0312) 0808030, 120k, 2, 01/11/2026 | SCREENING_REVIEW | SCREENING_REVIEW | 70.0 REVIEW | REVIEW | yes |
| APP-2026-00156 Naveed Akram | QA_ENG/review | 0300-0808031, 120k, 2, 2026-11-16 | SCREENING_REVIEW | SCREENING_REVIEW | 70.0 REVIEW | REVIEW | yes |
| APP-2026-00157 Mehwish Tariq | QA_ENG/weak | +92 321 0808032, 70k, 1, 01/11/2026 | REJECTED | REJECTED | 0.0 REJECT | REJECT | yes |
| APP-2026-00158 Shoaib Ghani | QA_ENG/weak | 92-333-0808033, 70k, 1, 2026-11-16 | REJECTED | REJECTED | 0.0 REJECT | REJECT | yes |
| APP-2026-00159 Anam Saleem | QA_ENG/weak | 03450808034, 70k, 1, 01/11/2026 | REJECTED | REJECTED | 0.0 REJECT | REJECT | yes |
| APP-2026-00160 Rizwan Arif | QA_ENG/incomplete | 03000808035, 2026-11-01 | SCREENING_REVIEW | SCREENING_REVIEW |   | - | yes |
| APP-2026-00161 Hafsa Noman | QA_ENG/incomplete | 03000808036, 2026-11-01 | SCREENING_REVIEW | SCREENING_REVIEW |   | - | yes |
| - Adeel Kazmi | invalid/unknown-position | 03000808037, 300k, 3, 2026-11-01 | INVALID | INVALID |   | - | yes |
| - Sidra Waheed | invalid/unknown-position | 03000808038, 300k, 3, 2026-11-01 | INVALID | INVALID |   | - | yes |
| - Fahad Rana | invalid/unknown-position | 03000808039, 300k, 3, 2026-11-01 | INVALID | INVALID |   | - | yes |
| - Huma Pervaiz | invalid/no-contact | 12, 2, 2026-11-01 | INVALID | INVALID |   | - | yes |
| - Arslan Bhatti | invalid/no-contact | 12, 2, 2026-11-01 | INVALID | INVALID |   | - | yes |
| - Ifrah Sohail | invalid/no-contact | 12, 2, 2026-11-01 | INVALID | INVALID |   | - | yes |

## Evidence per scenario

### 1. Strong candidate becomes an employee - pass

- ✅ screened and shortlisted
- ✅ candidate sees 12 open slots through the signed link
- ✅ slot confirmed by the candidate
- ✅ interviewer submitted the scorecard (HIRE, score 84.00)
- ✅ evaluation selected the candidate and drafted the offer
- ✅ L1 approved by Ayesha via signed link
- ✅ offer approved and sent
- ✅ candidate sees the terms, not internal scores
- ✅ offer letter PDF downloadable (2808 bytes)
- ✅ candidate accepted
- ✅ employee NT-2026-007 created, account areeba.siddiqui3@novatech.example
- ✅ one welcome email with orientation details
- ✅ all 9 remaining onboarding tasks done -> ONBOARDED
- ✅ HR and manager told onboarding is complete
- status path: NEW -> VALIDATING -> VALIDATED -> SCORED -> SHORTLISTED -> INTERVIEW_SCHEDULED -> INTERVIEWED -> SELECTED -> OFFER_PENDING_APPROVAL -> OFFERED -> ACCEPTED -> ONBOARDING -> ONBOARDED

### 2. Incomplete application goes to manual review with a reason - pass

- ✅ validator outcome NEEDS_REVIEW (no CV, no experience, no salary)
- ✅ application is in SCREENING_REVIEW
- ✅ reason recorded: years of experience not provided; expected salary not provided; no CV was attached
- ✅ not scored automatically
- ✅ recruiters alerted once

### 3. Duplicate candidate submission is detected - pass

- ✅ second submission classified DUPLICATE
- ✅ still exactly one application for the candidate
- ✅ candidate told the application already exists
- original APP-2026-00164 status SHORTLISTED

### 4. Same webhook replayed creates no duplicates - pass

- ✅ replay answered 200 with the original correlation id COR-20261003-0186
- ✅ event receive_count = 2 (replay recorded, not processed)
- ✅ EVENT_REPLAY_DETECTED logged
- ✅ one acknowledgement email
- ✅ same key with a different body is refused (409 IDEMPOTENCY_KEY_REUSED)
- ✅ missing Idempotency-Key is refused (400)

### 5. Weak candidate is rejected by the rules - pass

- ✅ rule score 0.00 routes to REJECT
- ✅ application REJECTED (AI only advised)
- ✅ polite rejection notice sent once

### 6. A rule changed in the database is used without editing workflows - pass

- ✅ scoring version bumped automatically 14 -> 15
- ✅ before: score 80.00 (SHORTLIST, v14)
- ✅ after: same profile scores 72.73 (REVIEW, v15) with no workflow change
- ✅ outcome follows the new rule

### 7. Malformed AI output is handled (one retry, then fallback to review) - pass

- ✅ AI output rejected twice -> FALLBACK (MALFORMED_OUTPUT: technical_strength: Input should be a valid integer, unable to parse string as an integer; experience_relevance: Input should be less than or equal to 10; communication_indication: Field required; summary: String should have at least 10 characters; recommendation: Input should be 'SHORTLIST', 'REVIEW' or 'REJECT')
- ✅ rule score unaffected (SHORTLIST)
- ✅ routed to manual review instead of trusting bad output

### 8. A retryable failure succeeds after retries - pass

- ✅ two RETRY_ATTEMPT entries (HTTP 503, backoff 2 s then 4 s)
- ✅ RETRY_RECOVERED on attempt 3
- ✅ screening completed; nothing in the error queue

### 9. A non-retryable failure goes to the error queue - pass

- ✅ one NON_RETRYABLE error queued (HTTP_400)
- ✅ original action stored for replay
- ✅ no retries for a permanent error
- ✅ application waits in VALIDATED (nothing half-done)

### 10. Retries exhausted -> dead queue - pass

- ✅ dispatcher gave up after 5 attempts with backoff
- ✅ 10 backend retries logged in total
- ✅ ACTION_ATTEMPTS_EXHAUSTED in the error queue (replayable via WF-00)

### 11. A corrected item is replayed without duplicates - pass

- ✅ HTTP_400 replayed by HR: DONE: screening complete: SHORTLISTED
- ✅ s09: shortlisted with exactly one score row
- ✅ s09: still one acknowledgement email
- ✅ error RESOLVED, replay attributed to the staff member
- ✅ ACTION_ATTEMPTS_EXHAUSTED replayed by HR: scheduled action requeued; the dispatcher runs it within a minute
- ✅ s10: shortlisted with exactly one score row
- ✅ s10: still one acknowledgement email
- ✅ error RESOLVED, replay attributed to the staff member

### 12. Interview invitation reminder, then expiry - pass

- ✅ two days pass without a slot choice
- ✅ candidate reminded once
- ✅ invitation expired
- ✅ application back to SCREENING_REVIEW for the recruiter
- ✅ recruiters alerted

### 13. Interview cancellation releases the slot and re-invites - pass

- ✅ candidate sees 12 open slots through the signed link
- ✅ slot confirmed by the candidate
- ✅ candidate cancelled via the link
- ✅ slot released for other candidates
- ✅ feedback timers cancelled
- ✅ new invitation (round 2) sent

### 14. Missing interview feedback: reminder, then escalation - pass

- ✅ candidate sees 12 open slots through the signed link
- ✅ slot confirmed by the candidate
- ✅ interviewer reminded
- ✅ escalated to HR
- ✅ HR inbox has the escalation

### 15. Candidate fails the interview - pass

- ✅ candidate sees 12 open slots through the signed link
- ✅ slot confirmed by the candidate
- ✅ interviewer submitted the scorecard (NO_HIRE, score 36.00)
- ✅ final score 55.2 below the review threshold -> REJECTED
- ✅ rejection notice sent

### 16. Standard offer: one approval level - pass

- ✅ screened and shortlisted
- ✅ candidate sees 12 open slots through the signed link
- ✅ slot confirmed by the candidate
- ✅ interviewer submitted the scorecard (HIRE, score 84.00)
- ✅ evaluation selected the candidate and drafted the offer
- ✅ L1 approved by Ayesha via signed link
- ✅ offer approved and sent
- ✅ PKR 200,000 needs one level
- ✅ offer letter generated and emailed once

### 17. High salary: two approval levels by different people - pass

- ✅ screened and shortlisted
- ✅ candidate sees 12 open slots through the signed link
- ✅ slot confirmed by the candidate
- ✅ interviewer submitted the scorecard (HIRE, score 84.00)
- ✅ evaluation selected the candidate and drafted the offer
- ✅ PKR 320,000 > threshold -> two levels
- ✅ L1 approved by Ayesha via signed link
- ✅ after L1 the offer still waits
- ✅ the L1 approver cannot also approve L2 (NOT_AN_APPROVER)
- ✅ L2 approved by Omar via signed link
- ✅ sent after both levels

### 18. Approver rejects the offer - pass

- ✅ screened and shortlisted
- ✅ candidate sees 12 open slots through the signed link
- ✅ slot confirmed by the candidate
- ✅ interviewer submitted the scorecard (HIRE, score 84.00)
- ✅ evaluation selected the candidate and drafted the offer
- ✅ L1 rejected by Ayesha via signed link
- ✅ offer stopped with the approver's reason
- ✅ nothing sent to the candidate
- ✅ HR asked to revise (no automatic re-draft)
- ✅ HR drafted revision 2 for approval

### 19. Candidate declines the offer - pass

- ✅ screened and shortlisted
- ✅ candidate sees 12 open slots through the signed link
- ✅ slot confirmed by the candidate
- ✅ interviewer submitted the scorecard (HIRE, score 84.00)
- ✅ evaluation selected the candidate and drafted the offer
- ✅ L1 approved by Ayesha via signed link
- ✅ offer approved and sent
- ✅ application DECLINED
- ✅ offer reminders and expiry cancelled
- ✅ HR informed; candidate thanked

### 20. Candidate negotiates; HR sends a revised offer - pass

- ✅ screened and shortlisted
- ✅ candidate sees 12 open slots through the signed link
- ✅ slot confirmed by the candidate
- ✅ interviewer submitted the scorecard (HIRE, score 84.00)
- ✅ evaluation selected the candidate and drafted the offer
- ✅ L1 approved by Ayesha via signed link
- ✅ offer approved and sent
- ✅ application in NEGOTIATION
- ✅ HR notified with the message; candidate acknowledged
- ✅ HR drafted revision 2
- ✅ L1 approved by Ayesha via signed link
- ✅ revised offer approved and sent
- ✅ revision 1 superseded

### 21. Unanswered offer: reminder, then expiry - pass

- ✅ screened and shortlisted
- ✅ candidate sees 12 open slots through the signed link
- ✅ slot confirmed by the candidate
- ✅ interviewer submitted the scorecard (HIRE, score 84.00)
- ✅ evaluation selected the candidate and drafted the offer
- ✅ L1 approved by Ayesha via signed link
- ✅ offer approved and sent
- ✅ candidate reminded
- ✅ offer expired
- ✅ a late acceptance is refused (409 OFFER_NOT_OPEN)
- ✅ candidate told the offer expired

### 22. Onboarding runs exactly once, even if the step is delivered twice - pass

- ✅ screened and shortlisted
- ✅ candidate sees 12 open slots through the signed link
- ✅ slot confirmed by the candidate
- ✅ interviewer submitted the scorecard (HIRE, score 84.00)
- ✅ evaluation selected the candidate and drafted the offer
- ✅ L1 approved by Ayesha via signed link
- ✅ offer approved and sent
- ✅ offer accepted; onboarding started
- ✅ START_ONBOARDING delivered a second time (simulated at-least-once redelivery)
- ✅ still exactly one employee (NT-2026-008)
- ✅ still one welcome email
- ✅ the second welcome was suppressed by its dedupe key

### 23. Overdue onboarding task is visible and its owner reminded - pass

- ✅ NT-2026-008 task visible in v_overdue_onboarding_tasks
- ✅ task owner (HR) reminded by the sweep
- ✅ reminder is in the owner's inbox
- ✅ a second sweep does not remind again within the reminder interval
- ✅ daily metrics count 4 overdue task(s)
