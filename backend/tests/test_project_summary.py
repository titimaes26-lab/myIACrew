import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from project_summary import MAX_LISTED_DEPENDENCIES, summarize_project  # noqa: E402

PACKAGE = json.dumps({
    "name": "boutique", "scripts": {"dev": "vite", "build": "tsc -b", "test": "vitest"},
    "dependencies": {"react": "^19.0.0", "zustand": "^5.0.0", "react-router-dom": "^7.0.0", "tailwindcss": "^4.0.0"},
    "devDependencies": {"vitest": "^3.0.0", "eslint": "^9.0.0", "typescript": "~5.8.0", "@testing-library/react": "^16.0.0"},
})


def test_summary_lists_dependencies_and_detects_the_conventions():
    text = summarize_project(PACKAGE, '{"compilerOptions": {"strict": true}}')
    assert "- Nom : boutique" in text and "- Scripts : build, dev, test" in text
    assert "react@^19.0.0" in text and "typescript@~5.8.0" in text
    assert "état : Zustand" in text and "routage : React Router" in text and "CSS : Tailwind" in text
    assert "tests : Testing Library, Vitest" in text and "qualité : ESLint" in text
    assert "mode strict activé" in text


def test_summary_is_much_shorter_than_the_raw_json_it_replaces():
    big = json.loads(PACKAGE)
    big["dependencies"].update({f"paquet-{i}": "^1.0.0" for i in range(200)})
    big["scripts"] = {f"script-{i}": "echo " + "x" * 80 for i in range(40)}
    raw = json.dumps(big, indent=2)
    text = summarize_project(raw)
    assert len(text) < len(raw) / 2
    assert f"(+{204 - MAX_LISTED_DEPENDENCIES})" in text   # plafonné, avec le nombre omis


def test_summary_handles_tsconfig_variants_and_missing_conventions():
    plain = json.dumps({"name": "x", "dependencies": {"left-pad": "1"}})
    assert "aucune bibliothèque" in summarize_project(plain)
    assert "mode strict non activé" in summarize_project(plain, '{"compilerOptions": {}}')
    assert "tsconfig.app.json" in summarize_project(plain, '{"files": [], "references": [{"path": "./tsconfig.app.json"}]}')
    assert "illisible" in summarize_project(plain, "pas du json")
    commented = '{\n  // commentaire\n  "compilerOptions": {"strict": true}\n}'
    assert "mode strict activé" in summarize_project(plain, commented)


def test_summary_is_none_for_an_unusable_package_json():
    assert summarize_project("{ pas du json") is None
    assert summarize_project("[1, 2]") is None


TSC_INIT = """{
  /* Visit https://aka.ms/tsconfig to read more about this file */
  "compilerOptions": {
    "target": "es2016", // langage de sortie
    "strict": true, /* tous les contrôles */
    "paths": {"@/*": ["./src/*"]},
    "note": "http://exemple.com/*x*/",
  },
}"""


def test_a_tsconfig_with_comments_and_trailing_commas_is_read_not_called_unreadable():
    text = summarize_project(PACKAGE, TSC_INIT)
    assert "mode strict activé" in text and "illisible" not in text


def test_comment_markers_inside_strings_are_kept_and_unterminated_comments_do_not_hang():
    from project_summary import _load_json
    assert _load_json(TSC_INIT)["compilerOptions"]["note"] == "http://exemple.com/*x*/"
    assert _load_json('{"a": 1 /* jamais fermé') is None
