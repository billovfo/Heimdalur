#!/usr/bin/env python3
"""Route matrix: request every GET route of the Heimdal portal once.

Discovery walks ``heimdal.app``'s ``app.url_map``.  Every rule whose methods
include GET is requested; only the Flask ``static`` rule is skipped (and named
in the report).  Path converters are filled with sample ids read read-only from
the DBs named by the environment, or from ``--ids``.  A route whose parameter
has no sample is reported as ``NO_SAMPLE`` and counted, never dropped.

Two request modes:

* default (in-process): Flask's test client, ``Host: 127.0.0.1``;
* ``--base-url http://127.0.0.1:8431``: stdlib ``urllib``, the given Host
  header, redirects are not followed, 30 s timeout.

Output is a JSON report on stdout (or in ``--json PATH``) and a one-line
summary on stderr.  Exit 1 if any 5xx or exception occurs, 2 if the only
problem is ``NO_SAMPLE``, else 0.  The tool only sends GET.  A GET route that
writes on its call path is skipped as ``SKIPPED_WRITES`` and listed; the write
detector resolves local imports and nested defs, and fails closed (skips) on
unresolved calls or a call chain deeper than its limit.

In-process mode refuses to start unless ``HEIMDAL_READ_DB`` is explicitly set,
and also ``HEIMDAL_PDFHIST_PROJECTION_DSN`` when a PDF-history flag is on.
HTTP mode leaves the remote portal's configuration alone.

Usage:
    HEIMDAL_READ_DB=... python3 tools/route_matrix.py
    python3 tools/route_matrix.py --base-url http://127.0.0.1:8431
    python3 tools/route_matrix.py --ids /tmp/route-ids.json --json /tmp/route.json

``--ids`` keys (integers unless noted): company_id, person_id, group_id,
pdf_company_id, pdf_person_id, analytics_company_id, review_person_role_id,
entity_id, canonical_entity_id, person_role_id, batch_id (string),
search_q (string), geo_city (string).  The direct converter names win over
the semantic names.

No DSN or secret is ever printed; output is redacted defensively.
"""
from __future__ import annotations

import argparse
import ast
import builtins
import importlib
import inspect
import json
import os
import re
import sys
import textwrap
import time
import traceback
from collections import Counter
from datetime import datetime, timezone
from urllib.error import HTTPError
from urllib.parse import unquote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

# Running ``python3 tools/route_matrix.py`` puts tools/ on sys.path, not the
# repo root; the portal import needs the root.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

HOST = "127.0.0.1"
TIMEOUT = 30.0
STATIC_ENDPOINT = "static"

# Semantic sample names, plus direct converter names accepted in --ids.
ID_KEYS = (
    "company_id", "person_id", "group_id", "pdf_company_id", "pdf_person_id",
    "analytics_company_id", "review_person_role_id", "entity_id",
    "canonical_entity_id", "person_role_id", "batch_id", "search_q",
    "geo_city",
)
STRING_ID_KEYS = {"batch_id", "search_q", "geo_city"}

# review.case is a GET that seals a packet (an INSERT/UPDATE on first open).
# The static detector below finds it too; this pin keeps the skip true even if
# a refactor hides the write behind an unresolvable local import.
WRITE_FALLBACK_ENDPOINTS = {
    "review.case": "GET seals/inserts a review packet on first open",
}

_DSN_RE = re.compile(r"postgres(?:ql)?(?:\+[a-z0-9_]+)?://[^\s'\"<>]+", re.I)
_PASSWORD_RE = re.compile(
    r"((?:password|passwd|pwd)\s*=\s*)[^\s&;]+", re.I)
# Any URL credentials before the @ must never be echoed.
_USERINFO_RE = re.compile(
    r"(?P<scheme>[a-z][a-z0-9+.-]*://)[^/@\s]+@", re.I)
_WRITE_SQL_RE = re.compile(
    r"\b(?:INSERT\s+INTO|UPDATE\b[\s\S]{0,160}?\bSET\b|DELETE\s+FROM|"
    r"TRUNCATE\b|CREATE\s+(?:OR\s+REPLACE\s+)?"
    r"(?:TABLE|INDEX|VIEW|SCHEMA|FUNCTION|TRIGGER)|"
    r"DROP\s+(?:TABLE|INDEX|VIEW|SCHEMA|FUNCTION|TRIGGER)|"
    r"ALTER\s+TABLE|MERGE\s+INTO|"
    r"COPY\s+[\w.\"]+\s+(?:FROM|TO))\b", re.I | re.S)
_READ_SQL_RE = re.compile(
    r"\b(?:SELECT|WITH|SHOW|EXPLAIN|VALUES|PRAGMA|SET)\b", re.I)


def _redact_text(text):
    text = _DSN_RE.sub("<redacted-dsn>", text)
    text = _USERINFO_RE.sub(r"\g<scheme><redacted>@", text)
    return _PASSWORD_RE.sub(r"\1<redacted>", text)


def redact(value):
    """Remove DSN, URL userinfo and password shapes from output.

    Non-string values (ints, None, ...) pass through unchanged so the JSON
    report keeps its types.  Percent-encoded values are checked too, because
    URLs in the report are percent-encoded.
    """
    if not isinstance(value, str):
        return value
    text = _redact_text(value)
    if text == value:
        decoded = unquote(value)
        if decoded != value:
            text = _redact_text(decoded)
    return text


