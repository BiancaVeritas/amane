from amane.crawlers.sites.d2pass import _product_id


def test_d2pass_requires_heyzo_mapping(tmp_path) -> None:
    mapping_path = tmp_path / "d2pass.json"
    mapping_path.write_text('{"1234": {"id": 98765}}', encoding="utf-8")

    assert _product_id("HEYZO-1234", mapping_path) == 98765
    assert _product_id("HEYZO-9999", mapping_path) is None
    assert _product_id("103126-001", mapping_path) is None
    assert _product_id("1234", mapping_path) is None
