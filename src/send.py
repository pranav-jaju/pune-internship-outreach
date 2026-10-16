# -*- coding: utf-8 -*-
"""Sender for the Pune December-internship outreach. Separate from the summer research outreach
(cold-mail-outreach): own repo, own queue, own sent log, own approval switch.

Reads data/queue.csv (columns: send, firm, name, email, subject, body, planned_date) and sends
the rows whose planned_date is on or before --date (IST today by default), once each. Anyone in
data/sent_log.csv with status "sent" (matched by email, or by firm for first-touch mails) is
skipped, so re-running never double-sends and no firm gets two cold mails.

It is a dry run unless --send is passed, and a real send also needs PUNE_SENDING_APPROVED=yes.

Usage:
    python src/send.py                                   # dry run of what is due today
    python src/send.py --test-to me@example.com --limit 1 --send
    python src/send.py --send                            # real send (CI only)
"""
import argparse
import csv
import imaplib
import os
import random
import smtplib
import ssl
import sys
import time
from datetime import datetime, timedelta, timezone
from email.charset import QP, Charset
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate, make_msgid
from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30))
UTF8_QP = Charset("utf-8")
UTF8_QP.body_encoding = QP
LOG_FIELDS = ["timestamp_ist", "firm", "name", "email", "subject", "kind", "status", "error"]


def env(name):
    v = os.environ.get(name)
    if not v:
        sys.exit(f"Missing environment variable {name}")
    return v


def load_sent(path):
    """(emails, firms, followed_up): first-touch emails and firms already sent, and emails that
    already got their one follow-up."""
    emails, firms, followed = set(), set(), set()
    if Path(path).exists():
        for r in csv.DictReader(open(path, newline="", encoding="utf-8")):
            if r.get("status") == "sent":
                if r.get("kind", "first") == "followup":
                    followed.add(r["email"].strip().lower())
                else:
                    emails.add(r["email"].strip().lower())
                    firms.add(r["firm"].strip().lower())
    return emails, firms, followed


def answered(data_dir="data"):
    """Emails that replied (any outcome) or bounced: never followed up."""
    out = set()
    for name in ("replies.csv", "bounces.csv"):
        p = Path(data_dir) / name
        if p.exists():
            out |= {r["email"].strip().lower() for r in csv.DictReader(open(p, newline="", encoding="utf-8"))
                    if not (name == "replies.csv" and r.get("outcome") == "auto")}
    return out


def build_message(row, sender_name, sender_email, resume):
    msg, alt = MIMEMultipart("mixed"), MIMEMultipart("alternative")
    msg["From"] = formataddr((sender_name, sender_email))
    msg["To"] = row["email"]
    msg["Subject"] = row["subject"]
    msg["Reply-To"] = sender_email
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid()
    body = row["body"].rstrip() + "\n"
    alt.attach(MIMEText(body, "plain", UTF8_QP))
    import html
    paras = "".join(f"<p>{html.escape(p).replace(chr(10), '<br>')}</p>" for p in body.split("\n\n") if p.strip())
    alt.attach(MIMEText(f'<div style="font-family:sans-serif;font-size:14px;line-height:1.5">{paras}</div>',
                        "html", UTF8_QP))
    msg.attach(alt)
    part = MIMEApplication(resume.read_bytes(), _subtype="pdf")
    part.add_header("Content-Disposition", "attachment", filename=resume.name)
    msg.attach(part)
    return msg


# Copied from the summer outreach sender (cold-mail-outreach src/send/send_batch.py), where it
# already files every sent mail in the IITB Sent folder.
def imap_connect(server, user, token):
    """IMAP login to IITB. Its server rejects OpenSSL 3's default ciphers on 993, so try SSL on 993,
    then STARTTLS on 143, then each again with the legacy cipher level; the first that logs in wins."""
    def ctx(legacy):
        c = ssl.create_default_context()
        if legacy:
            c.set_ciphers("DEFAULT:@SECLEVEL=0")
            c.minimum_version = ssl.TLSVersion.TLSv1
        return c
    errors = []
    for legacy in (False, True):
        for port in (993, 143):
            try:
                if port == 993:
                    m = imaplib.IMAP4_SSL(server, port, ssl_context=ctx(legacy), timeout=30)
                else:
                    m = imaplib.IMAP4(server, port, timeout=30)
                    m.starttls(ssl_context=ctx(legacy))
                m.login(user, token)
                return m
            except Exception as e:
                errors.append(f"{port}{'L' if legacy else ''}: {type(e).__name__}: {str(e)[:80]}")
    raise ConnectionError("; ".join(errors))


