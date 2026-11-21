"""Rule-based analysis of a docket's comments (no AI). Reads data/<docket>/comments.json,
writes data/<docket>/prepared.json with:

  - campaigns:   form-letter groups (comments sharing most of their wording)
  - commenters:  stakeholder type for every comment (from category, organization, text)
  - stance:      support / oppose / mixed / unclear, from explicit wording
  - points:      each unique comment's key recommendation sentences
  - themes:      TF-IDF + k-means clusters of those sentences, with top terms and
                 representative sentences (the input for the AI step, see ANALYZE.md)

Usage:  .venv/bin/python scripts/prep.py CMS-2026-1255
"""
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from sklearn.cluster import KMeans
from sklearn.feature_extraction.text import TfidfVectorizer

ROOT = Path(__file__).resolve().parent.parent
N_THEMES = 14
SHINGLE = 5            # words per shingle for form-letter matching
SAME_LETTER = 0.5      # Jaccard similarity of shingles to count as the same letter


def full_text(c):
    return "\n\n".join([c["comment"]] + c["attachments"]).strip()


def words(t):
    return re.findall(r"[a-z0-9']+", t.lower())


def shingles(t):
    w = words(t)[:400]  # the opening 400 words decide whether it's a form letter
    return {" ".join(w[i:i + SHINGLE]) for i in range(max(1, len(w) - SHINGLE + 1))}


# ---------- campaigns (form letters) ----------
def templates(comments, skip):
    """Organizations that filed the same template with their own name swapped in.
    Pairs whose 6-word shingles overlap >= 35% (after the opening form-letter check)."""
    def sh6(t):
        w = words(t)[:1500]
        return {" ".join(w[i:i + 6]) for i in range(max(1, len(w) - 5))}
    cand = [c for c in comments if c["id"] not in skip and len(words(full_text(c))) >= 150]
    S = {c["id"]: sh6(full_text(c)) for c in cand}
    ids = [c["id"] for c in cand]
    parent = {i: i for i in ids}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for a in range(len(ids)):
        A = S[ids[a]]
        for b in range(a + 1, len(ids)):
            B = S[ids[b]]
            small = min(len(A), len(B))
            if small and len(A & B) / small >= 0.35:
                parent[find(ids[a])] = find(ids[b])
    groups = defaultdict(list)
    for i in ids:
        groups[find(i)].append(i)
    out = []
    for members in groups.values():
        if len(members) >= 5:
            members.sort()
            out.append({"members": members, "size": len(members), "representative": members[0], "kind": "template"})
    return out


def campaigns(comments):
    sh = {c["id"]: shingles(c["comment"] or full_text(c)) for c in comments}
    ids = [c["id"] for c in comments if len(words(c["comment"] or full_text(c))) >= 40]
    parent = {i: i for i in ids}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    # bucket by a few shingles to avoid comparing every pair blindly
    buckets = defaultdict(list)
    for i in ids:
        for s in sorted(sh[i])[:8]:
            buckets[s].append(i)
    for group in buckets.values():
        for a in range(len(group)):
            for b in range(a + 1, len(group)):
                x, y = group[a], group[b]
                if find(x) == find(y):
                    continue
                inter = len(sh[x] & sh[y])
                if inter and inter / len(sh[x] | sh[y]) >= SAME_LETTER:
                    parent[find(x)] = find(y)
    groups = defaultdict(list)
    for i in ids:
        groups[find(i)].append(i)
    out = []
    for members in groups.values():
        if len(members) < 5:
            continue
        members.sort()
        out.append({"members": members, "size": len(members), "representative": members[0], "kind": "form letter"})
    out.sort(key=lambda g: -g["size"])
    return out


