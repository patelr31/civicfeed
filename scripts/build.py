"""Combine the rule-based analysis (prepared.json) with the AI analysis (analysis.json)
into report.json for the web UI. All numbers are computed here, not by the AI:

  - every quote must appear word for word in the cited comment (else exit 1)
  - theme sizes, sides of each debate and stakeholder breakdowns are regex counts over
    distinct comments (each campaign counted once, via its representative)

Usage:  .venv/bin/python scripts/build.py CMS-2026-1255
"""
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from prep import full_text, key_points, redact  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def norm(s):
    s = s.replace(" ", " ").replace("&mdash;", "—").replace("&ndash;", "–")
    return re.sub(r"\s+", " ", s).strip()


def main(docket):
    d = ROOT / "data" / docket
    comments = json.loads((d / "comments.json").read_text())["comments"]
    prep = json.loads((d / "prepared.json").read_text())
    ai = json.loads((d / "analysis.json").read_text())
    by_id = {c["id"]: c for c in comments}
    who = prep["commenters"]
    camp = prep["campaigns"]
    member_of = {m: i for i, g in enumerate(camp) for m in g["members"]}
    distinct = [c for c in comments if c["id"] not in member_of or camp[member_of[c["id"]]]["representative"] == c["id"]]
    texts = {c["id"]: norm(full_text(c)).lower() for c in distinct}

    def weight(cid):  # a campaign representative stands for every copy
        return camp[member_of[cid]]["size"] if cid in member_of else 1

    def count(rx):
        hits = [cid for cid, t in texts.items() if re.search(rx, t, re.I)]
        return {
            "distinct": len(hits),
            "with_copies": sum(weight(h) for h in hits),
            "by_type": dict(Counter(who[h]["type"] for h in hits).most_common()),
            "ids": hits,
        }

    errors, themes, comment_themes = [], [], {}
    for th in ai["themes"]:
        quotes = []
        for q in th["quotes"]:
            c = by_id.get(q["id"])
            if not c or norm(q["text"]) not in norm(full_text(c)):
                errors.append(f"{th['id']}: quote not found verbatim in {q['id']}: {q['text'][:70]}")
                continue
            quotes.append({"id": q["id"], "text": norm(q["text"]), "org": c["organization"] or "Individual commenter",
                           "type": who[q["id"]]["type"], "url": c["url"]})
        m = count(th["measure"]["regex"])
        for cid in m["ids"]:
            comment_themes.setdefault(cid, []).append(th["id"])
        sides = []
        for s in th.get("sides", []):
            sc = count(s["regex"])
            sides.append({"label": s["label"], "distinct": sc["distinct"], "by_type": sc["by_type"]})
        themes.append({"id": th["id"], "title": th["title"], "summary": th["summary"], "debate": th.get("debate"),
                       "measure_label": th["measure"]["label"], "distinct": m["distinct"], "with_copies": m["with_copies"],
                       "by_type": m["by_type"], "sides": sides, "quotes": quotes})

    t = prep["totals"]
    form = [g for g in camp if g["kind"] == "form letter"]
    tmpl = [g for g in camp if g["kind"] == "template"]
    numbers = {
        "comments": t["comments"], "unique": len(distinct), "campaign_comments": t["in_campaigns"],
        "campaign_share": round(100 * t["in_campaigns"] / t["comments"]),
        "form_letter_size": max((g["size"] for g in form), default=0),
        "template_size": max((g["size"] for g in tmpl), default=0),
        "objects": sum(1 for c in distinct if who[c["id"]]["stance"] == "objects"),
    }
    summary = [p.format(**numbers) for p in ai["summary"]]

    index = []
    for c in comments:
        w = who[c["id"]]
        pts = key_points(c, limit=1)
        excerpt = redact(c, pts[0] if pts else norm(full_text(c))[:240])
        g = member_of.get(c["id"])
        index.append({"id": c["id"], "org": c["organization"], "type": w["type"], "stance": w["stance"],
                      "campaign": None if g is None else camp[g]["kind"], "campaign_id": g,
                      "themes": comment_themes.get(c["id"] if g is None else camp[g]["representative"], []),
                      "posted": c["posted"], "excerpt": excerpt[:300], "url": c["url"],
                      "attachments": len(c["attachments"])})

    report = {
        "docket": docket, "rule": ai["rule"], "what_cms_proposed": ai["what_cms_proposed"], "headline": ai["headline"],
        "summary": summary, "numbers": numbers, "notes": ai.get("notes", []),
        "by_type": t["by_type"], "stance": t["stance"],
        "campaigns": [{"kind": g["kind"], "size": g["size"], "stance": g["stance"], "opening": g["opening"],
                       "organizations": g.get("organizations", [])[:40]} for g in camp],
        "themes": themes, "comments": index,
    }
    if errors:
        print("\n".join(errors))
        sys.exit(1)
    (d / "report.json").write_text(json.dumps(report, ensure_ascii=False))
    print(f"report.json OK: {len(themes)} themes, {sum(len(x['quotes']) for x in themes)} quotes verified, numbers {numbers}")
    for x in themes:
        print(f"  {x['distinct']:3} distinct ({x['with_copies']:3} with copies)  {x['title']}" +
              "".join(f" | {s['label']}: {s['distinct']}" for s in x["sides"]))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "CMS-2026-1255")
