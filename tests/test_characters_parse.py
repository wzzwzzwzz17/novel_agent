from novel_agent.core.characters import _parse_json_object


def test_parse_json_from_fence():
    raw = """```json
{"optimized": "冷静刺客", "personality": "克制", "appearance": "黑发"}
```"""
    data = _parse_json_object(raw)
    assert data["optimized"] == "冷静刺客"
    assert data["personality"] == "克制"
