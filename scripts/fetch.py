"""Download every public comment on a regulations.gov docket from the Mirrulations
archive (a public mirror of regulations.gov on AWS S3, no API key needed), including the
text already extracted from PDF attachments, and write data/<docket>/comments.json.

Usage:  python3 scripts/fetch.py CMS-2026-1255
"""
import json
import re
import sys
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

BUCKET = "https://mirrulations.s3.amazonaws.com"
ROOT = Path(__file__).resolve().parent.parent


def get(url, tries=3):
    for i in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                return r.read()
        except Exception:
            if i == tries - 1:
                raise


def list_keys(prefix):
    keys, token = [], None
    while True:
        q = {"list-type": "2", "prefix": prefix, "max-keys": "1000"}
        if token:
            q["continuation-token"] = token
        xml = get(f"{BUCKET}/?{urllib.parse.urlencode(q)}").decode()
        keys += re.findall(r"<Key>([^<]+)</Key>", xml)
        m = re.search(r"<NextContinuationToken>([^<]+)</NextContinuationToken>", xml)
        if not m:
            return keys
        token = m.group(1)


def clean(t):
    t = re.sub(r"<br\s*/?>", "\n", t or "", flags=re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    t = t.replace("&nbsp;", " ").replace("&amp;", "&").replace("&quot;", '"').replace("&#39;", "'")
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n\n", t)).strip()


def main(docket):
    agency = docket.split("-")[0]
    out_dir = ROOT / "data" / docket
    out_dir.mkdir(parents=True, exist_ok=True)

    ckeys = [k for k in list_keys(f"raw-data/{agency}/{docket}/text-{docket}/comments/") if k.endswith(".json")]
    tkeys = [k for k in list_keys(f"derived-data/{agency}/{docket}/mirrulations/extracted_txt/comments_extracted_text/")
             if k.endswith(".txt")]
    # one extracted text per attachment; prefer pypdf output, keep the longest per attachment
    attach = {}
    with ThreadPoolExecutor(16) as pool:
        texts = list(pool.map(lambda k: (k, get(f"{BUCKET}/{k}").decode("utf-8", "replace")), tkeys))
    for k, t in texts:
        m = re.search(r"(" + re.escape(docket) + r"-\d+)_attachment_(\d+)_extracted", k)
        if m:
            key = (m.group(1), int(m.group(2)))
            if len(t) > len(attach.get(key, "")):
                attach[key] = t

    with ThreadPoolExecutor(16) as pool:
        raw = list(pool.map(lambda k: json.loads(get(f"{BUCKET}/{k}")), ckeys))

    comments = []
    for d in raw:
        a = d["data"]["attributes"]
        cid = d["data"]["id"]
        atts = [clean(t) for (c, n), t in sorted(attach.items()) if c == cid]
        comments.append({
            "id": cid,
            "posted": (a.get("postedDate") or "")[:10],
            "organization": (a.get("organization") or "").strip(),
            "submitter": " ".join(x for x in (a.get("firstName"), a.get("lastName")) if x).strip(),
            "category": a.get("category") or "",
            "state": a.get("stateProvinceRegion") or "",
            "title": a.get("title") or "",
            "comment": clean(a.get("comment")),
            "attachments": atts,
            "url": f"https://www.regulations.gov/comment/{cid}",
        })
    comments.sort(key=lambda c: c["id"])
    (out_dir / "comments.json").write_text(json.dumps({"docket": docket, "count": len(comments), "comments": comments}, indent=1, ensure_ascii=False))
    with_att = sum(bool(c["attachments"]) for c in comments)
    print(f"{docket}: {len(comments)} comments, {with_att} with attachment text, {len(attach)} attachments")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "CMS-2026-1255")
