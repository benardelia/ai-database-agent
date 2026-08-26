import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dbagent.ai.provider import build_ollama_provider
from dbagent.config import settings
from dbagent.registry import DatabaseRegistry


def main() -> None:
    if len(sys.argv) < 3:
        print('Usage: python scripts/ask_agent.py <database_name> "<question>"')
        registry = DatabaseRegistry(settings.databases_config_path, build_ollama_provider())
        print(f"Configured databases: {registry.list_databases()}")
        raise SystemExit(1)

    database_name = sys.argv[1]
    question = " ".join(sys.argv[2:])

    registry = DatabaseRegistry(settings.databases_config_path, build_ollama_provider())
    bundle = registry.get(database_name)

    response = bundle.agent_service.ask(question)

    print(f"Database: {database_name}")
    print(f"Question: {response.question}\n")
    print("Steps:")
    for step in response.steps:
        print(f"  - {step.tool}({step.arguments}) -> {step.result_summary[:200]}")
    print(f"\nAnswer:\n{response.answer}")
    print(f"\nStopped reason: {response.stopped_reason}")


if __name__ == "__main__":
    main()
