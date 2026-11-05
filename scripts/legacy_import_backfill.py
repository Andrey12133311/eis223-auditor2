"""Retire the obsolete V204 import-time queue rebuild in the VPS-only runner.

The active collector and audit queues are initialized independently after import.
Keep every legacy queue row and the explicit recovery function; only suppress
the unconditional startup walk which rewrites all documents before HTTP starts.
"""
from __future__ import annotations

import ast
from copy import deepcopy
import os
from pathlib import Path
import tempfile

MARKER = "V204_LEGACY_IMPORT_BACKFILL_SKIPPED"


def patch_text(source):
    tree = ast.parse(source)
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                 and node.name == "install_v204"]
    if len(functions) != 1:
        raise RuntimeError("Expected the verified V204 installer")
    function = functions[0]
    recovery = [node for node in function.body if isinstance(node, ast.FunctionDef)
                and node.name == "recover_queue"]
    if len(recovery) != 1:
        raise RuntimeError("Expected the verified legacy recovery function")
    calls = []

    def visit(node):
        # A recovery invoked explicitly inside a handler is not an import.
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            return
        if (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name)
                and node.value.func.id == "recover_queue"
                and not node.value.args and not node.value.keywords):
            calls.append(node.value)
        for child in ast.iter_child_nodes(node):
            visit(child)

    for statement in function.body:
        visit(statement)
    if not calls and MARKER in ast.get_source_segment(source, function):
        return source
    if len(calls) != 1:
        raise RuntimeError("Expected one unconditional legacy queue recovery")
    recovery = recovery[0]
    # The inspected, checksum-verified recovery contains one schema transaction
    # followed by the per-document enqueue loop. Clone only its schema prefix;
    # preserve the original function for explicit maintenance callers.
    if (len(recovery.body) != 2 or not isinstance(recovery.body[0], ast.With)
            or not isinstance(recovery.body[1], ast.For)):
        raise RuntimeError("Legacy recovery shape differs from verified source")
    transaction = recovery.body[0]
    update = [index for index, node in enumerate(transaction.body)
              if any(isinstance(value, ast.Constant) and isinstance(value.value, str)
                     and value.value == "UPDATE document_jobs_v204 SET status='pending',due=0 WHERE status='running'"
                     for value in ast.walk(node))]
    if len(update) != 1 or update[0] != len(transaction.body)-2:
        raise RuntimeError("Expected schema followed by legacy queue reset and document scan")
    scan = transaction.body[-1]
    if not any(isinstance(value, ast.Constant) and value.value == "SELECT * FROM docs"
               for value in ast.walk(scan)):
        raise RuntimeError("Expected the verified full document scan")
    schema = deepcopy(recovery)
    schema.name = "_v204_initialize_schema_only"
    schema.body = [deepcopy(transaction)]
    schema.body[0].body = schema.body[0].body[:update[0]]
    if not schema.body[0].body:
        raise RuntimeError("Legacy schema initializer is empty")
    target = calls[0]
    lines = source.encode("utf-8").splitlines(keepends=True)
    start = sum(map(len, lines[:target.lineno-1])) + target.col_offset
    end = sum(map(len, lines[:target.end_lineno-1])) + target.end_col_offset
    raw = source.encode("utf-8")
    replacement = ("(_v204_initialize_schema_only(), print('"+MARKER+
                   " active_pipeline=official_eis_vps existing_queues_preserved=True', flush=True))").encode()
    # The added initializer belongs to the same closure, keeping db() and all
    # original migration logic available without publishing the private source.
    insert = sum(map(len, lines[:recovery.end_lineno]))
    indent = " " * recovery.col_offset
    initializer = ("\n" + "\n".join(indent+line for line in ast.unparse(schema).splitlines()) + "\n").encode()
    edits = [(start, end, replacement), (insert, insert, initializer)]
    for first, last, value in sorted(edits, reverse=True):
        raw = raw[:first] + value + raw[last:]
    result = raw.decode("utf-8")
    compile(result, "entry.py", "exec")
    return result


def patch_file(path, *, source_mode):
    if source_mode != "OFFICIAL_EIS_VPS_ONLY":
        raise ValueError("Legacy backfill suppression requires the active VPS-only pipeline")
    path = Path(path)
    source = path.read_text("utf-8")
    result = patch_text(source)
    if result == source:
        return False
    descriptor, temporary = tempfile.mkstemp(prefix=".entry-legacy-backfill-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write(result)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return True
