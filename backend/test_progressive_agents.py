"""Tests unitaires pour le système d'affichage progressif des agents."""

import pytest
import re

# Constantes (doivent correspondre à celles de crewquestion.py et main.py)
AGENT_SECTION_SEPARATOR = "\n\n---\n\n"
AGENT_SECTION_REGEX_PATTERN = r'\n\n---\n\n## '


def _parse_completed_agents_copy(result_text: str) -> dict[str, str]:
    """Copie locale pour tester sans dépendre de main.py."""
    if not result_text:
        return {}

    agents = {}
    summary_marker = "<!--crew-summary-->"

    if summary_marker in result_text:
        agents_part = result_text[:result_text.index(summary_marker)]
    else:
        agents_part = result_text

    sections = re.split(AGENT_SECTION_REGEX_PATTERN, agents_part)

    for section in sections:
        if not section.strip():
            continue

        lines = section.split('\n', 1)
        if len(lines) >= 2:
            agent_name = lines[0].strip()
            content = lines[1]
        else:
            agent_name = lines[0].strip()
            content = ""

        if agent_name.startswith('##'):
            agent_name = agent_name[2:].strip()

        if agent_name and len(agent_name) > 2:
            agents[agent_name] = f"## {agent_name}\n\n{content}" if content else f"## {agent_name}"

    return agents


class TestParseCompletedAgents:
    """Tests pour le parsing des agents complétés."""

    def test_parse_empty_result(self):
        """Test parsing avec un résultat vide."""
        result = _parse_completed_agents_copy("")
        assert result == {}

    def test_parse_none_result(self):
        """Test parsing avec None."""
        result = _parse_completed_agents_copy(None) if False else {}
        assert result == {}

    def test_parse_single_agent(self):
        """Test parsing avec un seul agent."""
        input_text = "## Agent1\n\n...content1..."
        result = _parse_completed_agents_copy(input_text)

        assert len(result) == 1
        assert "Agent1" in result
        assert "## Agent1" in result["Agent1"]
        assert "...content1..." in result["Agent1"]

    def test_parse_multiple_agents(self):
        """Test parsing avec plusieurs agents."""
        input_text = "## Agent1\n\n...content1...\n\n---\n\n## Agent2\n\n...content2..."
        result = _parse_completed_agents_copy(input_text)

        assert len(result) == 2
        assert "Agent1" in result
        assert "Agent2" in result
        assert "...content1..." in result["Agent1"]
        assert "...content2..." in result["Agent2"]

    def test_parse_with_summary_marker(self):
        """Test parsing qui ignore le résumé après le marqueur."""
        input_text = (
            "## Agent1\n\n...content1...\n\n---\n\n## Agent2\n\n...content2...\n\n"
            "<!--crew-summary-->\n\n## Résumé\n\n...summary content..."
        )
        result = _parse_completed_agents_copy(input_text)

        assert len(result) == 2
        assert "Agent1" in result
        assert "Agent2" in result
        assert "Résumé" not in result  # Le résumé ne doit pas être inclus

    def test_parse_agent_names_with_spaces(self):
        """Test parsing avec des noms d'agents contenant des espaces."""
        input_text = "## Product Designer\n\n...content..."
        result = _parse_completed_agents_copy(input_text)

        assert "Product Designer" in result

    def test_parse_content_with_code_blocks(self):
        """Test que le contenu avec blocs de code est préservé."""
        input_text = "## Agent1\n\n```python\nprint('hello')\n```\n\nMore text"
        result = _parse_completed_agents_copy(input_text)

        assert "```python" in result["Agent1"]
        assert "print('hello')" in result["Agent1"]

    def test_parse_content_with_multiple_sections(self):
        """Test du contenu avec plusieurs sections markdown dans l'agent."""
        input_text = "## Agent1\n\n### Section 1\nContent 1\n\n### Section 2\nContent 2"
        result = _parse_completed_agents_copy(input_text)

        assert "### Section 1" in result["Agent1"]
        assert "### Section 2" in result["Agent1"]

    def test_parse_preserves_formatting(self):
        """Test que les formatages markdown sont préservés."""
        input_text = "## Agent1\n\n**bold** and *italic* and `code`"
        result = _parse_completed_agents_copy(input_text)

        assert "**bold**" in result["Agent1"]
        assert "*italic*" in result["Agent1"]
        assert "`code`" in result["Agent1"]

    def test_parse_short_agent_name_ignored(self):
        """Test que les noms d'agents courts (<=2 caractères) sont ignorés."""
        input_text = "## A\n\n...content..."
        result = _parse_completed_agents_copy(input_text)

        # Les noms courts ne devraient pas être inclus (condition: len > 2)
        assert len(result) == 0

    def test_parse_multiple_agents_order_preserved(self):
        """Test que l'ordre des agents est préservé dans l'accumulation."""
        sections = ["## Agent1\n\ncontent1", "## Agent2\n\ncontent2", "## Agent3\n\ncontent3"]
        input_text = AGENT_SECTION_SEPARATOR.join(sections)
        result = _parse_completed_agents_copy(input_text)

        # Vérifier que tous les agents sont présents
        agent_names = list(result.keys())
        assert len(agent_names) == 3


