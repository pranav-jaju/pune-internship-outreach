"""Sanity checks on data/drafts.json before anything is queued.

Fails a draft that mentions on-site / office location, uses AI-sounding phrases, runs long,
has no firm-specific line, or has an email that wasn't marked as published (source_url).
Usage: python src/check_drafts.py
"""
import json
import re
import sys

BANNED = [r"on[- ]?site", r"in[- ]person", r"relocat", r"hope this (e-?mail|mail) finds", r"\bdelve",
          r"\bleverage", r"passionate", r"cutting[- ]edge", r"i am writing to express", "—",
          r"groundbreaking", r"esteemed", r"expertise", r"\bremote\b"]


def check(d):
    errs = []
    body = d.get("body", "")
    for pat in BANNED:
        if re.search(pat, body, re.I) or re.search(pat, d.get("subject", ""), re.I):
            errs.append(f"banned: {pat}")
    words = len(re.findall(r"\b\w+\b", body.split("\nBest,")[0]))
    if not 90 <= words <= 190:
        errs.append(f"length {words} words (want 90-190)")
    if d["firm"].split()[0].lower() not in body.lower():
        errs.append("firm name not in body")
    if not d.get("why_line") or d["why_line"] not in body:
        errs.append("no firm-specific why line")
    if d.get("email") and not d.get("email_source"):
        errs.append("email has no source")
    return errs


def main():
    drafts = json.load(open("data/drafts.json", encoding="utf-8"))
    bad = 0
    for d in drafts:
        e = check(d)
        if e:
            bad += 1
            print(f"{d['id']}: " + "; ".join(e))
    print(f"{len(drafts)} drafts, {bad} with problems")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
