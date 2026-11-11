"""Lecture de sections Markdown et contrôle de titres obligatoires (partagés par les contrôles de specs et d'architecture)."""
import re
from typing import List, Optional


def missing_heading_issues(raw: str, headings) -> List[str]:
    return [
        f"Section « {heading} » absente."
        for heading in headings
        if not re.search(rf"^#+\s*.*{re.escape(heading)}", raw, re.IGNORECASE | re.MULTILINE)
    ]


def markdown_section(raw: str, keyword: str) -> Optional[str]:
    """Corps de la première section dont le titre contient `keyword`, jusqu'au titre suivant."""
    lines = raw.splitlines()
    for index, line in enumerate(lines):
        if re.match(r"^#+\s", line) and keyword.lower() in line.lower():
            body = []
            for following in lines[index + 1:]:
                if re.match(r"^#+\s", following):
                    break
                body.append(following)
            return "\n".join(body)
    return None