def looks_like_secret(value):
    """True when a string carries a DSN, URL credentials or a password."""
    if not isinstance(value, str):
        return False
    return any(_DSN_RE.search(text) or _USERINFO_RE.search(text)
               or _PASSWORD_RE.search(text)
               for text in (value, unquote(value)))


# --------------------------------------------------------------------------
# Discovery and URL filling
# --------------------------------------------------------------------------

def discover(app):
    """Return (GET routes, static routes) as discovery records.

    Each record carries the Werkzeug Rule under ``rule_obj`` so the caller can
    build a concrete URL; the report writer uses only the JSON-safe fields.
    """
    gets, static = [], []
    for rule in sorted(app.url_map.iter_rules(), key=lambda r: r.rule):
        if "GET" not in rule.methods:
            continue
        record = {
            "rule": rule.rule,
            "endpoint": rule.endpoint,
            "methods": sorted(rule.methods),
            "rule_obj": rule,
        }
        (static if rule.endpoint == STATIC_ENDPOINT else gets).append(record)
    return gets, static


def _converter_name(rule, argument):
    converter = getattr(rule, "_converters", {}).get(argument)
    if converter is None:
        return "UnicodeConverter"
    return type(converter).__name__


def _sample_value(rule, argument, samples):
    """Choose a sample for one route argument, or None when there is none."""
    if argument in samples and samples[argument] is not None:
        return samples[argument]
    path = rule.rule
    if argument == "canonical_entity_id":
        if path.startswith("/groups") or path.startswith("/api/groups"):
            return samples.get("group_id")
        if path.startswith("/persons"):
            return samples.get("pdf_person_id") or samples.get("person_id")
        if path.startswith("/api/analytics/graph"):
            return samples.get("analytics_company_id") or samples.get("company_id")
        if path.startswith("/companies"):
            return samples.get("pdf_company_id") or samples.get("company_id")
        return samples.get("company_id")
    if argument == "person_role_id":
        return samples.get("review_person_role_id")
    if argument == "batch_id":
        return samples.get("batch_id")
    if argument == "entity_id":
        return samples.get("entity_id") or samples.get("company_id")
    return samples.get(argument)


def _query_params(rule, samples):
    """Return (params, missing) for the query parameters a route needs."""
    if rule.endpoint == "search.api_search":
        q = samples.get("search_q")
        return ({"q": q}, []) if q else ({}, [])
    if rule.endpoint == "graph.api_pathfinder":
        # Required: without them the API intentionally answers 400.
        from_id, to_id = samples.get("company_id"), samples.get("person_id")
        missing = [name for name, value in (("from", from_id), ("to", to_id))
                   if value is None]
        return ({"from": from_id, "to": to_id} if not missing else {}), missing
    if rule.endpoint == "graph.pathfinder":
        # Optional; fill them when samples exist, otherwise the page still runs.
        from_id, to_id = samples.get("company_id"), samples.get("person_id")
        return ({"from": from_id, "to": to_id} if from_id and to_id else {}), []
    if rule.endpoint == "geo.api_geo_companies":
        # Exactly one of city/region is required; without it the API answers 400.
        city = samples.get("geo_city")
        return ({"city": city}, []) if city else ({}, ["city"])
    return {}, []


def build_path(rule, samples):
    """Build the concrete request path.  Returns (path or None, missing names)."""
    values, missing = {}, []
    for argument in sorted(rule.arguments):
        value = _sample_value(rule, argument, samples)
        if value is None:
            missing.append(argument)
            continue
        if _converter_name(rule, argument) != "IntegerConverter":
            value = str(value)
        values[argument] = value
    params, query_missing = _query_params(rule, samples)
    missing.extend(query_missing)
    if missing:
        return None, missing
    try:
        _domain, path = rule.build(values)
    except Exception:  # noqa: BLE001 - a malformed rule is a clear NO_SAMPLE
        return None, ["<build>"]
    if params:
        path = path + "?" + urlencode(params, doseq=True)
    return path, []


# --------------------------------------------------------------------------
# Write detection: a GET that writes must never be sent
# --------------------------------------------------------------------------

_MAX_CALL_DEPTH = 12
_PROJECT_ROOT = "heimdal"
_UNRESOLVED = object()
_FAILED_IMPORT = object()

# Unresolved calls whose terminal name is known to be read-only.  Everything
# else unresolved is treated as a possible write (fail closed).  Keep this list
# to pure data/text/cursor operations; never add execute/commit/write/save.
_SAFE_CALL_NAMES = frozenset({
    "add", "append", "clear", "close", "copy", "count", "decode", "difference",
    "discard", "encode", "endswith", "enter_context", "exists", "extend",
    "fetchall", "fetchmany", "fetchone", "findall", "finditer", "format",
    "format_map", "fullmatch", "get", "getlist", "getvalue", "glob", "group",
    "groupdict", "groups", "hexdigest", "index", "intersection", "isdecimal",
    "isdigit",
    "isoformat", "items", "iterdir", "join", "keys", "log", "lower", "lstrip",
    "match", "partition", "pop", "quantize", "read_bytes", "read_text",
    "replace", "resolve", "reverse", "rfind", "rindex", "rjust", "rollback",
    "rglob", "round", "rsplit", "rstrip", "search", "setdefault", "sort",
    "split", "splitlines", "startswith", "strftime", "strip", "subn", "title",
    "transaction", "translate", "union", "update", "upper", "values", "warn",
    "warning", "writeheader", "writerow", "writerows",
    # Flask/werkzeug helpers that may surface as unresolved proxies.
    "abort", "jsonify", "redirect", "render_template", "url_for",
    # logging levels
    "critical", "debug", "error", "exception", "info",
    # GraphRepository reads (the service takes the repository as an object).
    "get_adjacent_edges", "get_control_rows", "get_entities_by_ids",
    "get_entity_by_id", "get_ownership_rows", "get_role_rows",
})


