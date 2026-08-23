from dbagent.business.glossary_service import BusinessTermService


def test_all_terms_loaded_from_default_glossary():
    service = BusinessTermService()
    terms = service.all_terms()
    assert "land parcel" in terms
    assert "land_parcel" in terms["land parcel"]


def test_aliases_for_known_term():
    service = BusinessTermService()
    assert "right_holder" in service.aliases_for("owner")


def test_aliases_for_unknown_term_is_empty():
    service = BusinessTermService()
    assert service.aliases_for("does not exist") == []


def test_expand_includes_original_query_and_aliases():
    service = BusinessTermService()
    expanded = service.expand("owner")
    assert "owner" in expanded
    assert "right_holder" in expanded
