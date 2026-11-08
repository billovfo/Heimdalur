"""Render the 10-random-company data audit markdown from the lossless JSON.

Input:  docs/HEIMDAL-PDF-HISTORIK-10-RANDOM-DATA-AUDIT-v1.json
Output: docs/HEIMDAL-PDF-HISTORIK-10-RANDOM-DATA-AUDIT-v1.md
"""
from __future__ import annotations

import html
import json
from pathlib import Path

JSON_IN = Path("docs/HEIMDAL-PDF-HISTORIK-10-RANDOM-DATA-AUDIT-v1.json")
MD_OUT = Path("docs/HEIMDAL-PDF-HISTORIK-10-RANDOM-DATA-AUDIT-v1.md")

SCALAR_FAMILIES = ["NAME", "ADDRESS", "CAPITAL", "PURPOSE", "SIGNING_RULES",
                   "COMPANY_FORM"]
MEMBERSHIP_FAMILIES = ["BOARD", "MANAGEMENT", "AUDITOR"]


def pretty(obj):
    return json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True)


def fold(title, text, lang=None):
    """Fenced verbatim block. Content is rendered AS STORED (no HTML
    transformation) so the reader sees exact evidence; only stray ``` at
    line starts would terminate the fence, which registry text never has."""
    body = (text or "").replace("```", "``\u200b``").lstrip("\n")
    return (f"<details><summary>{html.escape(title)}</summary>\n\n"
            f"```{'json' if lang == 'json' else 'text'}\n{body}\n```\n\n"
            "</details>\n")


def table(header, rows, aligns=None):
    if aligns is None:
        aligns = ["---"] * len(header)
    out = ["| " + " | ".join(header) + " |",
           "| " + " | ".join(aligns) + " |"]
    for row in rows:
        cells = [str(c0).replace("|", "\\|").replace("\n", " ") if c0 is not None
                 else "" for c0 in row]
        out.append("| " + " | ".join(cells) + " |")
    return "\n".join(out) + "\n"


