from novel_agent.core.session_store import SessionStore
from novel_agent.settings import Settings


def test_session_isolation(tmp_path, monkeypatch):
    monkeypatch.setenv("NOVELAGENT_HOME", str(tmp_path / "home"))
    # bypass lru_cache
    from novel_agent import settings as settings_mod

    settings_mod.get_settings.cache_clear()
    s = Settings(novelagent_home=tmp_path / "home")
    store = SessionStore(s)
    a = store.create("小说A")
    b = store.create("小说B")
    a.novel_outline = "世界观A"
    store.save(a)
    b2 = store.load(b.session_id)
    assert b2 is not None
    assert b2.novel_outline == ""
    a2 = store.load(a.session_id)
    assert a2 is not None
    assert a2.novel_outline == "世界观A"
