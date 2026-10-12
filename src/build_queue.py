"""Turn approved drafts into data/queue.csv with a planned send date per row.

data/drafts.json is a list of {id, firm, name, email, subject, body, priority, approved}.
Only approved drafts are queued, highest priority (lowest number) first, --per-day per weekday
starting --start. Rows already in the sent log keep their place out of the queue (send.py skips
them anyway).

Usage: python src/build_queue.py --start 2026-10-13 --per-day 8
"""
import argparse
import csv
import json
from datetime import date, timedelta


def weekdays(start):
    d = start
    while True:
        if d.weekday() < 5:
            yield d
        d += timedelta(days=1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--drafts", default="data/drafts.json")
    ap.add_argument("--out", default="data/queue.csv")
    ap.add_argument("--start", required=True)
    ap.add_argument("--per-day", type=int, default=8)
    a = ap.parse_args()

    drafts = [d for d in json.load(open(a.drafts, encoding="utf-8")) if d.get("approved") and d.get("email")]
    drafts.sort(key=lambda d: (d.get("priority", 9), d["firm"]))
    days = weekdays(date.fromisoformat(a.start))
    rows, day, n = [], next(days), 0
    for d in drafts:
        if n == a.per_day:
            day, n = next(days), 0
        rows.append({"send": "yes", "kind": "first", "firm": d["firm"], "name": d.get("name", ""),
                     "email": d["email"], "subject": d["subject"], "body": d["body"],
                     "planned_date": day.isoformat(), "id": d["id"]})
        n += 1
    with open(a.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["planned_date", "send", "kind", "id", "firm", "name", "email",
                                          "subject", "body"])
        w.writeheader()
        w.writerows(rows)
    print(f"Queued {len(rows)} mails, {a.start} to {rows[-1]['planned_date'] if rows else '-'}")


if __name__ == "__main__":
    main()
