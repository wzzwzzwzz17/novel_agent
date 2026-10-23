from novel_agent.core.memory import recall_events
from novel_agent.core.models import EventMemory, MemoryBank, NovelSession


def test_recall_high_always_and_keyword_match():
    session = NovelSession(
        memory=MemoryBank(
            event_memory=[
                EventMemory(content="主角与反派在雨夜决裂", weight="high"),
                EventMemory(content="厨房里煮了一锅汤", weight="low"),
                EventMemory(content="雨夜刀光对峙于城墙", weight="medium"),
            ]
        )
    )
    recalled = recall_events(session, "继续写雨夜对峙")
    texts = [e.content for e in recalled]
    assert any("决裂" in t for t in texts)
    assert any("城墙" in t for t in texts)
