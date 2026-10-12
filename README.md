# Pune December internship outreach

This setup is fully separate from the summer research outreach (`cold-mail-outreach`). It has its own repo, queue, sent log and approval switch, and nothing in it touches that project.

## Flow

1. Write the drafts in `data/drafts.json`, one per firm, then run `python src/check_drafts.py`.
2. Pranav approves each draft by setting `approved: true`.
3. Build the queue: `python src/build_queue.py --start <YYYY-MM-DD> --per-day 4`. This writes `data/queue.csv`.
4. The GitHub Actions workflow `pune-send` runs on weekdays at 09:41 IST. It sends the rows that are due and commits `data/sent_log.csv`.
5. To send a test mail, go to Actions → pune-send → Run workflow and set `test_to` to your own address. This sends the first due mail with a [TEST] subject and does not log it.

## Repo settings the sender needs

Variables:
- `SENDER_EMAIL`
- `SMTP_SERVER` (`smtp-auth.iitb.ac.in` for IITB mail, `smtp.gmail.com` for Gmail)
- `SMTP_PORT=587`
- `PUNE_SENDING_APPROVED=yes`, set only after Pranav sees the test mail

Secrets:
- `SMTP_USER` (the bare LDAP id for IITB)
- `SENDER_APP_TOKEN` (an IITB CC access token, or a Gmail app password)

## Rules

- Never mention on-site in a mail.
- Never guess an email address.
- Each firm gets one cold mail.
- No real send happens until Pranav says go.