def render(data):
    md = []
    add = md.append
    sel = data["selection"]
    add("# HEIMDAL PDF-HISTORIK — 10-RANDOM-COMPANY DATA AUDIT v1\n")
    add("**Dato:** 2026-09-01  ")
    add("**Seed:** `heimdal-250-audit-2026-08-31`  ")
    add("**Kilde-DB:** `pdfhist_pilot` (read-only; 0 live requests, 0 "
        "parserpatches, 0 reparse, 0 DB-mutationer)\n")
    add("Denne audit viser ALT gemt og afledt PDF-historikdata for 10 "
        "tilfældigt valgte virksomheder fra den 250-company validation "
        "cohort. Intet data er filtreret, trunkeret eller cherry-picket.\n")

    # - population + selection BEFORE any data --------------------------------
    add("## Population og blind udvælgelse\n")
    add("Populationen er reproduceret mekanisk fra de to valideringsartefakter:\n")
    add("* Cohort 100: `validation_selection.json` (runs 424241/424242/424243) — 100 virksomheder")
    add("* Cohort 150: `validation_selection_expansion.json` (run 424244) — 150 virksomheder")
    add("* Union: 250 unikke registration numbers, 0 overlap, 0 duplikater\n")
    add(table(["Mekanisme", "Værdi"],
              [["seed", "`heimdal-250-audit-2026-08-31`"],
               ["score", "`sha256(seed + '|' + registration_number)`"],
               ["sortering", "ascending efter score"],
               ["sample", "de 10 laveste scores"],
               ["alle 250 gemt med regnr/score/rank",
                "`audit_selection_250.json`"],
               ["udvælgelsen beregnet FØR", "nogen virksomhedsdata blev læst"]]))
    add("### Valgte virksomheder (blinde udvælgelse — vises FØR indhold)\n")
    add(table(["Rank", "Regnr", "Company ID", "Selection hash (prefix)"],
              [[c["selection_rank"], c["registration_number"],
                c["company_id"] if c["company_id"] else "(none)",
                c["selection_hash"][:16] + "…"]
               for c in data["companies"]]))

    for company in data["companies"]:
        render_company(company, add)

    # -- summary ---------------------------------------------------------------
    add("## Audit summary\n")
    rows = []
    tot = dict(docs=0, obs=0, ev=0, ch=0, un=0, rv=0, fi=0, addr=0, hist=0)
    for c0 in data["companies"]:
        docs = c0["documents"]
        changes = c0["change_count"]
        unk = sum(len(pr["structured_data"].get("unknown_sections") or [])
                  for d0 in docs for o0 in d0["observations"]
                  for pr in o0["parse_results"])
        findings = len(c0.get("audit_findings", []))
        addr = len(c0.get("address_audit", []))
        hist = c0.get("history_counts", {})
        htot = sum(hist.values()) if hist else 0
        rows.append([c0["registration_number"],
                     len(c0["source_events"]), len(docs),
                     sum(1 for d0 in docs if d0["logical_document"]["pdf_type"]
                         == "FelagPdf"),
                     sum(1 for d0 in docs if d0["logical_document"]["pdf_type"]
                         == "LysingPdf"),
                     changes, unk, len(c0["review_issues"]), findings_note(c0)])
        tot["docs"] += len(docs); tot["ev"] += len(c0["source_events"])
        tot["ch"] += changes; tot["un"] += unk; tot["rv"] += len(c0["review_issues"])
        tot["addr"] += addr; tot["hist"] += htot
    add(table(["Company", "Source rows", "PDFs", "Felag", "Lysing",
               "Changes", "Unknown sections", "Review issues", "Audit findings"],
              rows,
              ["---", "---:", "---:", "---:", "---:", "---:", "---:", "---:", "---"]))
    nc = len(data["companies"])
    add(f"""```text
{nc} companies audited
{tot['docs']} documents inspected
{tot['ev']} source rows inspected
{tot['ch']} changes inspected
{tot['addr']} addresses inspected
{tot['hist']} temporal entries inspected
{tot['un']} unknown sections
{tot['rv']} review issues
{sum(len(c0.get('audit_findings', [])) for c0 in data['companies'])} audit findings
```""")

    # §31 manual control-pass verification summary -----------------------------
    mv = data.get("mechanical_verification") or {}
    add("\n## Mekanisk kontrol (§31) — samlet bevis\n")
    add(table(["Kontrol", "Resultat"], [[k, v] for k, v in mv.items()]))  # J-2: only what finalize computed

    obs = data.get("presentation_observations") or []
    if obs:
        add("\nPræsentations-observationer (etablerede evidence-konventioner, "
            "ikke materiale-fund):\n")
        add(table(["ID", "Emne", "materiel?"],
                  [[o0["id"], o0["topic"], o0["material"]] for o0 in obs]))
        for o0 in obs:
            add(f"\n<details><summary>{o0['id']} — {o0['topic']}</summary>\n"
                f"```text\n{o0['detail']}\n```\n</details>\n")

    # §38 conclusion + §39 status ------------------------------------------------
    concl = data["audit_conclusion"]
    add(f"\n## Konklusion (§38)\n")
    add(f"**{concl['verdict']}**\n")
    add(concl["text"] + "\n")
    add("\n## Status (§39)\n")
    add(data["status_line"] + "\n")
    return "\n".join(add_lines_fix(md))


def findings_note(c0):
    """Assessment note per company for the audit summary."""
    cls = [c0i["assessment"] for c0i in c0["completeness"]]
    counts = {k: cls.count(k) for k in ("FULLY_STRUCTURED",
                                        "PARTIALLY_STRUCTURED_LOSSLESS",
                                        "REVIEW_REQUIRED")}
    findings = c0.get("audit_findings") or []
    if findings:
        return "; ".join(f["severity"] for f in findings) + " (see findings)"
    return "None recorded — manual control NOT MEASURED (J-2)"


def add_lines_fix(md):
    # helper: markdown already plain; kept for potential post-processing
    return md


