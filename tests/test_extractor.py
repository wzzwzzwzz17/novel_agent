from novel_ref.extractor import best_keyword_span, clean_paragraphs, wide_chunks


def test_clean_filters_ads_and_keywords():
    runtime = {
        "ad_blacklist": ["请点击", "广告"],
        "ui_noise_b": ["下一章"],
    }
    paras = [
        "短",
        "请点击下载最新网址",
        "雨夜里两人对峙，刀光闪过。" * 2,
        "下一章",
        "阳光洒在街道上，行人匆匆走过广场。",
    ]
    out = clean_paragraphs(paras, ["雨", "对峙"], runtime, site_id="site_a")
    assert len(out) == 1
    assert "雨夜" in out[0]


def test_wide_then_best_span_keeps_keyword():
    runtime = {"ad_blacklist": [], "ui_noise_b": []}
    paras = [
        f"路人低声交谈，风吹过空荡的街道，第{i}步。"
        for i in range(40)
    ]
    paras[20] = "她在街灯下突然露出肩膀，雨水顺着锁骨往下。"
    wides = wide_chunks(paras, runtime, target_chars=400, limit=4)
    assert wides
    spans = [best_keyword_span(w, ["露出"], ["街灯"], target_chars=80) for w in wides]
    kept = [s for s in spans if s]
    assert kept
    assert all("露出" in s for s in kept)
