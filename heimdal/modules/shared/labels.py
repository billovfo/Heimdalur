"""Presentation-only label maps (frontend quality consolidation §10/§91/§92).

Human product vocabulary for values that are stored as technical enums in the
database. NOTHING here changes stored data — these maps are used ONLY at the
last presentation step. Unknown values fail safe: the caller decides whether to
show the raw token (source-evidence surfaces) or a neutral fallback (product
surfaces).
"""
import re

# --- Ændringslog / event feed (entity_change_event.change_category) ---------
CHANGE_CATEGORY_LABELS = {
    "STATUS_CHANGED": "Status ændret",
    "OWNERSHIP_ADDED": "Ny ejer",
    "OWNERSHIP_REMOVED": "Ejer udtrådt",
    "OWNERSHIP_PERCENTAGE_CHANGED": "Ejerandel ændret",
    "ROLE_APPOINTED": "Ny rolle",
    "ROLE_RESIGNED": "Rolle fratrådt",
    "CONTROL_ACTOR_CHANGED": "Kontrolaktør ændret",
}


def change_category_label(category, summary=None):
    """Human event label; falls back to a neutral phrase, never a raw enum."""
    known = CHANGE_CATEGORY_LABELS.get(category or "")
    if known:
        return known
    if summary:
        # summaries are already human Danish; a summary-only row needs no enum
        return None
    return "Registerændring"


# --- Indsamlerens kørsler (collection_run) ----------------------------------
RUN_TYPE_LABELS = {
    "ACQUISITION": "Dataindsamling",
    "CANONICAL_ENTITY_ASSIGNMENT": "Kanonisk identitet",
    "RELATION_PROCESSING": "Relationsindlæsning",
    "RELATION_CONTEXTS": "Relationskontekster",
    "ROLE_NORMALIZATION": "Rolletilpasning",
    "INDIRECT_OWNERSHIP": "Indirekte ejerskab",
    "TYPE_VALIDATION": "Typevalidering",
    "INVENTORY": "Beholdningsopdatering",
}

RUN_STATUS_LABELS = {
    "COMPLETED": "Afsluttet",
    "RUNNING": "Kører",
    "STOPPED": "Stoppet",
}

FETCH_PHASE_LABELS = {
    "DISCOVERED": "Opdaget — afventer hentning",
    "QUEUED": "I kø",
    "RETRYABLE_ERROR": "Midlertidig fejl — prøves igen",
}

# --- Beregnede ejerkæder (person_owners_tree / dossier) ---------------------
# entities.service.owner_chain_rows sætter path_type ud fra `direct`.


def ownership_shape_label(row):
    """'Ejerform' for en beregnet ejerandel: direkte eller via kæde."""
    if row.get("direct"):
        return "Direkte ejer"
    return "Via ejerkæde"


# --- Dossier sum-tilstande (incoming.sum.state) -----------------------------
SUM_STATE_LABELS = {
    "OVER_100": ("Over 100 %", "badge-danger",
                 "Registrerede andele overstiger 100 % — kilden indeholder modstridende oplysninger."),
    "EXACTLY_100": ("Fuldt registreret (100 %)", "badge-success"),
    "UNDER_100": ("Under 100 %", "badge-warning"),
}


# --- Konstaterede kontroltilstande (dossier) --------------------------------
CONTROL_STATE_LABELS = {
    "REGISTERED_ACTORS": "Registrerede kontrolaktører",
    "NO_REGISTERED_CONTROL_ACTOR": "Ingen registreret kontrolaktør",
    "UNKNOWN": "Kan ikke fastslås fra kilden",
}


