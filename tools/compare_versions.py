#!/usr/bin/env python3
"""
compare_versions.py — logical drift detector between Stock_bot and Stock_bot_desktop.

Why:
    The web edition (Stock_bot) and the desktop edition (Stock_bot_desktop /
    HASAPCY) share ~235 functions in app/db/sqlite.py plus common code in
    app/utils/ and app/services/. Every change to the shared core is applied
    twice by hand — one missed sync eventually shows up as a mismatched
    number in a report, and by then nobody can remember which side is right.

    This tool compares the two trees by function LOGIC (via `ast.parse` +
    `ast.dump` after stripping the docstring), NOT text. That way comments,
    whitespace and blank lines never trigger a false positive; only real
    behavioural drift does.

Usage:
    python tools/compare_versions.py <web_root> <desktop_root>

    Example:
        python tools/compare_versions.py . ../Stock_bot_desktop

Exit codes:
    0 — clean; the two trees are in sync
    1 — drift found (or a compare error); details on stdout

CI hook: drop this into build.bat / pre-push. If it comes back non-zero,
the drift is real — don't guess which side to fix, look at both.
"""
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path
from typing import Iterable

# ═════════════════════════════════════════════════════════════════════════════
# Scope + exemptions
# ═════════════════════════════════════════════════════════════════════════════

# Only these subtrees are considered shared core. Anything else (bot, UI,
# templates, spec files, tests) is expected to differ between the two.
COMPARED_DIRS = ("app/db", "app/utils", "app/services")

# Files that legitimately exist in only one edition — never flagged as
# "missing in the other".
EXEMPT_FILES = frozenset({
    # Desktop-only helpers (no LAN/SSL work in the server edition).
    "app/utils/network.py",
    "app/utils/ssl_cert.py",
})

# Functions that intentionally exist in only one edition.
EXEMPT_FUNCTIONS = frozenset({
    # Desktop-only "wipe all data" flow. The server operator wipes DB by
    # hand on the host, not through the UI — see the docstring on the
    # desktop function for the rationale.
    "wipe_database",
})

# Renderer weights: money functions get shouted about, because a divergence
# here shifts real numbers in reports and takes weeks to detect.
MONEY_FUNCTIONS = frozenset({
    "get_profit_report",
    "get_finance_monthly_trend",
    "get_expenses_summary",
    "get_incomes_summary",
    "add_expense",
    "update_expense",
    "add_income",
    "update_income",
    "get_expense_tmt_rate",
    "apply_inventory_adjustments",
    # Also worth flagging loudly:
    "_compute_usd_from_original",
    "get_stock_qty",
    "cart_finish",
})

# Schema drift is checked separately (schema.sql — it's not Python).
SCHEMA_FILES = ("app/db/schema.sql",)


# ═════════════════════════════════════════════════════════════════════════════
# AST helpers
# ═════════════════════════════════════════════════════════════════════════════

FnAst = ast.FunctionDef | ast.AsyncFunctionDef


def _strip_docstring(node: FnAst) -> None:
    """Remove the leading docstring (if any) from a function's body in-place.

    Docstrings shouldn't count as behavioural drift — copy-pasted logic with
    a slightly reworded docstring is still the same logic.
    """
    if not node.body:
        return
    first = node.body[0]
    if (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Constant)
        and isinstance(first.value.value, str)
    ):
        node.body = node.body[1:]


def _collect_functions(source_path: Path) -> dict[str, FnAst]:
    """Return {qualified_name: FunctionDef} for every top-level and nested function.

    Qualified name is `ClassName.method` for methods, plain name for module-level
    functions. That's enough to catch drift; we don't handle nested closures.
    """
    try:
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
    except SyntaxError as exc:
        raise RuntimeError(f"SyntaxError in {source_path}: {exc}") from exc

    functions: dict[str, FnAst] = {}

    def _visit(node: ast.AST, prefix: str = "") -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qname = f"{prefix}{child.name}"
                if qname in functions:
                    # Two functions with the same name at the same scope
                    # would be a bug in the source — we can only see the
                    # second one anyway; note it and move on.
                    qname += f"@line{child.lineno}"
                functions[qname] = child
                # Don't recurse into function bodies — nested closures are
                # part of the outer function's logic and covered there.
            elif isinstance(child, ast.ClassDef):
                _visit(child, prefix=f"{child.name}.")
            else:
                _visit(child, prefix=prefix)

    _visit(tree)
    return functions


def _function_signature(node: FnAst) -> str:
    """Compact repr of the arg list — used to spot signature-only drift.

    ast.dump on args alone is noisy; this gives a human-readable diff.
    """
    parts: list[str] = []
    a = node.args
    for arg in a.args:
        parts.append(arg.arg)
    if a.vararg:
        parts.append("*" + a.vararg.arg)
    if a.kwonlyargs:
        if not a.vararg:
            parts.append("*")
        parts.extend(kw.arg for kw in a.kwonlyargs)
    if a.kwarg:
        parts.append("**" + a.kwarg.arg)
    return "(" + ", ".join(parts) + ")"


def _body_hash(node: FnAst) -> str:
    """AST dump of the body after stripping the docstring."""
    _strip_docstring(node)
    return ast.dump(ast.Module(body=list(node.body), type_ignores=[]),
                    include_attributes=False)


# ═════════════════════════════════════════════════════════════════════════════
# Comparison
# ═════════════════════════════════════════════════════════════════════════════


