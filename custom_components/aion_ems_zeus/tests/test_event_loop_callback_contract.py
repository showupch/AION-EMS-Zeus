"""Regression contract for GitHub issue #3; no HA runtime required."""
import ast
from pathlib import Path

def test_update_engine_state_listener_runs_on_event_loop():
    source = Path(__file__).resolve().parents[1] / "update_engine.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    imports = [node for node in tree.body if isinstance(node, ast.ImportFrom) and node.module == "homeassistant.core"]
    assert any(alias.name == "callback" for node in imports for alias in node.names)
    engine = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "UpdateEngine")
    handler = next(node for node in engine.body if isinstance(node, ast.FunctionDef) and node.name == "_handle_state_changed")
    assert any(isinstance(dec, ast.Name) and dec.id == "callback" for dec in handler.decorator_list)
    calls = [node for node in ast.walk(handler) if isinstance(node, ast.Call)]
    assert any(isinstance(call.func, ast.Name) and call.func.id == "async_call_later" for call in calls)
