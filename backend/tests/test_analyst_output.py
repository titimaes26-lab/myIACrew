import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analyst_output import (  # noqa: E402
    build_delivery_report,
    find_placeholders,
    parse_file_blocks,
    review_diagnostic_output,
)

SAMPLE = """## Plan
- src/App.tsx : corrige le total

### Fichier : src/App.tsx
```tsx
export default function App() {
  const items = [...base];
  return <p>n'y touche pas</p>;
}
```

### Fichier : `docs/README.md`
````markdown
# Doc
```bash
npm run dev
```
````
"""


def test_parse_file_blocks_extracts_paths_and_full_content():
    files = parse_file_blocks(SAMPLE)
    assert [f["path"] for f in files] == ["src/App.tsx", "docs/README.md"]
    assert "const items = [...base];" in files[0]["content"]
    # Le bloc ``` interne au Markdown ne coupe pas le fichier encadré par ````.
    assert files[1]["content"] == "# Doc\n```bash\nnpm run dev\n```\n"


def test_parse_ignores_unclosed_block_and_keeps_last_duplicate():
    text = (
        "### Fichier : a.py\n```python\nx = 1\n```\n"
        "### Fichier : a.py\n```python\nx = 2\n```\n"
        "### Fichier : b.py\n```python\ny = 1\n"
    )
    files = parse_file_blocks(text)
    assert files == [{"path": "a.py", "content": "x = 2\n"}]


def test_spread_operator_is_not_a_placeholder():
    assert find_placeholders(parse_file_blocks(SAMPLE)) == []


def test_placeholder_comments_are_detected():
    text = "### Fichier : src/a.ts\n```ts\nconst a = 1;\n// ... reste du code inchangé\n```\n"
    files, issue, faulty = review_diagnostic_output(text)
    assert len(files) == 1
    assert issue and "src/a.ts ligne 2" in issue
    assert faulty == {"src/a.ts"}


def test_output_without_files_is_rejected_unless_declared_not_delivered():
    assert review_diagnostic_output("Voici mon analyse sans code.")[1] is not None
    assert review_diagnostic_output("- big.tsx : NON réalisé (trop volumineux)")[1] is None
    # Titre de fichier visé mais mal formé : le marqueur "non réalisé" ne suffit plus.
    malformed = "Fichiers NON réalisés : aucun\n**Fichier : src/App.tsx**\nexport {}\n"
    assert review_diagnostic_output(malformed)[1] is not None


def test_unclosed_block_is_reported_not_committed():
    text = "### Fichier : a.py\n```python\nx = 1\n```\n### Fichier : src/store.ts\n```ts\nexport const a ="
    files, issue, _ = review_diagnostic_output(text)
    assert [f["path"] for f in files] == ["a.py"]
    assert issue and "src/store.ts" in issue and "jamais fermé" in issue


def test_nested_fence_inside_triple_backtick_markdown_is_kept():
    text = "### Fichier : README.md\n```markdown\n# Titre\n```bash\nnpm run dev\n```\nFin\n```\n"
    assert parse_file_blocks(text)[0]["content"] == "# Titre\n```bash\nnpm run dev\n```\nFin\n"


def test_hash_lines_are_only_comments_where_relevant():
    files = [
        {"path": "README.md", "content": "## Intégration au code existant\n"},
        {"path": "app.py", "content": "# ... reste du code\n"},
        {"path": "style.css", "content": "#reste-du-code { color: red; }\n"},
    ]
    assert [p for p, _, _ in find_placeholders(files)] == ["app.py"]


def test_heading_paths_are_normalized():
    text = (
        "### Fichier : `./src/App.tsx` (modifié)\n```tsx\na\n```\n"
        "**Fichier : /src/b.ts**\n```ts\nb\n```\n"
    )
    assert [f["path"] for f in parse_file_blocks(text)] == ["src/App.tsx", "src/b.ts"]


def test_delivery_report_flags_absent_divergent_and_identical():
    files = [
        {"path": "same.json", "content": '{"a": 1}\n'},
        {"path": "diff.py", "content": "x = 1\n"},
        {"path": "missing.ts", "content": "export {};\n"},
    ]
    remote = {"same.json": '{"a": 1}', "diff.py": "x = 2\n"}

    report = build_delivery_report(
        files, lambda p: (remote[p], None) if p in remote else (None, "introuvable")
    )
    assert "### same.json" in report and "IDENTIQUE" in report
    assert "DIVERGENT" in report and "+x = 2" in report
    assert "ABSENT" in report
