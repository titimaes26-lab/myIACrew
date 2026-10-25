"""Cherche les expressions régulières du backend à temps SUPER-LINÉAIRE (voir test_regex_linear_time.py). Lancé dans un interpréteur NEUF :
un enfant forké par motif, tué s'il reste trop longtemps sur une entrée (`re` garde le GIL : un motif quadratique ne peut pas être interrompu
de l'intérieur). Sortie : JSON des motifs lents ou bloqués."""
import ast
import glob
import importlib
import json
import os
import re
import select
import signal
import sys
import time

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SIZE = 20_000        # un motif quadratique y dépasse 0,5 s (mesuré : 30 000 retours à la ligne = 14 s pour l'ancien ANY_FENCE)
SLOW_SECONDS = 0.5   # un motif linéaire y prend quelques millisecondes (marge x100 pour une machine d'intégration lente)
KILL_SECONDS = 8.0
# Motifs appelés UNIQUEMENT avec `.match(texte, position)` (un seul point de départ, donc linéaires en pratique) : la lecture « à toutes les positions »
# de ce scanner les surestime (et les ferait durer 15 s). Chaque entrée dit où ils sont appelés ; test_regex_linear_time vérifie qu'ils le restent.
MATCH_ONLY = {
    r"\s*[:—–-]\s*": "crew_guardrails._ADJACENT_PATH_SEPARATOR : .match(text, pos)",
    r"\s*,\s*": "crew_guardrails._LIST_SEPARATOR : .match(text, pos)",
    r"\s*(?:sudo\s+)?(?:ba|z|da|k)?sh\b": "delivery_guards._SHELL_AT_START : .match(segment)",
    r"\s*Demande initiale\s*:": "delivery._REQUEST_HEADER_AT_START : .match(text)",
}
FILLERS = [" ", "\n", "a", "-", ":"]


def generic_inputs(size: int) -> dict[str, str]:
    return {
        "a*N": "a" * size, "mots 'a '": "a " * (size // 2), "espaces": " " * size, "retours ligne": "\n" * size,
        "ab.cd-ef_gh": "ab.cd-ef_gh" * (size // 11), "<<<": "<<<" * (size // 3), "=======": "=======\n" * (size // 8), "```": "```" * (size // 3),
        "-----": "-----" * (size // 5), "import ": "import " * (size // 7), "// ": "// " * (size // 3), "tab+!": "x" + "\t" * size + "!",
        "a*25+!": ("a" * 25 + "!") * (size // 26), "(((": "(" * size, "guillemets": '"' * size, "lignes de texte": "une ligne de texte\n" * (size // 19),
        "puces": "- " * (size // 2), "pipes": "|" * size, "virgules": "," * size, "chiffres": "1234567890" * (size // 10),
        "urls": "https://x.example/a?b=c&d=e " * (size // 27), "#": "#" * size, "*": "*" * size, "`": "`" * size,
    }


def derived_inputs(pattern: re.Pattern, size: int) -> dict[str, str]:
    """Entrées bâties sur les MOTS LITTÉRAUX du motif (un motif qui exige « Demande initiale » ne se déclenche pas sur du texte quelconque)."""
    words: list[str] = []
    for word in re.findall(r"[A-Za-zÀ-ÿ_']{3,}", pattern.pattern):
        if word not in words:
            words.append(word)
    inputs = {}
    for word in words[:5]:
        for filler in FILLERS:
            inputs[f"{word}+{filler!r}*N"] = word + filler * size
            inputs[f"({word}{filler!r})*N"] = (word + filler) * (size // (len(word) + 1))
            inputs[f"{filler!r}*N+{word}"] = filler * size + word            # longue suite AVANT le mot (motifs ancrés sur `^\\s*`)
        inputs[f"({word}\\n  )*N"] = (word + "\n  ") * (size // (len(word) + 3))
    return inputs


def _collect(value, label: str, found: dict[tuple[str, int], str], depth: int = 0) -> None:
    if isinstance(value, re.Pattern):
        found.setdefault((value.pattern, value.flags), label)
    elif depth < 3 and isinstance(value, (list, tuple, set, frozenset)):
        for item in value:
            _collect(item, label, found, depth + 1)
    elif depth < 3 and isinstance(value, dict):
        for item in value.values():
            _collect(item, label, found, depth + 1)


def _is_direct_re_call(node: ast.AST) -> bool:
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "re" and node.func.attr in ("search", "match", "fullmatch", "sub", "findall", "finditer", "split")
            and bool(node.args) and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str))


def _flags_of(call: ast.Call) -> int:
    for keyword in call.keywords:
        if keyword.arg == "flags":
            try:
                return eval(ast.unparse(keyword.value), {"re": re})  # noqa: S307  (constantes re.X de notre propre code)
            except Exception:  # noqa: BLE001
                return 0
    return 0


def backend_patterns() -> dict[tuple[str, int], str]:
    sys.path.insert(0, BACKEND)
    os.chdir(BACKEND)
    found: dict[tuple[str, int], str] = {}
    for path in sorted(glob.glob(BACKEND + "/*.py")):
        name = os.path.basename(path)[:-3]
        if name.startswith("test_") or name == "conftest":
            continue
        for attr, value in vars(importlib.import_module(name)).items():
            _collect(value, f"{name}.{attr}", found)
        for node in ast.walk(ast.parse(open(path, encoding="utf-8").read())):          # motifs écrits directement dans un appel re.*(…)
            if _is_direct_re_call(node):
                found.setdefault((node.args[0].value, _flags_of(node)), f"{name}:{node.lineno} (appel direct)")
    return found


def run_pattern(compiled: re.Pattern) -> tuple[list[tuple[str, float]], str | None]:
    read_fd, write_fd = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(read_fd)
        for label, text in {**generic_inputs(SIZE), **derived_inputs(compiled, SIZE)}.items():
            os.write(write_fd, f"S\t{label}\n".encode())
            started = time.perf_counter()
            compiled.sub("", text)
            os.write(write_fd, f"D\t{label}\t{time.perf_counter() - started:.3f}\n".encode())
        os._exit(0)
    os.close(write_fd)
    slow, hung, current, buffer = [], None, None, b""
    while True:
        ready, _, _ = select.select([read_fd], [], [], KILL_SECONDS)
        if not ready:
            hung = current
            os.kill(pid, signal.SIGKILL)
            break
        chunk = os.read(read_fd, 4096)
        if not chunk:
            break
        buffer += chunk
        while b"\n" in buffer:
            line, buffer = buffer.split(b"\n", 1)
            parts = line.decode().split("\t")
            if parts[0] == "S":
                current = parts[1]
            elif parts[0] == "D" and float(parts[2]) >= SLOW_SECONDS:
                slow.append((parts[1], float(parts[2])))
    os.waitpid(pid, 0)
    os.close(read_fd)
    return slow, hung


def main() -> None:
    report = []
    patterns = backend_patterns()
    for (pattern, flags), label in sorted(patterns.items(), key=lambda item: item[1]):
        if pattern in MATCH_ONLY:
            continue
        try:
            compiled = re.compile(pattern, flags)
        except re.error:
            continue
        slow, hung = run_pattern(compiled)
        if slow or hung:
            report.append({"label": label, "pattern": pattern, "slow": slow, "hung_on": hung})
    print(json.dumps({"count": len(patterns), "offenders": report}))


if __name__ == "__main__":
    main()