# ---------- stakeholders ----------
CATEGORY = [  # regulations.gov submitter category -> type (checked first)
    (r"physician|nurse|professional|pharmacist|clinician|dental", "Physician / clinician group"),
    (r"hospital", "Hospital / health system"),
    (r"health plan|insur", "Health plan / PBM"),
    (r"pharmaceutical|drug|device|laboratory industry|biotech", "Drug, device or lab company"),
    (r"consumer|patient", "Patient / consumer advocate"),
    (r"association - other|association$", None),
    (r"government|state|local|tribal|federal", "Government"),
]
ORG = [  # organization name -> type, checked in order
    ("Health IT / standards", r"\bhl7\b|health level seven|da vinci|himss|healthcare information and management|ahima|american health information management|wedi|workgroup for electronic data|ncpdp|carin|end-users alliance|health data consortium"),
    ("Drug, device or lab company", r"pharma|biotech|therapeutic|advamed|phrma|sanofi|pfizer|novartis|merck|amgen|genentech|lilly|bristol|abbvie|astrazeneca|johnson & johnson|labcorp|quest diagnostics|laborator|genetic|biodesix|aegis sciences|device|vgm|lincare"),
    ("Health plan / PBM", r"health plan|insur|\bpbm\b|pharmacy benefit|ahip|blue cross|blue shield|bcbs|unitedhealth|humana|aetna|cigna|centene|elevance|molina|anthem|kaiser|managed care"),
    ("Community health / provider association", r"community health|health cent(er|re)|primary care|health care association|healthcare association|hospital association|\bamga\b|neighborhood health|health network|healthcare network|rural health"),
    ("Hospital / health system", r"hospital|health system|medical center|healthcare system|\bhealth$|healthcare$|clinic\b|summit health|piedmont"),
    ("Physician / clinician group", r"physician|medical association|college of|academy of|society of|osteopath|nurs|pharmacist|surgeon|oncolog|neurolog|rheumatolog|dermatolog|psychiatr|orthopaed|medicine association|specialists"),
    ("Patient / consumer advocate", r"patient|consumer|advoca|foundation|alliance|coalition|law program|disease|cancer|arthritis|diabetes|lupus|alzheimer|syndrome|\bals\b|aids|hiv|families|homeless|civil rights|sharsheret|center on aging"),
    ("Health IT vendor", r"software|technolog|health it|\behr\b|informatics|\bdata\b|digital|\bai\b|surescripts|covermymeds|availity|epic|oracle|cerner|athena|change healthcare|optum|mcg health|agadia|easypa|calvient|coligomed|helsesoft|intersystems|firely|stedi|1uphealth|pointclickcare|veradigm|auth57|rxutility|evresa|avectic|artificer|defacto|zelo|vizient|premier inc|mckesson|\binc\.?$|\bllc\.?$|corp(oration)?$"),
    ("Government", r"state of|department of|county|government|\bagency\b|governor|commonwealth|public health"),
]


def stakeholder(c):
    org, cat = c["organization"], c["category"].lower()
    if not org:
        return "Individual"
    for rx, t in CATEGORY:
        if t and re.search(rx, cat):
            return t
    for t, rx in ORG:
        if re.search(rx, org.lower()):
            return t
    return "Other organization"


# ---------- stance ----------
SUPPORT = r"\b(strongly support|support(s|ing)? (the|this|cms'?s?|these)|applaud|commend|urge cms to finalize|in favor of|welcome[sd]? (the|this)|appreciate cms'?s? (proposal|efforts))"
OPPOSE = r"\b(oppose|opposes|opposition to|urge cms (not|to withdraw|to delay|to reconsider)|withdraw (the|this)|should not (finalize|adopt)|strongly object|concerned that the proposed|reject)"


def stance(text):
    t = text[:6000].lower()
    s, o = len(re.findall(SUPPORT, t)), len(re.findall(OPPOSE, t))
    if s and o:
        return "mixed"
    if s:
        return "support"
    if o:
        return "objects"
    return "unclear"


# ---------- key points ----------
KEY = re.compile(r"\b(recommend|urge|encourage|request|ask(s)? (that )?cms|should|must|support|oppose|concern|believe|propose that|clarify|delay|exempt|extend|require)\b", re.I)


def sentences(text):
    text = re.sub(r"\s+", " ", text)
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[A-Z\"(])", text) if 60 <= len(s.strip()) <= 420]


def key_points(c, limit=12):
    pts = [s for s in sentences(full_text(c)) if KEY.search(s) and not re.search(r"\b(sincerely|thank you for the opportunity|please contact|on behalf of)\b", s, re.I)]
    seen, out = set(), []
    for s in pts:
        k = s[:80].lower()
        if k not in seen:
            seen.add(k)
            out.append(s)
    return out[:limit]


def redact(c, text):
    """Remove an individual submitter's own name from text we display."""
    for part in (c.get("submitter") or "").split():
        if len(part) > 2:
            text = re.sub(rf"\b{re.escape(part)}\b", "[name]", text)
    return re.sub(r"\b([Mm]y name is|I am|I'm) ([A-Z][a-z]+(?: [A-Z]\.)?(?: [A-Z][a-z'-]+){1,2})", r"\1 [name]", text)


