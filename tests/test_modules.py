"""Prüft den Aufbau des Pakets gbui: Jedes Modul lässt sich für sich importieren (also keine Importzyklen), keines
importiert das Hauptskript, und auf Modulebene steht kein Streamlit-Aufruf. Module laufen nur beim ersten Import, das
Hauptskript dagegen bei jedem Rerun."""
import ast
import importlib
import pkgutil

import pytest

from helpers import PACKAGE, PACKAGE_DIR

MODULES = sorted(m.name for m in pkgutil.iter_modules([str(PACKAGE_DIR)]))


def parse(name):
    return ast.parse((PACKAGE_DIR / f"{name}.py").read_text("utf-8"))


@pytest.mark.parametrize("name", MODULES)
def test_every_module_imports_on_its_own(name):
    """Die Fixture fresh_package leert den Importspeicher: Ein Zyklus oder ein fehlender Import fällt hier auf."""
    module = importlib.import_module(f"{PACKAGE}.{name}")
    assert module.__name__ == f"{PACKAGE}.{name}"


def test_modules_never_import_the_main_script():
    offenders = []
    for name in MODULES:
        for node in ast.walk(parse(name)):
            if isinstance(node, ast.Import):
                imported = {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imported = {node.module.split(".")[0]}
            else:
                continue
            if "gemini_batch_ui" in imported:
                offenders.append(f"{name}.py Zeile {node.lineno}")
    assert not offenders, f"Die Logik darf das Hauptskript nicht importieren: {offenders}"


def runs_on_import(node):
    """Die Teile einer Anweisung, die beim Import laufen. Bei einer Funktion nur die Vorgabewerte: Dekoratoren wie
    @st.cache_resource sind erlaubt, der Rumpf läuft erst beim Aufruf."""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return [*node.args.defaults, *[d for d in node.args.kw_defaults if d is not None]]
    return [node]


def test_no_streamlit_calls_on_module_level():
    offenders = []
    for name in MODULES:
        for statement in parse(name).body:
            for part in runs_on_import(statement):
                if any(isinstance(n, ast.Name) and n.id == "st" for n in ast.walk(part)):
                    offenders.append(f"{name}.py Zeile {statement.lineno}")
    assert not offenders, f"Auf Modulebene darf kein Streamlit-Aufruf laufen (Module laufen nur einmal): {offenders}"
