from pydantic import BaseModel


class ColumnMetadata(BaseModel):
    name: str
    data_type: str
    nullable: bool
    primary_key: bool = False
    foreign_key: bool = False
    default: str | None = None
    comment: str | None = None


class IndexMetadata(BaseModel):
    name: str
    columns: list[str]
    unique: bool = False
    primary: bool = False


class RelationshipMetadata(BaseModel):
    source_table: str
    source_column: str
    target_table: str
    target_column: str
    relationship_type: str = "FOREIGN_KEY"
    constraint_name: str | None = None


class TableMetadata(BaseModel):
    schema_name: str
    name: str
    table_type: str = "table"
    description: str | None = None
    columns: list[ColumnMetadata] = []
    indexes: list[IndexMetadata] = []
    relationships: list[RelationshipMetadata] = []
    estimated_row_count: int | None = None

    @property
    def qualified_name(self) -> str:
        return f"{self.schema_name}.{self.name}"


class TableSearchResult(BaseModel):
    table: str
    schema_name: str
    score: int
    matched_reasons: list[str] = []


class DatabaseSchema(BaseModel):
    database_name: str
    tables: list[TableMetadata] = []

    def find_table(self, name: str) -> TableMetadata | None:
        for table in self.tables:
            if table.name == name or table.qualified_name == name:
                return table
        return None

    def all_relationships(self) -> list[RelationshipMetadata]:
        relationships: list[RelationshipMetadata] = []
        for table in self.tables:
            relationships.extend(table.relationships)
        return relationships
