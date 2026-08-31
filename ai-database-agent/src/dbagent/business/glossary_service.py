import json
from pathlib import Path

DEFAULT_GLOSSARY_PATH = Path(__file__).with_name("glossary.json")


class BusinessTermService:
    """Maps user/business terminology to database terminology.
    

    Database naming does not always match the words a user would use in a
    question (e.g. "owner" -> right_holder). This is a thin, file-backed
    lookup so the mapping can be edited or grown into a real table
    (business_term / business_term_alias) later without changing callers.
    """

    def __init__(self, glossary_path: Path | str = DEFAULT_GLOSSARY_PATH):
        self._glossary_path = Path(glossary_path)
        self._terms: dict[str, list[str]] = self._load()

    def _load(self) -> dict[str, list[str]]:
        if not self._glossary_path.exists():
            return {}
        with open(self._glossary_path) as f:
            return json.load(f)

    def all_terms(self) -> dict[str, list[str]]:
        return dict(self._terms)

    def aliases_for(self, term: str) -> list[str]:
        return list(self._terms.get(term.lower().strip(), []))

    def expand(self, query: str) -> list[str]:
        """Return the query terms plus any known database aliases for them."""
        query_lower = query.lower().strip()
        expanded = {query_lower}

        if query_lower in self._terms:
            expanded.update(self._terms[query_lower])

        for term, aliases in self._terms.items():
            if term in query_lower or query_lower in term:
                expanded.add(term)
                expanded.update(aliases)

        return list(expanded)