def _docstring_ids(node):
    """id() of every docstring constant under node, so prose is not SQL."""
    found = set()
    for item in ast.walk(node):
        body = getattr(item, "body", None)
        if not isinstance(body, list) or not body:
            continue
        first = body[0]
        if (isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)):
            found.add(id(first.value))
    return found


def _own_nodes(node):
    """Yield nodes belonging to one function, not to nested defs/lambdas."""
    pending = list(ast.iter_child_nodes(node))
    while pending:
        child = pending.pop(0)
        yield child
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef,
                              ast.Lambda, ast.ClassDef)):
            continue
        pending[:0] = list(ast.iter_child_nodes(child))


def _lambda_from_source(source):
    """Extract a lambda expression from a source line that is not standalone.

    ``inspect.getsource`` on a lambda passed inside a call may return the whole
    enclosing statement.  Parse from the ``lambda`` token and trim trailing
    call punctuation until the expression is valid.
    """
    for match in re.finditer(r"\blambda\b", source):
        text = source[match.start():].rstrip()
        for end in range(len(text), 0, -1):
            try:
                tree = ast.parse(text[:end], mode="eval")
            except SyntaxError:
                continue
            if isinstance(tree.body, ast.Lambda):
                return tree.body
    return None


def _import_base(module_name, level, target):
    if not level:
        return target
    parts = module_name.split(".")
    package = parts[:-1]  # the function's module is not a package
    if level > len(package):
        return None
    base = ".".join(package[:len(package) - level + 1])
    return base + "." + target if target else base


class _LocalDef:
    """A nested def, bound to the scope that defines it (not the caller)."""

    __slots__ = ("node", "scope")

    def __init__(self, node, scope):
        self.node = node
        self.scope = scope


class _Alternatives:
    """Possible targets of a conditional call (both branches are checked)."""

    __slots__ = ("targets",)

    def __init__(self, targets):
        self.targets = tuple(targets)


