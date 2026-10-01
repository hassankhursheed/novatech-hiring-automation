-- DEMO ONLY: compress business delays from days to minutes so reminders, expiries and escalations
-- can be shown live. Enabled with DEMO_FAST_TIMERS=true. Never enable in production.
UPDATE hiring.settings SET value = '"2 minutes"',  updated_by = 'demo_fast_timers' WHERE key = 'interview.invite_reminder_after';
UPDATE hiring.settings SET value = '"5 minutes"',  updated_by = 'demo_fast_timers' WHERE key = 'interview.invite_expires_after';
UPDATE hiring.settings SET value = '"2 minutes"',  updated_by = 'demo_fast_timers' WHERE key = 'interview.feedback_reminder_after';
UPDATE hiring.settings SET value = '"4 minutes"',  updated_by = 'demo_fast_timers' WHERE key = 'interview.feedback_escalate_after';
UPDATE hiring.settings SET value = '"2 minutes"',  updated_by = 'demo_fast_timers' WHERE key = 'offer.first_reminder_after';
UPDATE hiring.settings SET value = '"4 minutes"',  updated_by = 'demo_fast_timers' WHERE key = 'offer.final_reminder_after';
UPDATE hiring.settings SET value = '"6 minutes"',  updated_by = 'demo_fast_timers' WHERE key = 'offer.validity';
UPDATE hiring.settings SET value = '"5 minutes"',  updated_by = 'demo_fast_timers' WHERE key = 'onboarding.overdue_reminder_every';
