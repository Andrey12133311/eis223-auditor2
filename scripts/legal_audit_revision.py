"""Deterministic, process-cached signatures of the installed legal pipeline.

The registry is explicit: schedulers, UI, git metadata and timestamps are not
legal rule inputs. AST dependency closure follows local definitions/constants;
comments, formatting and docstrings never invalidate previous legal analysis.
"""
from __future__ import annotations

import ast
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import sys


ANALYZER_REGISTRY = {
    "v324_fas_practice": ("install",),
    "v330_amendments_223": ("install",),
    "v331_broad_legal": ("install", "verified"),
    "v333_dedup_ui": ("install.analyze_unique", "install.checks_unique"),
    "v336_legal_matrix": ("install",),
    "v337_all_legal_categories": ("signals", "install.build_checks"),
    "v339_fact_comparison": ("install",),
    "v341_precise_legal": ("install",),
    "v348_cross_document_legal": ("install",),
    "v372_auto_regulation_protocols": ("install",),
}
DEPENDENCY_REGISTRY = {
    "legal_applicability": ("competitive_scope", "competitive_rule_scope"),
    "official_publication_evidence": ("annotate_protocol_publications", "protocol_publication_verifier",
                                      "verified_protocol_publication"),
    "eis223_soap_xml": ("parse_document", "quarantine_document", "revision_key"),
    "v294_eis_bridge": ("parse_notices", "soap_metadata_incomplete"),
    "v295_eis_originals": ("saved_document", "uid_from_url", "allowed_documents", "_merge_source_metadata"),
    "v298_eis_only": ("official_xml_documents", "official_notice", "official_notice_record", "official_notice_revisions",
                      "verified_xml_nmcd", "federal_workday", "federal_workdays_between", "offline_audit",
                      "saved_review_context"),
    "v310_archive_scan": ("extract_members",),
    "v311_verified_customer": ("_official_source", "_verified_money", "read_verified_price"),
    "v314_eis_metadata": ("document_text", "table_pairs", "from_documents"),
    "v324_official_price": ("prices_in_text", "verified_nmcd"),
    "v332_all_procurements_audit": ("as_procurement", "source_verified", "stored_docs.read_one", "merge_docs", "is_official",
                                   "collect_saved_evidence", "_require_reconciled_rows"),
    "v335_price_recovery": ("money",),
    "v341_eis_facts": ("enrich", "persist_facts", "card_predates_notice", "reconcile_notice_context"),
    "v342_identity": ("valid_inn", "subject", "customer_label", "organization_display_name"),
    "v342_thematic_queue": ("independent_review",),
    "v347_text_cache": ("cached_extract",),
    "v348_cross_document_legal": ("event_occurrence_text", "finding_protocol_number", "finding_clarification_number",
                                  "finding_original_identity", "finding_binary_identity", "same_original_identity"),
    "v352_dashboard_truth": ("collapse_legal_events",),
    "v353_episode_guard": ("distinct_new",),
    "v365_consultant_refs": ("legal_identity",),
    "v383_msp_scope": ("resolve",),
}
DECLARED_REVISION_REGISTRY = {
    "v332_all_procurements_audit": ("RULE_REVISION",),
    "v345_thematic_accelerator": ("ANALYSIS_VERSION",),
}
OPERATIONAL_DEPENDENCIES = {
    # Only the concurrency setting is cosmetic. Keep the whole collector and
    # stored_docs body, including filtering, status and unreadable accounting.
    "v332_all_procurements_audit": frozenset(("FILE_BATCH_SIZE",)),
}
BASE_ENGINE_ROOTS = ("analyze", "build_checks", "extract_text", "doc_type")
MAX_ENGINE_FILE_BYTES = 4 * 1024 * 1024


class EngineSignatureError(RuntimeError):
    """The engine registry could not be read completely; retry, never acknowledge."""


class _CanonicalAST(ast.NodeTransformer):
    def __init__(self, filename):
        self.filename = filename

    def _body(self, node):
        self.generic_visit(node)
        if node.body and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant) and isinstance(node.body[0].value.value, str):
            node.body.pop(0)
        return node

    visit_Module = _body
    visit_FunctionDef = _body
    visit_AsyncFunctionDef = _body
    visit_ClassDef = _body

    def visit_Expr(self, node):
        # Logging does not change evidence qualification or emitted findings.
        if isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name) and node.value.func.id == "print":
            return None
        # This single runtime boundary has explicitly side-effect-free,
        # best-effort observation statements. Exclude only standalone calls
        # with inert name/literal arguments. Nested execution, assignments,
        # conditions, decorators and changed legal gates remain substantive.
        call = node.value
        if (self.filename == "v342_thematic_queue" and isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute) and call.func.attr == "observe"
                and isinstance(call.func.value, ast.Name) and call.func.value.id == "audit_funnel"
                and call.args and isinstance(call.args[0], ast.Constant)
                and call.args[0].value in {"gate", "documents_thematic", "incomplete", "analyzer_results", "checklist_results",
                                           "thematic_reviews", "selected_before_collapse"}
                and not call.keywords
                and all(isinstance(arg, (ast.Name, ast.Constant)) for arg in call.args)):
            return None
        return self.generic_visit(node)