class _Scope:
    """One function's names: imports, nested defs and local assignments."""

    __slots__ = ("module_name", "globals", "node", "qualname", "parent",
                 "bindings", "assignments", "key", "function")

    def __init__(self, module_name, globals_, node, qualname, parent=None,
                 function=None, key=None):
        self.module_name = module_name
        self.globals = globals_
        self.node = node
        self.qualname = qualname
        self.parent = parent
        self.function = function
        self.key = key if key is not None else (module_name, qualname)
        self.bindings = {}
        self.assignments = {}
        self._collect()

    def _collect(self):
        for child in _own_nodes(self.node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.bindings[child.name] = _LocalDef(child, self)
            elif isinstance(child, ast.Import):
                for alias in child.names:
                    bound = alias.asname or alias.name.split(".")[0]
                    try:
                        self.bindings[bound] = importlib.import_module(
                            alias.name if alias.asname
                            else alias.name.split(".")[0])
                    except Exception:  # noqa: BLE001 - unresolved is meaningful
                        self.bindings[bound] = _FAILED_IMPORT
            elif isinstance(child, ast.ImportFrom):
                base = _import_base(self.module_name, child.level,
                                    child.module)
                module = None
                if base:
                    try:
                        module = importlib.import_module(base)
                    except Exception:  # noqa: BLE001
                        module = _FAILED_IMPORT
                for alias in child.names:
                    bound = alias.asname or alias.name
                    if alias.name == "*" or module is _FAILED_IMPORT:
                        self.bindings[bound] = _FAILED_IMPORT
                        continue
                    obj = None
                    try:
                        obj = getattr(module, alias.name)
                    except Exception:  # noqa: BLE001 - may be a submodule
                        try:
                            obj = importlib.import_module(
                                base + "." + alias.name)
                        except Exception:  # noqa: BLE001
                            obj = _FAILED_IMPORT
                    self.bindings[bound] = obj
            elif isinstance(child, ast.Assign):
                value = child.value
                for target in child.targets:
                    if isinstance(target, ast.Name):
                        self.assignments[target.id] = value
                    elif isinstance(target, (ast.Tuple, ast.List)):
                        for elt in target.elts:
                            if isinstance(elt, ast.Name):
                                self.assignments[elt.id] = None
            elif isinstance(child, ast.AnnAssign) and isinstance(child.target, ast.Name):
                self.assignments[child.target.id] = child.value

    def chain(self):
        scope = self
        while scope is not None:
            yield scope
            scope = scope.parent


class _WriteAnalyzer:
    """Static call-path analysis with a conservative (fail-closed) default."""

    def __init__(self, root_module):
        self.root_module = root_module
        self.cache = {}
        self.scope_cache = {}
        self.local_cache = {}
        self.literal_cache = {}

    @staticmethod
    def _unwrap(function):
        try:
            return inspect.unwrap(function)
        except Exception:  # noqa: BLE001 - a broken wrapper is just opaque
            return function

    def is_project(self, function):
        function = self._unwrap(function)
        module = getattr(function, "__module__", "") or ""
        return (module == _PROJECT_ROOT
                or module.startswith(_PROJECT_ROOT + ".")
                or module == self.root_module)

    def scope_for_function(self, function):
        function = self._unwrap(function)
        if function in self.scope_cache:
            return self.scope_cache[function]
        try:
            source = textwrap.dedent(inspect.getsource(function))
        except (OSError, TypeError):
            return None
        try:
            tree = ast.parse(source)
        except (SyntaxError, IndentationError):
            tree = None
        node = None
        if tree is not None:
            node = tree.body[0]
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                node = next((item for item in ast.walk(tree)
                             if isinstance(item, ast.Lambda)), None)
        if node is None:
            node = _lambda_from_source(source)
        if node is None:
            return None
        scope = _Scope(getattr(function, "__module__", ""),
                       getattr(function, "__globals__", {}), node,
                       getattr(function, "__qualname__", "?"),
                       function=function, key=function)
        self.scope_cache[function] = scope
        return scope

    def scope_for_local(self, localdef):
        key = id(localdef)
        if key in self.local_cache:
            return self.local_cache[key]
        owner = localdef.scope
        scope = _Scope(owner.module_name, owner.globals, localdef.node,
                       owner.qualname + "." + localdef.node.name,
                       parent=owner, key=("local", key))
        self.local_cache[key] = scope
        return scope

    def resolve(self, node, scope):
        if isinstance(node, ast.Name):
            for item in scope.chain():
                if node.id in item.bindings:
                    value = item.bindings[node.id]
                    return _UNRESOLVED if value is _FAILED_IMPORT else value
                if node.id in item.assignments:
                    value = item.assignments[node.id]
                    if isinstance(value, (ast.Name, ast.Attribute)):
                        resolved = self.resolve(value, item)
                        if resolved is not _UNRESOLVED:
                            return resolved
                    return _UNRESOLVED
            if node.id in scope.globals:
                return scope.globals[node.id]
            if hasattr(builtins, node.id):
                return getattr(builtins, node.id)
            return _UNRESOLVED
        if isinstance(node, ast.Attribute):
            base = self.resolve(node.value, scope)
            if base is _UNRESOLVED or base is _FAILED_IMPORT or base is None:
                return _UNRESOLVED
            try:
                return getattr(base, node.attr)
            except Exception:  # noqa: BLE001 - proxies may refuse
                return _UNRESOLVED
        if isinstance(node, ast.IfExp):
            targets = [target for target in (
                self.resolve(node.body, scope),
                self.resolve(node.orelse, scope))
                if target is not _UNRESOLVED]
            return _Alternatives(targets) if targets else _UNRESOLVED
        return _UNRESOLVED

    def evidence(self, node, scope, depth=0):
        """Best-effort static text of a SQL expression (may be partial)."""
        if node is None or depth > 6:
            return ""
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.JoinedStr):
            return "".join(self.evidence(value, scope, depth + 1)
                           for value in node.values)
        if isinstance(node, ast.FormattedValue):
            return self.evidence(node.value, scope, depth + 1)
        if isinstance(node, ast.BinOp) and isinstance(node.op,
                                                      (ast.Add, ast.Mod)):
            return (self.evidence(node.left, scope, depth + 1)
                    + self.evidence(node.right, scope, depth + 1))
        if isinstance(node, ast.IfExp):
            return (self.evidence(node.body, scope, depth + 1)
                    + self.evidence(node.orelse, scope, depth + 1))
        if isinstance(node, ast.Name):
            for item in scope.chain():
                if node.id in item.assignments:
                    value = item.assignments[node.id]
                    return ("" if value is None
                            else self.evidence(value, scope, depth + 1))
                if node.id in item.bindings:
                    bound = item.bindings[node.id]
                    return bound if isinstance(bound, str) else ""
            if node.id in scope.globals:
                value = scope.globals[node.id]
                return value if isinstance(value, str) else ""
            return ""
        if isinstance(node, ast.Attribute):
            value = self.resolve(node, scope)
            return value if isinstance(value, str) else ""
        if isinstance(node, ast.Call):
            target = self.resolve(node.func, scope)
            return self.target_evidence(target)
        return ""

    def target_evidence(self, target):
        if isinstance(target, _LocalDef):
            return self.literal_evidence(self.scope_for_local(target))
        if isinstance(target, _Alternatives):
            return "".join(self.target_evidence(item)
                           for item in target.targets)
        if inspect.isfunction(target) and self.is_project(target):
            target_scope = self.scope_for_function(target)
            if target_scope is not None:
                return self.literal_evidence(target_scope)
        return ""

    def literal_evidence(self, scope):
        """All string literals in one function, joined, for SQL heuristics."""
        if scope.key in self.literal_cache:
            return self.literal_cache[scope.key]
        docstrings = _docstring_ids(scope.node)
        chunks = [child.value for child in _own_nodes(scope.node)
                  if isinstance(child, ast.Constant)
                  and isinstance(child.value, str)
                  and id(child) not in docstrings]
        text = "\n".join(chunks)
        self.literal_cache[scope.key] = text
        return text

    def scan(self, scope, depth=0, stack=frozenset()):
        if scope is None:
            return "function source unavailable (fail closed)"
        if scope.key in self.cache:
            return self.cache[scope.key]
        if scope.key in stack:
            return None
        if depth > _MAX_CALL_DEPTH:
            return ("%s: call chain deeper than %d (fail closed)"
                    % (scope.qualname, _MAX_CALL_DEPTH))
        stack = stack | {scope.key}
        docstrings = _docstring_ids(scope.node)
        reason = None
        for node in _own_nodes(scope.node):
            if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                    and id(node) not in docstrings
                    and _WRITE_SQL_RE.search(node.value)):
                reason = "%s contains a SQL write" % scope.qualname
                break
        if reason is None:
            for node in _own_nodes(scope.node):
                if not isinstance(node, ast.Call):
                    continue
                terminal = _terminal_name(node.func)
                if terminal == "commit":
                    reason = "%s commits a transaction" % scope.qualname
                    break
                if terminal in ("execute", "executemany"):
                    evidence = (self.evidence(node.args[0], scope)
                                if node.args else "")
                    if _WRITE_SQL_RE.search(evidence):
                        reason = ("%s contains a SQL write in %s()"
                                  % (scope.qualname, terminal))
                        break
                    if not _READ_SQL_RE.search(evidence):
                        reason = ("%s calls %s() with an unresolved SQL "
                                  "argument (fail closed)"
                                  % (scope.qualname, terminal))
                        break
                target = self.resolve(node.func, scope)
                if target is _UNRESOLVED:
                    if terminal in ("execute", "executemany"):
                        continue  # read-only SQL, checked above
                    if terminal in _SAFE_CALL_NAMES:
                        continue
                    reason = ("%s has an unresolved call to %s (fail closed)"
                              % (scope.qualname, terminal or "<expression>"))
                    break
                found = self.scan_target(target, depth, stack)
                if found:
                    reason = "%s -> %s" % (scope.qualname, found)
                    break
        self.cache[scope.key] = reason
        return reason

    def scan_target(self, target, depth, stack):
        if isinstance(target, _LocalDef):
            return self.scan(self.scope_for_local(target), depth + 1, stack)
        if isinstance(target, _Alternatives):
            for item in target.targets:
                found = self.scan_target(item, depth, stack)
                if found:
                    return found
            return None
        if inspect.ismethod(target):
            target = target.__func__
        if inspect.isfunction(target):
            target = self._unwrap(target)
            if not self.is_project(target):
                return None
            return self.scan(self.scope_for_function(target),
                             depth + 1, stack)
        if (inspect.isclass(target) or inspect.ismodule(target)
                or inspect.isbuiltin(target)):
            return None
        if callable(target):
            return ("%s calls a callable object that cannot be resolved "
                    "(fail closed)" % (getattr(target, "__qualname__", None)
                                       or type(target).__name__))
        return None

    def writes(self, function):
        scope = self.scope_for_function(function)
        return self.scan(scope)


