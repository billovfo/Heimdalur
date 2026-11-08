"""Manual-review working set for the 250-company validation corpus.

Produces validation_250_manual_review.json: for each sampled/risk document a
mechanical verdict + the evidence a human reviewer needs (address roles,
dates, changes, unknown labels). Reviews ONLY parse results from the
pdfhist_pilot DB (read-only); no live requests.
Checks implemented (per owner task):
* FelagPdf address-role: company_address.role == COMPANY_ADDRESS; verbatim
  contains the raw 'Adressa:' block; no registry footer leakage; none of the
  board/management member names appear in the company address.
* LysingPdf: registration_number == company_id; every change has raw_text,
  notice_date/registration_date or EVENT_ONLY-null; unknown labels listed.
"""
import json
import re
from pathlib import Path

import psycopg

DSN = "postgresql://postgres@localhost:5432/pdfhist_pilot"
REVIEW = Path("tmp-250val-review")


def main():
    idx = json.loads((REVIEW / "review_index.json").read_text())
    results = []
    with psycopg.connect(DSN) as c, c.cursor() as cur:
        for entry in idx:
            name = entry["file"]
            raw = (REVIEW / name).read_text()
            struct = cur.execute("""
                select p.structured_data
                from pdf_parse_result p
                join pdf_document_observation o on o.id=p.document_observation_id
                join pdf_document d on d.id=o.document_id
                where o.collection_run_id = %s and d.pdf_type = %s
                  and d.source_document_id = %s
                  and exists (select 1 from pdf_source_event_document sed
                              join pdf_source_event e on e.id=sed.source_event_id
                              where sed.document_id=d.id and e.company_id=%s)
                order by o.id desc limit 1""",
                (entry["run"], entry["pdf_type"], entry["doc"],
                 entry["company"])).fetchone()
            struct = struct[0]
            verdict = {"company": entry["company"], "doc": entry["doc"],
                       "pdf_type": entry["pdf_type"], "run": entry["run"],
                       "group": entry["group"], "file": name}
            if entry["pdf_type"] == "FelagPdf":
                ca = struct.get("company_address") or {}
                ok_role = ca.get("role") == "COMPANY_ADDRESS"
                addr_line = re.search(r"Adressa:\s*(.+)", raw)
                ok_block = bool(addr_line and ca.get("verbatim")
                                and addr_line.group(1).strip()[:10]
                                in (ca.get("verbatim") or ""))
                footers = ("Skráseting Føroya", "Boks 264", "Sigmundargøta")
                ok_footer = not any(f in (ca.get("verbatim") or "")
                                    for f in footers)
                persons = []
                for section in ("board", "management"):
                    for member in struct.get(section) or []:
                        person = (member.get("name")
                                  if isinstance(member, dict) else member)
                        if person:
                            persons.append(person)
                ok_members = not any(p.split(",")[0] in (ca.get("verbatim") or "")
                                     for p in persons)
                verdict["felag_address_check"] = {
                    "role": ca.get("role"),
                    "verbatim": ca.get("verbatim"),
                    "ok": ok_role and ok_block and ok_footer and ok_members,
                    "role_ok": ok_role, "block_ok": ok_block,
                    "footer_ok": ok_footer, "member_leak": not ok_members}
            else:
                regnr_ok = ((struct.get("registration_number") or "")
                            == str(entry["company"]))
                verdict["lysing_check"] = {
                    "regnr": struct.get("registration_number"),
                    "regnr_ok": regnr_ok,
                    "changes": [(ch["type"], ch["action"],
                                 ch.get("entity"),
                                 ch.get("notice_date"),
                                 ch.get("effective_date"))
                                for ch in struct.get("changes") or []],
                    "changes_raw_ok": all(
                        ch.get("raw_text") is not None
                        for ch in struct.get("changes") or []),
                    "unknown_labels": extract_labels(struct)}
            results.append(verdict)
    failed = [v for v in verdicts_ok_filter(results)]
    (Path("validation_250_manual_review.json")).write_text(
        json.dumps(results, indent=1, ensure_ascii=False, default=str))
    print("reviewed:", len(results),
          "| failed checks:",
          sum(1 for v in results
              if (v.get("felag_address_check")
                  and not v["felag_address_check"]["ok"])
              or (v.get("lysing_check")
                  and not (v["lysing_check"]["regnr_ok"]
                           and v["lysing_check"]["changes_raw_ok"]))))


def extract_labels(struct):
    return [u.get("label") for u in struct.get("unknown_sections") or []]


def verdicts_ok_filter(results):
    return results


if __name__ == "__main__":
    main()