def _iter_py_files(root: Path) -> Iterable[Path]:
    for d in COMPARED_DIRS:
        base = root / d
        if not base.is_dir():
            continue
        for py in sorted(base.rglob("*.py")):
            rel = py.relative_to(root).as_posix()
            if rel in EXEMPT_FILES:
                continue
            if py.name == "__init__.py" and py.stat().st_size < 200:
                continue  # empty package markers — nothing to compare
            yield py


def compare(web_root: Path, desktop_root: Path) -> int:
    """Return the number of drift items found (0 = clean)."""
    web_files:  dict[str, Path] = {}
    dsk_files:  dict[str, Path] = {}
    for py in _iter_py_files(web_root):
        web_files[py.relative_to(web_root).as_posix()] = py
    for py in _iter_py_files(desktop_root):
        dsk_files[py.relative_to(desktop_root).as_posix()] = py

    drift = 0
    money_drift: list[str] = []

    def _emit(msg: str, *, money: bool = False) -> None:
        nonlocal drift
        drift += 1
        prefix = "!! MONEY " if money else "   "
        print(f"{prefix}{msg}")
        if money:
            money_drift.append(msg)

    # ── Files present only in one side ────────────────────────────────────
    only_web = sorted(set(web_files) - set(dsk_files) - EXEMPT_FILES)
    only_dsk = sorted(set(dsk_files) - set(web_files) - EXEMPT_FILES)
    for rel in only_web:
        _emit(f"[file] only in web edition:     {rel}")
    for rel in only_dsk:
        _emit(f"[file] only in desktop edition: {rel}")

    # ── Files present on both sides — compare functions ──────────────────
    common = sorted(set(web_files) & set(dsk_files))
    for rel in common:
        try:
            web_fns = _collect_functions(web_files[rel])
            dsk_fns = _collect_functions(dsk_files[rel])
        except RuntimeError as exc:
            _emit(f"[parse-error] {rel}: {exc}")
            continue

        # Names only in one side (minus exempted).
        only_web_fns = sorted(set(web_fns) - set(dsk_fns) - EXEMPT_FUNCTIONS)
        only_dsk_fns = sorted(set(dsk_fns) - set(web_fns) - EXEMPT_FUNCTIONS)
        for fn in only_web_fns:
            base = fn.split(".")[-1].split("@")[0]
            _emit(f"[fn]  {rel}: only in web:     {fn}",
                  money=base in MONEY_FUNCTIONS)
        for fn in only_dsk_fns:
            base = fn.split(".")[-1].split("@")[0]
            _emit(f"[fn]  {rel}: only in desktop: {fn}",
                  money=base in MONEY_FUNCTIONS)

        # Bodies of functions common to both sides.
        for name in sorted(set(web_fns) & set(dsk_fns)):
            web_fn, dsk_fn = web_fns[name], dsk_fns[name]

            web_sig = _function_signature(web_fn)
            dsk_sig = _function_signature(dsk_fn)
            if web_sig != dsk_sig:
                base = name.split(".")[-1].split("@")[0]
                _emit(
                    f"[sig] {rel}::{name} "
                    f"web={web_sig} desktop={dsk_sig}",
                    money=base in MONEY_FUNCTIONS,
                )

            web_body = _body_hash(web_fn)
            dsk_body = _body_hash(dsk_fn)
            if web_body != dsk_body:
                base = name.split(".")[-1].split("@")[0]
                _emit(
                    f"[body] {rel}::{name} — logic differs",
                    money=base in MONEY_FUNCTIONS,
                )

    # ── schema.sql — plain text comparison ────────────────────────────────
    for rel in SCHEMA_FILES:
        w = web_root / rel
        d = desktop_root / rel
        if not w.exists() and not d.exists():
            continue
        if not w.exists():
            _emit(f"[schema] {rel}: missing on web side")
            continue
        if not d.exists():
            _emit(f"[schema] {rel}: missing on desktop side")
            continue
        # Normalise whitespace so accidental blank-line drift doesn't fire.
        def _norm(p: Path) -> str:
            return "\n".join(
                line.rstrip() for line in p.read_text(encoding="utf-8").splitlines()
                if line.strip()
            )
        if _norm(w) != _norm(d):
            _emit(f"[schema] {rel} differs between editions", money=True)

    # ── Summary ────────────────────────────────────────────────────────────
    print()
    if drift == 0:
        print("✓ CLEAN — both editions are in sync across shared core "
              f"({', '.join(COMPARED_DIRS)}).")
        return 0

    print(f"✗ DRIFT — {drift} item(s) diverged.")
    if money_drift:
        print()
        print("!! MONEY-CRITICAL divergences (fix these first):")
        for msg in money_drift:
            print(f"   {msg}")
    return 1


# ═════════════════════════════════════════════════════════════════════════════
# CLI
# ═════════════════════════════════════════════════════════════════════════════


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    p.add_argument("web_root",     help="path to the Stock_bot repo (server edition)")
    p.add_argument("desktop_root", help="path to the Stock_bot_desktop repo")
    args = p.parse_args()

    web = Path(args.web_root).resolve()
    dsk = Path(args.desktop_root).resolve()

    if not web.is_dir():
        p.error(f"web_root is not a directory: {web}")
    if not dsk.is_dir():
        p.error(f"desktop_root is not a directory: {dsk}")

    print(f"Comparing shared core:")
    print(f"  web     : {web}")
    print(f"  desktop : {dsk}")
    print(f"  scope   : {', '.join(COMPARED_DIRS)}")
    print(f"  ignored : {sorted(EXEMPT_FILES)}")
    print(f"  ignored functions : {sorted(EXEMPT_FUNCTIONS)}")
    print()

    return 1 if compare(web, dsk) else 0


if __name__ == "__main__":
    sys.exit(main())