def _terminal_name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def writes_on_get(app):
    """Map endpoint -> reason for every GET route that writes on its path."""
    detected = {endpoint: reason
                for endpoint, reason in WRITE_FALLBACK_ENDPOINTS.items()
                if endpoint in app.view_functions}
    for rule in app.url_map.iter_rules():
        if "GET" not in rule.methods or rule.endpoint == STATIC_ENDPOINT:
            continue
        function = app.view_functions.get(rule.endpoint)
        analyzer = _WriteAnalyzer(getattr(function, "__module__", ""))
        reason = analyzer.writes(function)
        if reason:
            detected[rule.endpoint] = reason
    return {endpoint: redact(reason) for endpoint, reason in sorted(detected.items())}


# --------------------------------------------------------------------------
# Sample ids (read-only)
# --------------------------------------------------------------------------

def _readonly_connect(dsn):
    import psycopg
    return psycopg.connect(
        dsn, connect_timeout=5,
        options="-c default_transaction_read_only=on")


def _scalar(row):
    if row is None:
        return None
    return row[0]


def _query(conn, sql, params, warnings, label):
    try:
        return _scalar(conn.execute(sql, params).fetchone())
    except Exception as exc:  # noqa: BLE001 - sample discovery must not abort
        warnings.append("sample %s unavailable: %s" % (label, type(exc).__name__))
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001
            pass
        return None


def _safe_connect(dsn, label, warnings):
    if not dsn:
        warnings.append("%s is not set; its samples are unavailable" % label)
        return None
    try:
        return _readonly_connect(dsn)
    except Exception as exc:  # noqa: BLE001 - no DSN text in the report
        warnings.append("%s is unreachable: %s" % (label, type(exc).__name__))
        return None


def _canonical_samples(conn, warnings):
    out = {}
    out["company_id"] = _query(
        conn, "SELECT canonical_entity_id FROM canonical_entity "
              "WHERE entity_kind = 'COMPANY' ORDER BY canonical_entity_id LIMIT 1",
        (), warnings, "company_id")
    out["person_id"] = _query(
        conn, "SELECT canonical_entity_id FROM canonical_entity "
              "WHERE entity_kind = 'PERSON' ORDER BY canonical_entity_id LIMIT 1",
        (), warnings, "person_id")
    out["group_id"] = _query(
        conn,
        "SELECT d.owner_id FROM v_direct_ownership d "
        "WHERE d.owner_id <> d.owned_id AND d.parsed_percentage > 50.0 "
        "  AND d.owner_kind IN ('COMPANY', 'ORGANIZATION', 'PUBLIC_AUTHORITY') "
        "GROUP BY d.owner_id "
        "ORDER BY count(*) DESC, d.owner_id LIMIT 1",
        (), warnings, "group_id")
    out["geo_city"] = _query(
        conn,
        "SELECT city FROM geo_postcode "
        "WHERE city IS NOT NULL AND city <> '' "
        "ORDER BY city LIMIT 1",
        (), warnings, "geo_city")
    out["analytics_company_id"] = _query(
        conn,
        "SELECT p.canonical_company_id "
        "FROM registry_analytics.person_company_period p "
        "JOIN registry_analytics.analytics_build b "
        "  ON b.build_run_id = p.build_run_id "
        "WHERE b.status = 'ACTIVE' "
        "GROUP BY p.canonical_company_id "
        "ORDER BY (p.canonical_company_id = 9667) DESC, "
        "         count(*) DESC, p.canonical_company_id LIMIT 1",
        (), warnings, "analytics_company_id")
    return out