# --- Bindende produktvokabular (remediation plan v1, T3.1/F-06) -------------
# BINDENDE tekniske tokens på produktflader. DB-/source-værdier uændrede —
# disse maps bruges KUN ved sidste præsentationstrin. Eksisterende korrekte
# mappings bevares uændrede. Unknown source-roller → neutral fallback
# ("Registreret rolle") på produktflader; original term forbliver synlig under
# source/evidence. Der opdigtes INGEN oversættelser for ukendte roller.
ROLE_TOKEN_LABELS = {
    # bindende (Visible Product UX plan v1 T1.1 / remediation T3.1)
    "Nevnd": "Bestyrelse",
    "Nevndarformaður": "Bestyrelsesformand",
    "Nevndarforkvinna": "Bestyrelsesformand",
    "Nevndarlimur": "Bestyrelsesmedlem",
    "Stjórn": "Ledelse",
    "Stjóri": "Direktør",
    "Forstjóri": "Direktør",
    "Veruligur Eigari": "Reel ejer",
    # phrase variants from graph/control edges (same binding)
    "Veruligur Eigari fyri": "Reel ejer",
    "Hevur Veruligan Eigara": "Reel ejer",
    "Veruligur Eigari fyri ": "Reel ejer",
    # eksisterende korrekte mappings (bevares uændrede)
    "Formaður": "Bestyrelsesformand",
    "Næstformaður": "Næstformand",
    "Næstforkvinna": "Næstformand",
    "Næstformand": "Næstformand",
    "Bestyrelsesmedlem": "Bestyrelsesmedlem",
    "Medlem": "Bestyrelsesmedlem",
    "Bestyrelsesformand": "Bestyrelsesformand",
    "Direktør": "Direktør",
    "Starvsfólkavalt umboð": "Medarbejderrepræsentant",
}

UNKNOWN_ROLE_FALLBACK = "Registreret rolle"

# Tekniske/engelske tokens fra source eller kode — produktlaget viser dansk.
# ULTIMATE PARENT: G4 er ÅBEN — neutral teknisk formulering: dansk glosse +
# kildeterm i parentes (ingen juridisk/ledelsesmæssig betydning fremsættes).
TECHNICAL_TOKEN_LABELS = {
    "FETCHED": "Hentet fra kilde",
    "DIRECT OWNERSHIP": "Direkte ejerskab",
    "OTHER SOURCE RELATION": "Anden kilderelation",
    "REGISTERED CONTROL ACTOR": "Registreret kontrolaktør",
    "ULTIMATE PARENT": "Øverste moderselskab (ULTIMATE PARENT)",
}


def product_role_label(canonical_role_label, raw_label=None):
    """Produktlabel for en kilde-rolle. Kendt token → bindende dansk label.
    Ukendt/None → neutral fallback (aldrig opdigtet oversættelse); den
    originale term forbliver tilgængelig under source/evidence."""
    known = ROLE_TOKEN_LABELS.get(canonical_role_label or "")
    if known:
        return known
    return UNKNOWN_ROLE_FALLBACK


def product_token_label(token):
    """Dansk produktlabel for en teknisk/engelsk token; ukendt token falder
    tilbage til sig selv (source/evidence-surface beslutter visning)."""
    if not token:
        return token
    return TECHNICAL_TOKEN_LABELS.get(str(token).strip().upper(), token)


# --- Beholdnings-/hentningsstatus (remediation plan v1, T3.2/F-07) ----------
# inventory_status er en teknisk hentningsstatus, ikke en juridisk tilstand.
# Produktflader viser dansk; den rå token forbliver i source/evidence.
INVENTORY_STATUS_LABELS = {
    "FETCHED": "Hentet fra kilde",                     # bindende token (T3.1)
    "DISCOVERED": "Opdaget — afventer hentning",
    "QUEUED": "I kø",
    "RETRYABLE_ERROR": "Midlertidig fejl — prøves igen",
}


def inventory_status_label(status):
    """Dansk label for beholdningsstatus; fail-safe fallback, aldrig en
    opdigtet tilstand. None betyder kun 'ikke i hentningsbeholdningen'."""
    if status is None:
        return "Ikke i hentningsbeholdningen"
    return INVENTORY_STATUS_LABELS.get(str(status).strip().upper(),
                                       "Anden beholdningsstatus")

# --- Ændringslog summaries (G-07) -------------------------------------------
# A summary is free text, but older rows embed raw enums ("... med status
# RETRYABLE_ERROR"). Known tokens become their Danish label; any other
# upper-case word (a company name) stays exactly as written.
_SUMMARY_TOKENS = {**INVENTORY_STATUS_LABELS, **CHANGE_CATEGORY_LABELS}
_SUMMARY_TOKEN_RE = re.compile(r"\b[A-Z][A-Z0-9_]{5,}\b")


def change_summary_label(summary):
    """Change summary for a product surface: never a raw enum token."""
    if not summary:
        return summary
    return _SUMMARY_TOKEN_RE.sub(lambda m: _SUMMARY_TOKENS.get(m.group(0), m.group(0)), summary)
