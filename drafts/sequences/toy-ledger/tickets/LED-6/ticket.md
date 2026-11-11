# Reversing an entry can change a closed period

We closed March: the books are locked through 31 March 2024. Posting a new
entry dated in March is refused with `PeriodLockedError`, as it should be.

But when a colleague reversed a March invoice (without giving a date, so the
reversal took the invoice's own date), it went through. March's sales figure
changed after we had already sent out the March report. Reversing with an
explicit March date goes through too.

Expected: a reversal that would land in a locked period is refused with the
same error as any other entry in a locked period, and nothing changes: the
original entry stays un-reversed, so it can still be reversed with a date in
an open period (e.g. 1 April). Reversals in open periods must keep working as
they do now.