_ACTIVE_RUN = (
    "SELECT projection_run_id FROM registry_projection.projection_run "
    "WHERE status = 'ACTIVE' ORDER BY projection_run_id DESC LIMIT 1")


def _projection_samples(conn, warnings):
    out = {}
    out["pdf_company_id"] = _query(
        conn,
        "WITH active AS (" + _ACTIVE_RUN + ") "
        "SELECT l.canonical_entity_id "
        "FROM registry_projection.entity_link l "
        "JOIN active a ON a.projection_run_id = l.projection_run_id "
        "JOIN registry_projection.company_current_state s "
        "  ON s.projection_run_id = l.projection_run_id "
        " AND s.registration_number = l.registration_number "
        "JOIN registry_projection.company_history_event he "
        "  ON he.projection_run_id = s.projection_run_id "
        " AND he.company_id = s.company_id "
        "WHERE l.entity_type = 'COMPANY' AND l.link_status = 'LINKED' "
        "GROUP BY l.canonical_entity_id "
        "ORDER BY (l.canonical_entity_id = 9667) DESC, "
        "         count(*) DESC, l.canonical_entity_id LIMIT 1",
        (), warnings, "pdf_company_id")
    out["pdf_person_id"] = _query(
        conn,
        "WITH active AS (" + _ACTIVE_RUN + ") "
        "SELECT l.canonical_entity_id "
        "FROM registry_projection.entity_link l "
        "JOIN active a ON a.projection_run_id = l.projection_run_id "
        "JOIN registry_projection.company_person_role pr "
        "  ON pr.id = l.person_role_id "
        " AND pr.projection_run_id = l.projection_run_id "
        "WHERE l.entity_type = 'PERSON' AND l.link_status = 'LINKED' "
        "  AND pr.source_scope = 'HISTORICAL_EVENT' "
        "GROUP BY l.canonical_entity_id "
        "ORDER BY (l.canonical_entity_id = 9298) DESC, "
        "         count(DISTINCT pr.id) DESC, l.canonical_entity_id LIMIT 1",
        (), warnings, "pdf_person_id")
    out["review_person_role_id"] = _query(
        conn,
        "WITH active AS (" + _ACTIVE_RUN + ") "
        "SELECT (ref->>'person_role_id')::bigint "
        "FROM registry_projection.person_review_case c "
        "CROSS JOIN LATERAL jsonb_array_elements(c.occurrence_refs) ref "
        "WHERE (ref->>'run_id')::bigint = "
        "      (SELECT projection_run_id FROM active) LIMIT 1",
        (), warnings, "review_person_role_id")
    if out["review_person_role_id"] is None:
        out["review_person_role_id"] = _query(
            conn,
            "WITH active AS (" + _ACTIVE_RUN + ") "
            "SELECT l.person_role_id "
            "FROM registry_projection.entity_link l "
            "JOIN active a ON a.projection_run_id = l.projection_run_id "
            "WHERE l.entity_type = 'PERSON' "
            "  AND l.link_status = 'UNRESOLVED' "
            "ORDER BY l.person_role_id LIMIT 1",
            (), warnings, "review_person_role_id")
    out["batch_id"] = _query(
        conn,
        "SELECT d.frozen_batch_id "
        "FROM registry_projection.person_review_decision d "
        "WHERE d.frozen_batch_id IS NOT NULL AND d.frozen_batch_id <> '' "
        "ORDER BY d.decision_id DESC LIMIT 1",
        (), warnings, "batch_id")
    return out


def discover_sample_ids():
    """Read sample ids read-only from the env DBs.  Returns (ids, warnings)."""
    warnings = []
    ids = {key: None for key in ID_KEYS}
    canonical = _safe_connect(
        os.environ.get("HEIMDAL_READ_DB"),
        "HEIMDAL_READ_DB", warnings)
    projection = _safe_connect(
        os.environ.get("HEIMDAL_PDFHIST_PROJECTION_DSN"),
        "HEIMDAL_PDFHIST_PROJECTION_DSN", warnings)
    try:
        if canonical is not None:
            ids.update(_canonical_samples(canonical, warnings))
        if projection is not None:
            ids.update(_projection_samples(projection, warnings))
    finally:
        for conn in (canonical, projection):
            if conn is not None:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass
    # Generic fallbacks keep a route testable when one specialised sample is
    # absent; a genuinely parameter-less route is still NO_SAMPLE.
    ids["pdf_company_id"] = ids.get("pdf_company_id") or ids.get("company_id")
    ids["pdf_person_id"] = ids.get("pdf_person_id") or ids.get("person_id")
    ids["analytics_company_id"] = (
        ids.get("analytics_company_id") or ids.get("company_id"))
    ids["search_q"] = (
        ids.get("search_q")
        or str(ids.get("analytics_company_id") or ids.get("company_id") or ""))
    return ids, warnings


