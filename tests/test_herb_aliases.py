import unicodedata

import pytest

from herbfold.herb_aliases import HERB_ALIASES, list_herb_aliases, resolve_herb_query


def test_exact_korean_queries_resolve_to_cited_taxa_only():
    result = resolve_herb_query("강황")
    assert result["query"] == "Curcuma longa"
    assert result["taxa"] == ["Curcuma longa"]
    assert result["mapped"] is True
    assert result["scope"] == "reported_taxon"
    assert result["mapping_source_url"].startswith("https://")
    assert "unspecified" in result["license_label"]
    assert resolve_herb_query(" 갈근 ")["query"] == "Pueraria lobata"
    assert resolve_herb_query(unicodedata.normalize("NFD", "갈근"))["query"] == "Pueraria lobata"


def test_mixed_search_unknown_and_latin_are_preserved():
    for query in ("갈근 성분", "갈근 OR 감초", "Pueraria lobata", "", "없는약재", "../../../"):
        result = resolve_herb_query(query)
        assert result["query"] == query
        assert result["original_query"] == query
        assert result["mapped"] is False
        assert result["mapping_source_url"] is None
    with pytest.raises(ValueError):
        resolve_herb_query(None)


def test_public_index_facts_retain_scope_and_do_not_expose_mutable_state():
    aliases = list_herb_aliases()
    assert len(aliases) >= 60
    assert len({row["name"] for row in aliases}) == len(aliases)
    assert all(row["scope"] in {"reported_taxon", "broader_genus", "ambiguous_taxa"} for row in aliases)
    assert all(row["mapping_source_url"].startswith("https://") for row in aliases)
    assert all("efficacy" not in row and "indication" not in row for row in aliases)
    row = resolve_herb_query("강황")
    row["taxa"].append("Fake species")
    assert "Fake species" not in HERB_ALIASES["강황"]["taxa"]
    assert "백반" not in HERB_ALIASES  # Mineral, no reported biological taxon.
    assert "육미지황탕" not in HERB_ALIASES  # Prescription is not one taxon.
    assert "녹두" not in HERB_ALIASES  # Source typo is not silently corrected.


def test_multiple_reported_origins_broaden_explicitly_without_discarding_taxa():
    licorice = resolve_herb_query("감초")
    assert licorice["query"] == "Glycyrrhiza"
    assert licorice["scope"] == "broader_genus"
    assert set(licorice["taxa"]) == {"Glycyrrhiza uralensis", "Glycyrrhiza glabra", "Glycyrrhiza inflata"}
    assert "monoDetailView_M01.jsp?idx=7" in licorice["mapping_source_url"]
    coptis = resolve_herb_query("황련")
    assert coptis["query"] == "Coptis"
    assert coptis["scope"] == "broader_genus"
    assert set(coptis["taxa"]) == {"Coptis japonica", "Coptis chinensis"}
    quince = resolve_herb_query("모과")
    assert quince["query"] == "Chaenomeles"
    assert set(quince["taxa"]) == {"Chaenomeles speciosa", "Chaenomeles sinensis"}
    assert len(HERB_ALIASES) >= 500
    assert resolve_herb_query("황금")["query"] == "Scutellaria baicalensis"
    assert resolve_herb_query("인삼")["query"] == "Panax ginseng"