def main(docket):
    d = json.loads((ROOT / "data" / docket / "comments.json").read_text())
    comments = d["comments"]
    camp = campaigns(comments)
    camp += templates(comments, {m for g in camp for m in g["members"]})
    in_campaign = {m: i for i, g in enumerate(camp) for m in g["members"]}

    by_id = {c["id"]: c for c in comments}
    people = {}
    for c in comments:
        people[c["id"]] = {"type": stakeholder(c), "stance": stance(full_text(c)),
                           "campaign": in_campaign.get(c["id"]), "chars": len(full_text(c))}

    # points from every unique comment (one representative per campaign)
    unique = [c for c in comments if c["id"] not in in_campaign or camp[in_campaign[c["id"]]]["representative"] == c["id"]]
    points = [{"id": c["id"], "text": s} for c in unique for s in key_points(c)]

    vec = TfidfVectorizer(stop_words="english", ngram_range=(1, 2), min_df=3, max_df=0.4, sublinear_tf=True)
    X = vec.fit_transform([p["text"] for p in points])
    km = KMeans(n_clusters=N_THEMES, n_init=10, random_state=7).fit(X)
    terms = np.array(vec.get_feature_names_out())
    themes = []
    for k in range(N_THEMES):
        idx = np.where(km.labels_ == k)[0]
        center = km.cluster_centers_[k]
        top = terms[center.argsort()[::-1][:10]].tolist()
        # representatives: closest sentences from different comments
        dist = np.asarray(X[idx] @ center).ravel()
        reps, used = [], set()
        for j in idx[dist.argsort()[::-1]]:
            p = points[j]
            if p["id"] not in used:
                used.add(p["id"])
                reps.append(p)
            if len(reps) == 10:
                break
        cids = sorted({points[j]["id"] for j in idx})
        types = Counter(people[i]["type"] for i in cids)
        weight = sum(camp[people[i]["campaign"]]["size"] if people[i]["campaign"] is not None else 1 for i in cids)
        themes.append({"k": k, "points": len(idx), "comments": cids, "comment_count": len(cids),
                       "weighted_count": weight, "top_terms": top, "representatives": reps,
                       "by_type": dict(types.most_common()),
                       "stance": dict(Counter(people[i]["stance"] for i in cids))})
    themes.sort(key=lambda t: -t["comment_count"])

    for g in camp:
        rep = by_id[g["representative"]]
        if g["kind"] == "template":  # show the shared wording, not one member's letterhead
            others = [set(key_points(by_id[m], limit=40)) for m in g["members"][1:]]
            shared = [x for x in key_points(rep, limit=40) if sum(x in o for o in others) >= len(others) // 2]
            g["opening"] = redact(rep, " ".join(shared[:3]))[:600]
        else:
            body = rep["comment"] if len(rep["comment"]) >= 200 else full_text(rep)
            g["opening"] = redact(rep, re.sub(r"^See attached file\(s\)\s*", "", body))[:600]
        g["organizations"] = sorted({by_id[m]["organization"] for m in g["members"] if by_id[m]["organization"]})
        g["stance"] = Counter(people[m]["stance"] for m in g["members"]).most_common(1)[0][0]

    out = {
        "docket": docket,
        "totals": {
            "comments": len(comments),
            "unique": len(unique),
            "in_campaigns": len(in_campaign),
            "organizations": sum(bool(c["organization"]) for c in comments),
            "with_attachments": sum(bool(c["attachments"]) for c in comments),
            "by_type": dict(Counter(p["type"] for p in people.values()).most_common()),
            "stance": dict(Counter(p["stance"] for p in people.values()).most_common()),
            "stance_unique": dict(Counter(people[c["id"]]["stance"] for c in unique).most_common()),
        },
        "campaigns": camp,
        "commenters": people,
        "themes": themes,
    }
    (ROOT / "data" / docket / "prepared.json").write_text(json.dumps(out, indent=1, ensure_ascii=False))
    t = out["totals"]
    print(f"{docket}: {t['comments']} comments, {t['unique']} unique, {len(camp)} campaigns covering {t['in_campaigns']}")
    print("types:", t["by_type"]); print("stance:", t["stance"], "| unique:", t["stance_unique"])
    for g in camp:
        print(f"  {g['kind']} {g['size']} ({g['stance']}): {g['opening'][:100]!r}")
    for th in themes:
        print(f"  theme {th['k']:2} {th['comment_count']:3} comments: {', '.join(th['top_terms'][:7])}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "CMS-2026-1255")