# --------------------------------------------------------------------------
# Requests
# --------------------------------------------------------------------------

class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # urllib then raises HTTPError, which we record as 3xx


def _status_class(status):
    if status is None:
        return None
    if 100 <= status <= 599:
        return "%dxx" % (status // 100)
    return "other"


def _request_in_process(client, path):
    started = time.perf_counter()
    try:
        response = client.get(path, headers={"Host": HOST})
        return response.status_code, None, None, time.perf_counter() - started
    except Exception as exc:  # noqa: BLE001 - the report records the class
        return None, exc, traceback.format_exc(), time.perf_counter() - started


def _request_http(opener, base_url, host, path, timeout=TIMEOUT):
    started = time.perf_counter()
    request = Request(base_url.rstrip("/") + path,
                      headers={"Host": host,
                               "User-Agent": "heimdal-route-matrix/1"})
    try:
        with opener.open(request, timeout=timeout) as response:
            status = response.status
        return status, None, None, time.perf_counter() - started
    except HTTPError as exc:  # 3xx (no redirect) and 4xx/5xx
        status = exc.code
        exc.close()
        return status, None, None, time.perf_counter() - started
    except Exception as exc:  # noqa: BLE001
        return None, exc, traceback.format_exc(), time.perf_counter() - started


def run(app, samples, *, base_url=None, timeout=TIMEOUT,
        include_traceback=False):
    """Run the matrix.  Returns the JSON-safe report dict."""
    gets, static = discover(app)
    detected_writes = writes_on_get(app)
    results = []
    if base_url:
        validate_base_url(base_url)
        host = urlsplit(base_url).netloc.rsplit("@", 1)[-1]
        opener = build_opener(ProxyHandler({}), _NoRedirect())
        client = None
        mode = "http"
    else:
        app.config["TESTING"] = True  # propagate exceptions; disable rate limit
        client = app.test_client()
        opener = host = None
        mode = "in_process"

    for record in gets:
        rule = record["rule_obj"]
        path, missing = build_path(rule, samples)
        entry = {
            "route": redact(rule.rule),
            "endpoint": redact(rule.endpoint),
            "url": redact(path or rule.rule),
            "status": None,
            "status_class": None,
            "ms": None,
            "error_class": None,
        }
        if record["endpoint"] in detected_writes:
            entry["status_class"] = "SKIPPED_WRITES"
            entry["reason"] = redact(detected_writes[record["endpoint"]])
        elif missing:
            entry["status_class"] = "NO_SAMPLE"
            entry["missing"] = sorted(redact(name) for name in set(missing))
        else:
            if base_url:
                status, exc, tb, seconds = _request_http(
                    opener, base_url, host, path, timeout)
            else:
                status, exc, tb, seconds = _request_in_process(client, path)
            entry["ms"] = round(seconds * 1000, 1)
            if exc is not None:
                entry["status_class"] = "EXCEPTION"
                entry["error_class"] = type(exc).__name__
                if include_traceback:
                    entry["traceback"] = redact(tb)
            else:
                entry["status"] = status
                entry["status_class"] = _status_class(status)
        results.append(entry)

    counts = Counter(entry["status_class"] for entry in results)
    summary = {
        "total": len(results),
        "requested": sum(1 for entry in results
                         if entry["status_class"] not in
                         ("NO_SAMPLE", "SKIPPED_WRITES")),
        "2xx": counts.get("2xx", 0),
        "3xx": counts.get("3xx", 0),
        "4xx": counts.get("4xx", 0),
        "5xx": counts.get("5xx", 0),
        "NO_SAMPLE": counts.get("NO_SAMPLE", 0),
        "EXCEPTION": counts.get("EXCEPTION", 0),
        "SKIPPED_WRITES": counts.get("SKIPPED_WRITES", 0),
        "static_skipped": len(static),
    }
    report = {
        "tool": "route_matrix",
        "mode": mode,
        "base_url": redact(base_url) if base_url else None,
        "host": redact(host) if base_url else HOST,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "summary": summary,
        "ids": {key: redact(samples.get(key)) for key in ID_KEYS},
        "detected_writes": {
            redact(endpoint): redact(reason)
            for endpoint, reason in detected_writes.items()},
        "skipped_static": [
            {"route": redact(record["rule"]),
             "endpoint": redact(record["endpoint"]),
             "reason": "Flask's static endpoint is not part of the route matrix"}
            for record in static
        ],
        "routes": results,
    }
    report["summary"]["exit_code"] = exit_code(report)
    return report


def exit_code(report):
    """1 for 5xx/exception, 2 for NO_SAMPLE alone, else 0."""
    classes = {entry["status_class"] for entry in report["routes"]}
    if "5xx" in classes or "EXCEPTION" in classes:
        return 1
    if "NO_SAMPLE" in classes:
        return 2
    return 0


def summary_line(report):
    s = report["summary"]
    return ("SUMMARY routes=%(total)d 2xx=%(2xx)d 3xx=%(3xx)d 4xx=%(4xx)d "
            "5xx=%(5xx)d NO_SAMPLE=%(NO_SAMPLE)d EXCEPTION=%(EXCEPTION)d "
            "SKIPPED_WRITES=%(SKIPPED_WRITES)d static=%(static_skipped)d"
            % s)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def load_ids(path):
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("--ids must hold a JSON object")
    unknown = sorted(set(data) - set(ID_KEYS))
    if unknown:
        raise ValueError("unknown --ids key(s): %s" % ", ".join(unknown))
    clean = {}
    for key, value in data.items():
        if key in STRING_ID_KEYS:
            if not isinstance(value, str) or not value:
                raise ValueError("--ids %s must be a non-empty string" % key)
            if looks_like_secret(value):
                raise ValueError(
                    "--ids %s must not contain a DSN, URL credentials or "
                    "a password" % key)
            clean[key] = value
        else:
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError("--ids %s must be a positive integer" % key)
            clean[key] = value
    return clean


def _load_app():
    from heimdal.app import app
    return app


_TRUE_ENV = {"1", "true", "yes", "on"}
PDF_HISTORY_FLAGS = ("PDF_HISTORY_INTEGRATION_ENABLED",
                     "PDF_HISTORY_UI_ENABLED")


def _env_flag_on(name, environ=None):
    environ = os.environ if environ is None else environ
    return (environ.get(name) or "").strip().lower() in _TRUE_ENV


def env_guard_problems(environ=None):
    """Reasons an in-process run must not start (empty list when allowed).

    The app's request connections come from ``DB_URI``, whose fallback is a
    live/dev database.  In-process mode therefore requires explicitly named
    read-only sources; HTTP mode is left alone because the remote portal owns
    its own configuration.
    """
    environ = os.environ if environ is None else environ
    problems = []
    if not (environ.get("HEIMDAL_READ_DB") or "").strip():
        problems.append(
            "HEIMDAL_READ_DB is not set; refusing to start an in-process run "
            "against the app's default (live) DB")
    if any(_env_flag_on(name, environ) for name in PDF_HISTORY_FLAGS) and not (
            environ.get("HEIMDAL_PDFHIST_PROJECTION_DSN") or "").strip():
        problems.append(
            "a PDF-history flag is on but HEIMDAL_PDFHIST_PROJECTION_DSN is "
            "not set; refusing to start an in-process run")
    return problems


def validate_base_url(url):
    """Reject HTTP-mode URLs that carry credentials (or are not http(s))."""
    try:
        parts = urlsplit(url)
    except ValueError as exc:
        raise ValueError("--base-url is not a valid URL: %s"
                         % type(exc).__name__)
    if parts.username is not None or parts.password is not None:
        raise ValueError("--base-url must not contain credentials")
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError("--base-url must be an http(s) URL with a host")
    return url


def build_parser():
    parser = argparse.ArgumentParser(
        description="Request every GET route of the Heimdal portal and report 5xx.")
    parser.add_argument("--base-url", metavar="URL",
                        help="real HTTP mode, e.g. http://127.0.0.1:8431")
    parser.add_argument("--ids", metavar="FILE",
                        help="JSON object overriding the sample ids")
    parser.add_argument("--json", metavar="FILE",
                        help="write the JSON report here instead of stdout")
    parser.add_argument("--timeout", type=float, default=TIMEOUT,
                        help="HTTP timeout in seconds (default 30)")
    parser.add_argument("--traceback", action="store_true",
                        help="include a redacted traceback for exceptions")
    return parser


def main(argv=None, *, app=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    if args.base_url:
        try:
            validate_base_url(args.base_url)
        except ValueError as exc:
            parser.error(str(exc))
    else:
        problems = env_guard_problems()
        if problems:
            for problem in problems:
                print("route_matrix: %s" % redact(problem), file=sys.stderr)
            print("route_matrix: refusing an in-process run without explicit "
                  "read-only DSNs; use --base-url for a running portal",
                  file=sys.stderr)
            return 1

    try:
        overrides = load_ids(args.ids) if args.ids else {}
    except (OSError, ValueError) as exc:
        parser.error(redact(str(exc)))

    warnings = []
    samples = {}
    if app is None:
        try:
            app = _load_app()
        except Exception as exc:  # noqa: BLE001
            print("route_matrix: app import failed: %s" % type(exc).__name__,
                  file=sys.stderr)
            return 1
        samples, warnings = discover_sample_ids()
    samples.update(overrides)

    try:
        report = run(app, samples, base_url=args.base_url,
                     timeout=args.timeout, include_traceback=args.traceback)
    except Exception as exc:  # noqa: BLE001 - a broken app is a failed matrix
        print("route_matrix: run failed: %s" % type(exc).__name__, file=sys.stderr)
        return 1
    report["warnings"] = [redact(w) for w in warnings]

    payload = json.dumps(report, indent=2, ensure_ascii=True)
    if args.json:
        try:
            with open(args.json, "w", encoding="utf-8") as handle:
                handle.write(payload + "\n")
        except OSError as exc:
            print("route_matrix: cannot write %s: %s"
                  % (redact(args.json), type(exc).__name__), file=sys.stderr)
            return 1
    else:
        sys.stdout.write(payload + "\n")

    for entry in report["routes"]:
        if entry["status_class"] in ("5xx", "EXCEPTION", "NO_SAMPLE",
                                     "SKIPPED_WRITES"):
            line = "%s %s -> %s" % (
                entry["status_class"], redact(entry["route"]),
                redact(entry["url"]))
            if entry["status_class"] == "SKIPPED_WRITES":
                line += " (%s)" % redact(entry.get("reason") or "write detected")
            print(line, file=sys.stderr)
    print(redact(summary_line(report)), file=sys.stderr)
    for warning in report["warnings"]:
        print("warning: %s" % redact(warning), file=sys.stderr)
    return exit_code(report)


if __name__ == "__main__":
    sys.exit(main())