def render_company(c0, add):
    reg = c0["registration_number"]
    name = c0.get("company_name_latest") or "(no Felag snapshot)"
    add("\n---\n")
    add(f"# Company {reg} — {name}\n")

    # A -----------------------------------------------------------------------
    add("## A. Company identification\n")
    add(table(["Field", "Value"], [
        ["registration_number", reg],
        ["company_id", c0["company_id"]],
        ["entity_type (selection artifact)", c0.get("entity_type")],
        ["selection rank / hash", f'{c0["selection_rank"]} / `{c0["selection_hash"][:16]}…`'],
        ["company_name (latest Felag snapshot)", c0.get("company_name_latest") or "None"],
    ]))

    # B -----------------------------------------------------------------------
    add("## B. Source events\n")
    if not c0["source_events"]:
        add("None\n")
    else:
        for ev in c0["source_events"]:
            add(f"**source_event_id {ev['source_event_id']} — "
                f"{ev['source_event_date']} page {ev['page_no']} "
                f"row {ev['row_ordinal']}** "
                f"(skrasnr `{ev['row_skrasnr']}`, key `{ev['source_row_key']}`, "
                f"discovered `{ev['discovered_at']}`)\n")
            add(fold("raw_text", ev["raw_text"] or ""))

    # C/D ---------------------------------------------------------------------
    add("## C. Logical PDF documents\n")
    add(table(["document_id", "pdf_type", "source_document_id", "kind_hint",
               "cell_role(s)"],
              [[d0["logical_document"]["document_id"],
                d0["logical_document"]["pdf_type"],
                d0["logical_document"]["source_document_id"],
                d0["logical_document"]["pdf_kind_hint"],
                ", ".join(sorted({r["cell_role"] for r in
                                  d0["source_event_relations"]})) or "None"]
               for d0 in c0["documents"]]) if c0["documents"] else "None\n")

    add("## D. PDF observations / versions\n")
    has_multi = False
    for d0 in c0["documents"]:
        if len(d0["observations"]) > 1:
            has_multi = True
    if not has_multi:
        add("Alle logical documents har præcis 1 observation — ingen "
            "multi-version dokumenter i denne udvælgelse.\n")
    for d0 in c0["documents"]:
        did = d0["logical_document"]["document_id"]
        add(f"**document {did}** "
            f"({d0['logical_document']['pdf_type']}, "
            f"source_document_id `{d0['logical_document']['source_document_id']}`)\n")
        for o0 in d0["observations"]:
            add("- observation **%s** — run %s, fetched_at `%s`, "
                "document_snapshot_date `%s`, sha256 `%s…`, %s bytes, "
                "content verified: **%s**, local evidence: %s\n"
                % (o0["observation_id"], o0["collection_run_id"],
                   o0["fetched_at"], o0["document_snapshot_date"],
                   (o0["sha256"] or "")[:16], o0["byte_length"],
                   o0["content"]["verified"],
                   ", ".join(o0["content"].get("local_evidence_paths") or [])
                   or "none"))
        if d0["version_classification"]:
            vc = d0["version_classification"]
            add(f"- versions-sammenligning: `{pretty(vc['pairwise_vs_first_observation'])}` "
                f"→ **{vc['verdict']}**\n")

    # E: extracted text (full, folded) --------------------------------------
    add("## E. Extracted text (komplet, uforkortet)\n")
    for d0 in c0["documents"]:
        for o0 in d0["observations"]:
            title = ("Extracted text — document %s / observation %s "
                     "(%s bytes)" % (d0["logical_document"]["document_id"],
                                     o0["observation_id"],
                                     o0["byte_length"]))
            add(fold(title,
                     f"sha256: {o0['sha256']}\n\n{o0['extracted_text'] or ''}"))

    # F: structured data ------------------------------------------------------
    add("## F. Complete structured parse data\n")
    for d0 in c0["documents"]:
        for o0 in d0["observations"]:
            for pr in o0["parse_results"]:
                meta = {k: pr[k] for k in ("parser_version", "document_kind",
                                           "parse_status", "needs_review",
                                           "review_reason", "parsed_at")}
                add(f"**parse_result {pr['parse_result_id']} (observation "
                    f"{o0['observation_id']}, document "
                    f"{d0['logical_document']['document_id']})** — "
                    f"`{pretty(meta)}`\n")
                add(fold(f"structured_data — parse {pr['parse_result_id']}",
                         pretty(pr["structured_data"]), lang="json"))

    # G: Felag snapshots ------------------------------------------------------
    add("## G. Felag snapshots (company facts pr. snapshot)\n")
    felag_docs = [d0 for d0 in c0["documents"]
                  if d0["logical_document"]["pdf_type"] == "FelagPdf"]
    if not felag_docs:
        add("None\n")
    for d0 in felag_docs:
        for o0 in d0["observations"]:
            for pr in o0["parse_results"]:
                sd = pr["structured_data"] or {}
                if sd.get("document_kind") != "COMPANY_SNAPSHOT":
                    continue
                cap = sd.get("capital") or {}
                ca = sd.get("company_address") or {}
                add(f"**snapshot observation {o0['observation_id']}**\n")
                add(table(["Field", "Value"], [
                    ["company_name", sd.get("company_name")],
                    ["registration_number", sd.get("registration_number")],
                    ["snapshot_date", sd.get("snapshot_date")],
                    ["alias_names", ", ".join(sd.get("alias_names") or []) or None],
                    ["address role / verbatim",
                     f'{ca.get("role")} / {ca.get("verbatim")}'],
                    ["street / postal / city / kommuna",
                     f'{ca.get("street")} / {ca.get("postal_code")} / {ca.get("city")} / {ca.get("kommuna")}'],
                    ["postrum", ca.get("postrum")],
                    ["capital", f'{cap.get("currency")} {cap.get("amount")} ({cap.get("verbatim")})'],
                    ["purpose", (sd.get("purpose") or "")[:120]],
                    ["signing_rules", sd.get("signing_rules")],
                    ["accounts_dates", ", ".join(sd.get("accounts_dates") or []) or None],
                    ["board members", len(sd.get("board") or [])],
                    ["management members", len(sd.get("management") or [])],
                    ["unknown sections", len(sd.get("unknown_sections") or [])],
                ]))

    # H: people/roles/addresses ----------------------------------------------
    add("## H. People / roles / addresses\n")
    prows = []
    for d0 in c0["documents"]:
        if d0["logical_document"]["pdf_type"] != "FelagPdf":
            continue
        for o0 in d0["observations"]:
            for pr in o0["parse_results"]:
                sd = pr["structured_data"] or {}
                if sd.get("document_kind") != "COMPANY_SNAPSHOT":
                    continue
                ca = sd.get("company_address")
                if ca:
                    prows.append(["COMPANY", "-", sd.get("company_name") or reg,
                                  ca.get("verbatim"), ca.get("role")])
                for member in (sd.get("board") or []) + \
                        (sd.get("management") or []):
                    if not isinstance(member, dict):
                        prows.append(["?", "-", str(member), None, None])
                        continue
                    a0 = member.get("address") or {}
                    prows.append([
                        "BOARD" if member in (sd.get("board") or [])
                        else "MANAGEMENT",
                        member.get("role"), member.get("name"),
                        a0.get("verbatim"), a0.get("role")])
    if prows:
        add(table(["Type", "Rolle", "Navn", "Adresse", "Address role"],
                  prows))
    else:
        add("None\n")

    # I: lysing changes ---------------------------------------------------------
    add("## I. Lysing changes (REGISTRATION_NOTICE)\n")
    any_change = False
    for d0 in c0["documents"]:
        if d0["logical_document"]["pdf_type"] != "LysingPdf":
            continue
        for o0 in d0["observations"]:
            for pr in o0["parse_results"]:
                sd = pr["structured_data"] or {}
                if not sd.get("changes"):
                    continue
                any_change = True
                header_fields = {}
                for hf in ("registration_number", "notice_date",
                           "registration_date", "journal_number",
                           "reporter", "event_id"):
                    if sd.get(hf) is not None:
                        header_fields[hf] = sd.get(hf)
                add(f"**parse_result {pr['parse_result_id']} / observation "
                    f"{o0['observation_id']} (document {d0['logical_document']['document_id']})** — "
                    f"header: `{pretty(header_fields)}`\n")
                add(table(["Date", "Type", "Action", "Entity", "Raw text"],
                          [[ch.get("effective_date")
                            or ch.get("notice_date")
                            or ch.get("registration_date"),
                            ch.get("type"), ch.get("action"),
                            ch.get("entity"), (ch.get("raw_text") or "")[:70]]
                           for ch in sd["changes"]]))
                add(fold("complete changes[] JSON", pretty(sd["changes"]),
                         lang="json"))
    if not any_change:
        add("None\n")

    # Complete per-change data after tables (§19) - full JSON of each change is
    # already inside the F structured_data fold above.

    # J: unknown sections -------------------------------------------------------
    add("## J. Unknown sections (fuldt verbatim)\n")
    unk_rows = []
    unk_blocks = []
    for d0 in c0["documents"]:
        for o0 in d0["observations"]:
            for pr in o0["parse_results"]:
                us = (pr["structured_data"] or {}).get("unknown_sections") or []
                for u0 in us:
                    unk_rows.append([d0["logical_document"]["document_id"],
                                     o0["observation_id"],
                                     pr["parse_result_id"],
                                     u0.get("label"),
                                     u0])
                    unk_blocks.append((d0["logical_document"]["document_id"],
                                       o0["observation_id"],
                                       pr["parse_result_id"], u0))
    if unk_rows:
        add(table(["document", "observation", "parse", "label", "antal linjer"],
                  [[r[0], r[1], r[2], r[3],
                    len((r[4].get("verbatim") or "").splitlines())]
                   for r in unk_rows]))
        add("### Fulde unknown-section-indhold\n")
        for (did, oid, pr_id, u0) in unk_blocks:
            add(fold(f'unknown-section "{u0.get("label")}" (document {did}, '
                     f'observation {oid}, parse {pr_id})',
                     u0.get("verbatim") or u0.get("body") or pretty(u0)))
    else:
        add("None\n")

    # K: review -----------------------------------------------------------------
    add("## K. Review issues\n")
    if c0["review_issues"]:
        add(table(["issue", "document", "observation", "detail"],
                  [[r["issue"], r["document_id"],
                    r["document_observation_id"], r["detail"]]
                   for r in c0["review_issues"]]))
    else:
        add("Review issues: 0\n")
    if c0["run_log_signals"]:
        add("\nRun-log signaler (WARN/STOPPED/REVIEW/FAILED):\n")
        add(table(["run", "step", "status", "error_type", "detail", "at"],
                  [[r["run_id"], r["step"], r["status"], r["error_type"],
                    r["detail"], r["created_at"]]
                   for r in c0["run_log_signals"]]))
    else:
        add("\nRun-log signals: 0\n")

    # L: snapshot timeline --------------------------------------------------------
    add("## L. Snapshot timeline (company_pdf_snapshot_history)\n")
    if c0["snapshot_history"]:
        add(table(["valid_from", "valid_to", "observation", "sha256 (prefix)",
                   "company name", "company address"],
                  [[r["valid_from"], r["valid_to"] or "OPEN",
                    r["document_observation_id"], (r["sha256"] or "")[:12] + "…",
                    r["company_name"],
                    ((r["company_address"] or {}).get("verbatim") or "")
                    .replace("\n", ", ")]
                   for r in c0["snapshot_history"]]))
        add("\nHele structured_data bag hvert snapshot står i sektion F "
            "(parse-resultater bag de tilsvarende observationer).\n")
    else:
        add("None\n")

    # M/N/O: histories -------------------------------------------------------------
    entries = (c0.get("derived_change_history") or {}).get("entries") or []
    hist_review = (c0.get("derived_change_history") or {}).get("review") or []

    add("## M. Scalar history (SCALAR_REPLACEMENT)\n")
    scalars = [e0 for e0 in entries if e0.get("family") in SCALAR_FAMILIES]
    if scalars:
        add(table(["Property", "Value", "Valid from", "Valid to", "Evidence"],
                  [[e0["family"], e0.get("entity"), e0.get("from_date"),
                    e0.get("to_date"), (e0.get("raw_text") or "")[:60]]
                   for e0 in scalars]))
    else:
        add("None\n")

    add("\n## N. Membership history (SET_MEMBERSHIP)\n")
    mem = [e0 for e0 in entries if e0.get("family") in MEMBERSHIP_FAMILIES]
    if mem:
        add(table(["Family", "Entity", "Role/action", "Valid from", "Valid to",
                   "Evidence"],
                  [[e0["family"], e0.get("entity"), e0.get("action"),
                    e0.get("from_date"), e0.get("to_date"),
                    (e0.get("raw_text") or "")[:60]] for e0 in mem]))
    else:
        add("None\n")

    add("\n## O. Event-only history (EVENT_ONLY)\n")
    ev_only = [e0 for e0 in entries if e0.get("family") == "EVENT_ONLY"]
    if ev_only:
        add(table(["Date", "Type", "Action", "Entity", "Evidence"],
                  [[e0.get("from_date"), e0.get("type"), e0.get("action"),
                    e0.get("entity"), (e0.get("raw_text") or "")[:60]]
                   for e0 in ev_only]))
    else:
        add("None\n")
    if hist_review:
        add("\nTemporal-udlednings-review (CHANGE_DATE_MISSING o.lign.):\n")
        add(table(["reason", "raw_text"],
                  [[r0.get("reason"), (r0.get("raw_text") or "")[:60]]
                   for r0 in hist_review]))
    else:
        add("\nTemporal-udlednings-review: 0\n")

    # P: dates ---------------------------------------------------------------------
    add("\n## P. All dates\n")
    if c0["dates"]:
        add(table(["Dato", "Type", "Hvor", "Entity"],
                  [[r0.get("value"), r0.get("date_type"), r0.get("where"),
                    r0.get("entity", "")] for r0 in c0["dates"]]))
    else:
        add("None\n")

    # Q: registration gate -----------------------------------------------------------
    add("\n## Q. Registration-number verification\n")
    bad = [g0 for g0 in c0["registration_gate"] if not g0["verified"]]
    add(table(["document", "observation", "parse", "source row regnr",
               "requested", "parsed", "verified"],
              [[g0["document_id"], g0["observation_id"],
                g0["parse_result_id"], ", ".join(g0["source_row_regnr"]),
                g0["requested_regnr"], g0["parsed_regnr"],
                g0["verified"]] for g0 in c0["registration_gate"]]))
    if bad:
        add(f"\n**MISMATCHES: {len(bad)}**\n")
    else:
        add("\nAlle verificeret.\n")

    # R: completeness ------------------------------------------------------------------
    add("\n## R. Audit completeness assessment (pr. dokument)\n")
    add(table(["document", "pdf_type", "assessment", "unknown labels",
               "review issues", "manual kontrol"],
              [[ci["document_id"], ci["pdf_type"], ci["assessment"],
                ", ".join(ci["unknown_section_labels"]) or "",
                ci["review_issues_on_doc"],
                ci.get("manual_agent_control") or "(nedenfor)"]
               for ci in c0["completeness"]]))

    # S: findings -------------------------------------------------------------------------
    add("\n## S. Audit findings\n")
    findings = c0.get("audit_findings") or []
    if findings:
        for finding in findings:
            add(f"**AUDIT_FINDING** ({finding['severity']}) — company {reg}, "
                f"document {finding.get('document_id')}, observation "
                f"{finding.get('observation_id')}\n")
            add(f"- source text: `{finding.get('source_text')}`")
            add(f"- parsed output: `{finding.get('parsed_output')}`")
            add(f"- hvorfor forkert: {finding['why']}\n")
    else:
        add("None\n")

    add("\n## Manuelt kontrolpass (sektion R/S dokumentation)\n")
    add(c0.get("manual_control_note", "(pending)\n"))


def main():
    data = json.loads(JSON_IN.read_text())
    md = render(data)
    MD_OUT.write_text(md)
    print(f"wrote {MD_OUT} ({MD_OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()