class SentFolder:
    """Saves a copy of each sent email to the mailbox's Sent folder over IMAP (SMTP alone keeps
    no copy). Best effort: a failure is printed once and never blocks or fails a send."""

    def __init__(self, user, token):
        self.user, self.token, self.imap, self.dead = user, token, None, False
        self.server = os.environ.get("IMAP_SERVER", "imap.iitb.ac.in")
        self.folder = os.environ.get("IMAP_SENT_FOLDER", "Sent")

    def save(self, msg):
        if self.dead:
            return
        try:
            if self.imap is None:
                self.imap = self.connect()
            typ, data = self.imap.append(self.folder, r"(\Seen)", imaplib.Time2Internaldate(time.time()),
                                         msg.as_bytes())
            if typ != "OK":
                raise imaplib.IMAP4.error(f"{typ} {data}")
        except Exception as e:
            self.dead = True
            print(f"  (could not save a copy to {self.folder}: {type(e).__name__}: {e}; sending continues)")

    def connect(self):
        m = imap_connect(self.server, self.user, self.token)
        print(f"  (Sent-folder copies via {self.server})")
        return m

    def close(self):
        if self.imap is not None:
            try:
                self.imap.logout()
            except Exception:
                pass


def log(path, row, status, error=""):
    new = not Path(path).exists()
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=LOG_FIELDS, extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerow({**row, "timestamp_ist": datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S"),
                    "kind": row.get("kind") or "first", "status": status, "error": error})


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--queue", default="data/queue.csv")
    ap.add_argument("--log", default="data/sent_log.csv")
    ap.add_argument("--date", default=datetime.now(IST).date().isoformat())
    ap.add_argument("--limit", type=int)
    ap.add_argument("--delay", type=int, default=90, help="base seconds between sends")
    ap.add_argument("--jitter", type=int, default=120, help="extra random 0..N seconds per send")
    ap.add_argument("--test-to", default="")
    ap.add_argument("--send", action="store_true")
    a = ap.parse_args()

    sent_emails, sent_firms, followed = load_sent(a.log)
    done = answered(str(Path(a.log).parent))
    queue, seen = [], set()
    for row in csv.DictReader(open(a.queue, newline="", encoding="utf-8")):
        if row.get("send", "").strip().lower() != "yes" or not row.get("email") or not row.get("body"):
            continue
        email, firm = row["email"].strip().lower(), row["firm"].strip().lower()
        if (row.get("kind") or "first") == "followup":
            # one follow-up, only to a first mail that went out and got no answer or bounce
            if email in followed or email in done or email not in sent_emails or ("fu", email) in seen:
                continue
            if row.get("planned_date") and row["planned_date"] > a.date:
                continue
            seen.add(("fu", email))
            queue.append(row)
            continue
        if email in sent_emails or email in seen or firm in sent_firms or firm in seen:
            continue
        if row.get("planned_date") and row["planned_date"] > a.date:
            continue
        seen |= {email, firm}
        queue.append(row)
    queue = queue[: a.limit] if a.limit else queue

    print(f"Due on or before {a.date}: {len(queue)}")
    if not a.send:
        for r in queue:
            print(f"  [dry run] {r['firm']:<34} {r['email']:<36} | {r['subject']}")
        return
    if not queue:
        return
    if os.environ.get("PUNE_SENDING_APPROVED") != "yes" and not a.test_to:
        sys.exit("Sending not approved (PUNE_SENDING_APPROVED != yes). Use --test-to for a test send.")

    resume = Path(os.environ.get("RESUME_PATH", "resume/PranavJaju_Resume.pdf"))
    if not resume.exists():
        sys.exit(f"Resume missing at {resume}; not sending without it")
    sender_name, sender_email = env("SENDER_NAME"), env("SENDER_EMAIL")
    smtp = smtplib.SMTP(env("SMTP_SERVER"), int(os.environ.get("SMTP_PORT", "587")), timeout=30)
    smtp.ehlo()
    smtp.starttls(context=ssl.create_default_context())
    smtp.ehlo()
    smtp.login(os.environ.get("SMTP_USER") or sender_email, env("SENDER_APP_TOKEN"))

    sent_folder = SentFolder(os.environ.get("SMTP_USER") or sender_email, env("SENDER_APP_TOKEN"))
    ok = fail = 0
    for i, row in enumerate(queue, 1):
        if a.test_to:
            row = {**row, "email": a.test_to, "subject": "[TEST] " + row["subject"]}
        print(f"[{i}/{len(queue)}] {row['firm']} -> {row['email']}")
        try:
            msg = build_message(row, sender_name, sender_email, resume)
            smtp.send_message(msg)
            ok += 1
            sent_folder.save(msg)
            if not a.test_to:
                log(a.log, row, "sent")
        except Exception as e:  # keep going; the log records the failure
            fail += 1
            print(f"  failed: {type(e).__name__}: {e}")
            if not a.test_to:
                log(a.log, row, "failed", str(e))
        if i < len(queue):
            time.sleep(a.delay + random.randint(0, a.jitter))
    smtp.quit()
    sent_folder.close()
    print(f"Done. sent={ok} failed={fail}")


if __name__ == "__main__":
    main()
