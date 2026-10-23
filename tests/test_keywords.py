from pathlib import Path

from novel_ref.keywords import KeywordRegistry


def test_normalize_alias(tmp_path: Path):
    p = tmp_path / "keywords.yaml"
    p.write_text(
        "keywords:\n  - 温情\n  - 武侠\naliases:\n  治愈: 温情\n  打斗: 武侠\n",
        encoding="utf-8",
    )
    reg = KeywordRegistry(p)
    assert reg.normalize("治愈") == "温情"
    assert reg.normalize("未知") is None
    assert "温情" in reg.list_tags()
