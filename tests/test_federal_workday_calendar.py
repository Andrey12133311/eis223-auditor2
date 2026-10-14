"""Regression checks for federal five-day calendar, no network calls."""
import ast
from datetime import date
from pathlib import Path

source=Path(__file__).resolve().parents[1].joinpath("scripts/v298_eis_only.py").read_text(encoding="utf-8")
tree=ast.parse(source)
names={"federal_workday","federal_workdays_between"}
functions=[node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name in names]
namespace={}
exec(compile(ast.Module(body=functions,type_ignores=[]),"<calendar>", "exec"),namespace)
is_workday=namespace["federal_workday"]
assert is_workday(date(2024,4,27)) is True
assert is_workday(date(2024,4,29)) is False
assert is_workday(date(2025,2,24)) is True
assert is_workday(date(2025,3,10)) is True
assert is_workday(date(2025,5,8)) is False
assert is_workday(date(2025,11,1)) is True
assert is_workday(date(2025,11,3)) is False
assert is_workday(date(2026,1,9)) is False
assert is_workday(date(2026,3,9)) is False
assert is_workday(date(2026,5,11)) is False
assert is_workday(date(2026,12,31)) is False
assert is_workday(date(2027,1,11)) is None
print("Federal calendar regression checks passed")
