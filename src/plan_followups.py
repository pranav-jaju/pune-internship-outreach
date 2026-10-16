"""Queue one short follow-up for each firm that hasn't answered a cold mail.

A firm qualifies when its first mail went out at least --after weekdays before --date, it is not in
data/replies.csv (a real reply or a decline; an auto-acknowledgement doesn't count) or
data/bounces.csv, and it has no follow-up queued or sent yet. Follow-ups are appended to
data/queue.csv as kind=followup rows due on --date; send.py sends them like any other row.
Nothing is queued unless data/followups_approved.txt says "yes" (Pranav approves the wording).

Usage: python src/plan_followups.py [--after 5] [--date YYYY-MM-DD] [--dry-run]
"""
import argparse
import csv
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from send import answered, load_sent  # noqa: E402

IST = timezone(timedelta(hours=5, minutes=30))
SIGNATURE = "Best,\nPranav Jaju\nIIT Bombay | +91 88069 22758"


def weekdays_between(a, b):
    """Weekdays after a, up to and including b."""
    n, d = 0, a
    while d < b:
        d += timedelta(days=1)
        n += d.weekday() < 5
    return n


def followup_body(first_body, firm, sent_on):
    greet = first_body.split("\n", 1)[0].strip()
    if not greet.lower().startswith("hi"):
        greet = f"Hi {firm} team,"
    quoted = "\n".join("> " + line if line else ">" for line in first_body.rstrip().split("\n"))
    return (f"{greet}\n\n"
            f"Just following up on my note from {sent_on:%-d %B} about spending my winter break (the last week of "
            f"November to the first week of January) learning at {firm}. I know inboxes get busy, so I'm sharing it "
            f"once more with my resume attached. I'd be glad to help with anything useful, even a small project.\n\n"
            f"{SIGNATURE}\n\n"
            f"On {sent_on:%-d %B %Y}, Pranav Jaju wrote:\n{quoted}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--after", type=int, default=5, help="weekdays to wait after the first mail")
    ap.add_argument("--date", default=datetime.now(IST).date().isoformat())
    ap.add_argument("--queue", default="data/queue.csv")
    ap.add_argument("--log", default="data/sent_log.csv")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    approved = Path("data/followups_approved.txt")
    if not a.dry_run and not (approved.exists() and approved.read_text().strip().lower() == "yes"):
        print("Follow-ups not approved (data/followups_approved.txt != yes); nothing queued.")
        return
    today = date.fromisoformat(a.date)
    rows = list(csv.DictReader(open(a.queue, newline="", encoding="utf-8")))
    fields = list(rows[0].keys())
    first_rows = {r["email"].strip().lower(): r for r in rows if (r.get("kind") or "first") == "first"}
    queued_fu = {r["email"].strip().lower() for r in rows if r.get("kind") == "followup"}
    _, _, followed = load_sent(a.log)
    done = answered(str(Path(a.log).parent))

    first_sent = {}
    for r in csv.DictReader(open(a.log, newline="", encoding="utf-8")):
        if r.get("status") == "sent" and (r.get("kind") or "first") == "first":
            first_sent.setdefault(r["email"].strip().lower(), date.fromisoformat(r["timestamp_ist"][:10]))

    new = []
    for em, sent_on in sorted(first_sent.items(), key=lambda kv: kv[1]):
        if em in done or em in followed or em in queued_fu or em not in first_rows:
            continue
        if weekdays_between(sent_on, today) < a.after:
            continue
        orig = first_rows[em]
        subject = re.sub(r"^(Re:\s*)+", "", orig["subject"])
        new.append({**{k: "" for k in fields}, "planned_date": today.isoformat(), "send": "yes", "kind": "followup",
                    "id": orig.get("id", "") + "-fu", "firm": orig["firm"], "name": orig.get("name", ""),
                    "email": orig["email"], "subject": "Re: " + subject,
                    "body": followup_body(orig["body"], orig["firm"], sent_on)})
    print(f"{len(new)} follow-ups due on {today}")
    for r in new:
        print(f"  {r['firm']:<30} {r['email']}")
    if a.dry_run or not new:
        return
    with open(a.queue, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows + new)


if __name__ == "__main__":
    main()