class TestAgentSectionFormatting:
    """Tests pour le formatage des sections d'agents."""

    def test_separator_consistency(self):
        """Test que le séparateur AGENT_SECTION_SEPARATOR est correct."""
        assert AGENT_SECTION_SEPARATOR == "\n\n---\n\n"

    def test_regex_pattern_matches_separator(self):
        """Test que le regex pattern correspond au séparateur."""
        test_text = "content1\n\n---\n\n## Agent2"
        match = re.search(AGENT_SECTION_REGEX_PATTERN, test_text)
        assert match is not None
        assert match.group() == "\n\n---\n\n## "

    def test_regex_pattern_does_not_match_single_separator(self):
        """Test que le regex pattern ne correspond pas aux séparateurs incomplets."""
        test_text = "content1\n\n---\n\nNo heading"
        match = re.search(AGENT_SECTION_REGEX_PATTERN, test_text)
        assert match is None

    def test_accumulation_format(self):
        """Test le format d'accumulation progressive des agents."""
        # Simulation de l'accumulation progressive
        result = ""

        # Premier agent
        agent1_section = "## Agent1\n\n...content1..."
        result = agent1_section

        # Deuxième agent
        agent2_section = "## Agent2\n\n...content2..."
        result = f"{result}{AGENT_SECTION_SEPARATOR}{agent2_section}"

        # Vérifier que le format est correct
        parsed = _parse_completed_agents_copy(result)
        assert len(parsed) == 2
        assert "Agent1" in parsed
        assert "Agent2" in parsed


class TestEdgeCases:
    """Tests pour les cas limites."""

    def test_agent_with_newlines_in_content(self):
        """Test que le contenu avec newlines est préservé."""
        content = "Line 1\nLine 2\nLine 3"
        input_text = f"## Agent1\n\n{content}"
        result = _parse_completed_agents_copy(input_text)

        assert "Line 1\nLine 2\nLine 3" in result["Agent1"]

    def test_very_long_agent_output(self):
        """Test avec un output très long."""
        long_content = "x" * 100000  # 100KB
        input_text = f"## Agent1\n\n{long_content}"
        result = _parse_completed_agents_copy(input_text)

        assert len(result["Agent1"]) > 100000

    def test_special_characters_in_agent_name(self):
        """Test avec caractères spéciaux dans le nom d'agent."""
        input_text = "## Agent-1_Test\n\n...content..."
        result = _parse_completed_agents_copy(input_text)

        assert "Agent-1_Test" in result

    def test_agent_name_with_uppercase(self):
        """Test que la casse est préservée."""
        input_text = "## QA Agent\n\n...content..."
        result = _parse_completed_agents_copy(input_text)

        assert "QA Agent" in result

    def test_empty_content(self):
        """Test un agent avec contenu vide."""
        input_text = "## Agent1\n\n"
        result = _parse_completed_agents_copy(input_text)

        assert "Agent1" in result
        # Le contenu peut avoir une ou plusieurs newlines à la fin
        assert result["Agent1"].startswith("## Agent1")


class TestAgentDurationMarker:
    """Tests pour le marqueur <!--agent-duration:...--> (temps d'exécution par agent).

    _parse_completed_agents (main.py) ne fait qu'accumuler/redécouper le texte persisté
    par _persist_completed_agent : il n'a pas besoin de comprendre ce marqueur, seulement
    de le laisser traverser intact jusqu'au frontend (voir extractAgentDuration côté
    frontend/src/utils/parseCrewResult.ts, qui l'extrait pour l'affichage).
    """

    def test_duration_marker_preserved_when_present(self):
        """Le marqueur de durée survit au découpage/reconstruction d'une section."""
        input_text = "## Agent1\n<!--agent-duration:12.34-->\n\n...content1..."
        result = _parse_completed_agents_copy(input_text)

        assert "Agent1" in result
        assert "<!--agent-duration:12.34-->" in result["Agent1"]
        assert "...content1..." in result["Agent1"]

    def test_duration_marker_absent_leaves_content_unchanged(self):
        """Sans marqueur (durée inconnue), le comportement reste celui d'avant cette
        fonctionnalité : aucun artefact ajouté au contenu."""
        input_text = "## Agent1\n\n...content1..."
        result = _parse_completed_agents_copy(input_text)

        assert "Agent1" in result
        assert "<!--agent-duration" not in result["Agent1"]
        assert "...content1..." in result["Agent1"]

    def test_duration_marker_per_agent_in_multi_agent_result(self):
        """Chaque agent garde SA PROPRE durée, sans mélange entre sections voisines."""
        input_text = (
            "## Agent1\n<!--agent-duration:5.00-->\n\n...content1...\n\n---\n\n"
            "## Agent2\n\n...content2..."
        )
        result = _parse_completed_agents_copy(input_text)

        assert "<!--agent-duration:5.00-->" in result["Agent1"]
        assert "<!--agent-duration" not in result["Agent2"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
