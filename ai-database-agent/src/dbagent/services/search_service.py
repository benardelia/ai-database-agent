import re

from dbagent.business.glossary_service import BusinessTermService
from dbagent.metadata.models import TableMetadata, TableSearchResult
from dbagent.services.schema_service import DatabaseSchemaService

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


class SchemaSearchService:
    """Finds tables relevant to a business concept without sending the
    whole schema to an LLM. Considers table names, column names, table
    comments, and business-glossary aliases. Simple keyword scoring for
    now; can be swapped for full-text search or embeddings later without
    changing the interface.
    """

    def __init__(
        self,
        schema_service: DatabaseSchemaService,
        glossary_service: BusinessTermService | None = None,
    ):
        self._schema_service = schema_service
        self._glossary_service = glossary_service or BusinessTermService()

    def search_tables(self, query: str, limit: int = 20) -> list[TableSearchResult]:
        search_terms = self._glossary_service.expand(query)
        search_terms.extend(_tokenize(query))
        search_terms = {t for t in search_terms if t}

        schema = self._schema_service.get_schema()
        results: list[TableSearchResult] = []

        for table in schema.tables:
            score, reasons = self._score_table(table, search_terms)
            if score > 0:
                results.append(
                    TableSearchResult(
                        table=table.name,
                        schema_name=table.schema_name,
                        score=score,
                        matched_reasons=reasons,
                    )
                )

        results.sort(key=lambda r: r.score, reverse=True)
        return results[:limit]

    def _score_table(
        self, table: TableMetadata, search_terms: set[str]
    ) -> tuple[int, list[str]]:
        score = 0
        reasons: list[str] = []
        table_name = table.name.lower()
        description = (table.description or "").lower()
        column_names = {c.name.lower() for c in table.columns}

        for term in search_terms:
            if term == table_name:
                score += 10
                reasons.append(f"table name matches '{term}'")
            elif term in table_name:
                score += 5
                reasons.append(f"table name contains '{term}'")

            if term in column_names:
                score += 3
                reasons.append(f"has column '{term}'")
            else:
                matching_cols = [c for c in column_names if term in c]
                if matching_cols:
                    score += 2
                    reasons.append(f"column matches '{term}' ({matching_cols[0]})")

            if description and term in description:
                score += 4
                reasons.append(f"description mentions '{term}'")

        return score, reasons
