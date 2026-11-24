"""Every child process must be started through the Windows no-window flags."""
import ast
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "relate_codemem"
STARTERS = {"run", "call", "Popen", "check_output", "check_call"}


def test_every_child_process_hides_its_console_window():
    missing = []
    for path in sorted(SRC.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name) and node.func.value.id == "subprocess"
                    and node.func.attr in STARTERS):
                continue
            # the flags arrive as **config.NO_WINDOW or **kwargs
            if not any(kw.arg is None for kw in node.keywords):
                missing.append(f"{path.name}:{node.lineno}")
    assert not missing, f"subprocess calls without the no-window flags: {missing}"
