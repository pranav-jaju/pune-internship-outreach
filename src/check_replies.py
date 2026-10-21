"""Find firms' replies (and bounces) in the IITB inbox and record them.

Adapted from the summer outreach (cold-mail-outreach src/send/check_replies.py). Differences: the
Pune sent log stamps times in IST (timestamp_ist), and a reply counts when it comes from the address
we mailed *or from anyone at the same company domain* (a careers@ inbox is usually answered by a
person). Writes data/replies.csv and data/bounces.csv; send.py and plan_followups.py skip both.
Read-only on the mailbox: messages are fetched with PEEK, so nothing is marked as read.

Usage: python src/check_replies.py [--log data/sent_log.csv] [--data data]
"""
import argparse
import csv
import email
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.utils import getaddresses, parsedate_to_datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from send import imap_connect  # noqa: E402

IST = timezone(timedelta(hours=5, minutes=30))
DECLINE = re.compile(r"\b(unfortunately|unable to (offer|take|accommodate)|not able to (offer|take)|"
                     r"no (open )?(positions?|openings?|vacanc(y|ies))|cannot (offer|take)|regret|"
                     r"not (currently )?(taking|accepting|hiring))\b", re.I)
AUTO = re.compile(r"auto(matic)?[- ]?(reply|response)|out of office|ticket|request .{0,40}(has been|is being) (received|created|reviewed)", re.I)
DAEMON = re.compile(r"mailer-daemon|postmaster|mail delivery", re.I)
FREE = {"gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "iitb.ac.in"}
REPLY_FIELDS = ["email", "firm", "from", "replied_at", "subject", "snippet", "outcome"]
BOUNCE_FIELDS = ["email", "firm", "bounced_at", "reason"]


def header(msg, name):
    try:
        return str(make_header(decode_header(msg.get(name, "") or "")))
    except Exception:
        return msg.get(name, "") or ""


def plain_text(msg):
    part = next((p for p in msg.walk() if p.get_content_type() == "text/plain"), None)
    html = part is None
    part = part or next((p for p in msg.walk() if p.get_content_type() == "text/html"), None)
    if part is None:
        return ""
    raw = part.get_payload(decode=True) or b""
    text = raw.decode(part.get_content_charset() or "utf-8", "replace")
    if html:
        text = re.sub(r"<[^>]+>", " ", re.sub(r"(?is)<(style|script).*?</\1>", " ", text))
    text = re.split(r"\n\s*(On .{0,200}wrote:|From:|-{3,}\s*Original Message)", text, maxsplit=1)[0]
    return re.sub(r"\s+", " ", text).strip()


def read_rows(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_rows(path, fields, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def domain(addr):
    return addr.rsplit("@", 1)[-1].lower()


def sent_times(log_path):
    """{email: (first send time UTC, firm)} for successful sends."""
    out = {}
    for r in read_rows(log_path):
        if r.get("status") != "sent":
            continue
        try:
            t = datetime.strptime(r["timestamp_ist"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST).astimezone(timezone.utc)
        except (KeyError, ValueError):
            continue
        em = r["email"].strip().lower()
        if em not in out or t < out[em][0]:
            out[em] = (t, r["firm"])
    return out


def classify(messages, sent):
    replies, bounces = {}, {}
    by_domain = {}
    for em, v in sent.items():
        if domain(em) not in FREE:
            by_domain.setdefault(domain(em), []).append(em)
    for msg in messages:
        try:
            when = parsedate_to_datetime(msg.get("Date")).astimezone(timezone.utc)
        except Exception:
            continue
        frm = header(msg, "From")
        addrs = [a.lower() for _, a in getaddresses([frm]) if a]
        if DAEMON.search(frm):
            body = msg.as_string()[:200_000].lower()
            for em, (t, firm) in sent.items():
                if em in body and when >= t and em not in bounces:
                    bounces[em] = {"email": em, "firm": firm, "bounced_at": when.astimezone(IST).strftime("%Y-%m-%d %H:%M"),
                                   "reason": header(msg, "Subject")[:200]}
            continue
        for a in addrs:
            targets = [a] if a in sent else by_domain.get(domain(a), [])
            for em in targets:
                t, firm = sent[em]
                if when < t:
                    continue
                text = plain_text(msg)
                subj = header(msg, "Subject")
                auto = AUTO.search(subj + " " + text[:300]) or re.search(r"no-?reply", a)
                outcome = "declined" if DECLINE.search(text) else "auto" if auto else "reply"
                row = {"email": em, "firm": firm, "from": a, "replied_at": when.astimezone(IST).strftime("%Y-%m-%d %H:%M"),
                       "subject": subj[:200], "snippet": text[:400], "outcome": outcome}
                old = replies.get(em)
                # a human reply beats an auto-acknowledgement; otherwise keep the first
                if old is None or (old["outcome"] == "auto" and outcome != "auto"):
                    replies[em] = row
    return replies, bounces


def fetch_inbox(imap, since):
    imap.select("INBOX", readonly=True)
    typ, data = imap.search(None, "SINCE", since.strftime("%d-%b-%Y"))
    if typ != "OK":
        return
    for num in (data[0] or b"").split():
        typ, parts = imap.fetch(num, "(BODY.PEEK[])")
        if typ == "OK" and parts and isinstance(parts[0], tuple):
            yield email.message_from_bytes(parts[0][1])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--log", default="data/sent_log.csv")
    ap.add_argument("--data", default="data")
    a = ap.parse_args()
    sent = sent_times(a.log)
    if not sent:
        print("nothing sent yet")
        return
    user = os.environ.get("SMTP_USER") or os.environ.get("SENDER_EMAIL", "")
    imap = imap_connect(os.environ.get("IMAP_SERVER", "imap.iitb.ac.in"), user, os.environ["SENDER_APP_TOKEN"])
    try:
        replies, bounces = classify(fetch_inbox(imap, min(t for t, _ in sent.values())), sent)
    finally:
        try:
            imap.logout()
        except Exception:
            pass
    rpath, bpath = os.path.join(a.data, "replies.csv"), os.path.join(a.data, "bounces.csv")
    old_r = {r["email"].strip().lower(): r for r in read_rows(rpath)}
    old_b = {r["email"].strip().lower(): r for r in read_rows(bpath)}
    merged_r = {**replies, **{k: v for k, v in old_r.items() if not (v["outcome"] == "auto" and k in replies)}}
    merged_b = {**bounces, **old_b}
    write_rows(rpath, REPLY_FIELDS, sorted(merged_r.values(), key=lambda r: r["replied_at"]))
    write_rows(bpath, BOUNCE_FIELDS, sorted(merged_b.values(), key=lambda r: r["bounced_at"]))
    kinds = {}
    for r in merged_r.values():
        kinds[r["outcome"]] = kinds.get(r["outcome"], 0) + 1
    print(f"replies: {len(merged_r)} {kinds}; bounces: {len(merged_b)}")
    for r in merged_r.values():
        print(f"  {r['outcome']:<8} {r['firm']:<28} {r['from']:<34} {r['replied_at']} | {r['subject'][:60]}")
    for r in merged_b.values():
        print(f"  bounce   {r['firm']:<28} {r['email']:<34} {r['bounced_at']} | {r['reason'][:60]}")


if __name__ == "__main__":
    main()
