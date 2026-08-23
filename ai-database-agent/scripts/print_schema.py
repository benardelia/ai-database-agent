import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dbagent.ai.provider import OllamaProvider
from dbagent.config import settings
from dbagent.registry import DatabaseRegistry


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python scripts/print_schema.py <database_name>")
        registry = DatabaseRegistry(
            settings.databases_config_path,
            OllamaProvider(host=settings.ollama_host, model=settings.ollama_model),
        )
        print(f"Configured databases: {registry.list_databases()}")
        raise SystemExit(1)

    database_name = sys.argv[1]
    provider = OllamaProvider(host=settings.ollama_host, model=settings.ollama_model)
    registry = DatabaseRegistry(settings.databases_config_path, provider)
    bundle = registry.get(database_name)
    schema = bundle.schema_service.get_schema()

    print(f"Database: {database_name} ({schema.database_name})\n")

    print("Tables:")
    for table in schema.tables:
        print(f"    {table.schema_name}.{table.name}")

    print("\nRelationships:")
    relationships = schema.all_relationships()
    if not relationships:
        print("    (none found)")
    for rel in relationships:
        print(
            f"    {rel.source_table}.{rel.source_column} -> "
            f"{rel.target_table}.{rel.target_column}"
        )


if __name__ == "__main__":
    main()