def _bound_names(node):
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return [node.name]
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        return [alias.asname or alias.name.split(".")[0] for alias in node.names]
    if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        return [item.id for target in targets for item in ast.walk(target) if isinstance(item, ast.Name)]
    # A selected mutable constant includes its module-level initialization,
    # e.g. WRITTEN_NUMBERS.update(...), not only the initial assignment.
    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
        function = node.value.func
        if isinstance(function, ast.Attribute) and isinstance(function.value, ast.Name):
            return [function.value.id]
    return []


def canonical_engine_ast(source, roots, filename="<engine>", ignored_dependencies=()):
    """Select explicit legal roots and all local definition dependencies."""
    try:
        tree = _CanonicalAST(filename).visit(ast.parse(source, filename=filename))
    except (SyntaxError, ValueError, RecursionError) as exc:
        raise EngineSignatureError("Cannot parse legal engine " + filename) from exc
    definitions = {}
    for node in tree.body:
        for name in _bound_names(node):
            definitions.setdefault(name, []).append(node)
        # Imported parser libraries may be optional, with import fallbacks.
        if isinstance(node, ast.Try):
            for nested in ast.walk(node):
                if isinstance(nested, (ast.Import, ast.ImportFrom)):
                    for name in _bound_names(nested):
                        definitions.setdefault(name, []).append(node)
    def resolve_root(root):
        if "." not in root:
            if root not in definitions:
                raise EngineSignatureError("Missing legal registry root " + filename + ":" + root)
            return definitions[root]
        else:
            names = root.split(".")
            candidates = definitions.get(names[0], [])
            for name in names[1:]:
                candidates = [child for candidate in candidates for child in ast.walk(candidate)
                              if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and child.name == name]
            if not candidates:
                raise EngineSignatureError("Missing legal registry root " + filename + ":" + root)
            return candidates
    selected, pending = {}, []
    for root in roots:
        pending.extend(resolve_root(root))
    while pending:
        node = pending.pop()
        identity = id(node)
        if identity in selected:
            continue
        selected[identity] = node
        dependencies = {item.id for item in ast.walk(node) if isinstance(item, ast.Name) and isinstance(item.ctx, ast.Load)}
        for dependency in sorted(dependencies):
            if dependency not in ignored_dependencies:
                pending.extend(definitions.get(dependency, []))
    # Preserve executable order and repeated mutations. Sorting/deduplicating
    # dumps would miss a changed final value after reordered updates/appends.
    ordered = sorted(selected.values(), key=lambda node: (node.lineno, node.col_offset))
    return [ast.dump(node, annotate_fields=True, include_attributes=False) for node in ordered]


def _read_engine(path):
    try:
        with path.open("rb") as stream:
            source = stream.read(MAX_ENGINE_FILE_BYTES + 1)
        if len(source) > MAX_ENGINE_FILE_BYTES:
            raise EngineSignatureError("Legal engine exceeds source limit: " + path.name)
        return source.decode("utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise EngineSignatureError("Cannot read legal engine " + path.name) from exc


@lru_cache(maxsize=1)
def engine_signature() -> str:
    """Compute once per process/deployment; tests may use cache_clear()."""
    folder = Path(__file__).resolve().parent
    modules = {}
    for registry in (ANALYZER_REGISTRY, DEPENDENCY_REGISTRY, DECLARED_REVISION_REGISTRY):
        for name, roots in registry.items():
            modules[name] = modules.get(name, ()) + roots
    payload = {}
    for name, roots in sorted(modules.items()):
        payload[name] = canonical_engine_ast(_read_engine(folder / (name + ".py")), roots, name,
                                             OPERATIONAL_DEPENDENCIES.get(name, ()))
    payload["base_app"] = canonical_engine_ast(_read_engine(folder.parent / "app.py"), BASE_ENGINE_ROOTS, "app")
    # Production restores then patches entry.py before installing legal wrappers.
    # Read its source without importing the app or executing initialization.
    entry = sys.modules.get("entry")
    if entry is not None and getattr(entry, "__file__", None):
        payload["installed_entry"] = canonical_engine_ast(_read_engine(Path(entry.__file__)), BASE_ENGINE_ROOTS, "entry")
    raw = json.dumps({"format": 1, "modules": payload}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
