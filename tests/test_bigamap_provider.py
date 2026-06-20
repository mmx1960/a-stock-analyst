from app.core.providers.bigamap_provider import BigAmapProvider


def test_bigamap_repeated_top_hot_boards_from_payload():
    provider = BigAmapProvider(timeout=20, min_interval=0.1)
    payload = {
        "access_scope": "guest",
        "daily_rankings": [
            {"trade_date": "2026-01-03", "boards": [
                {"rank": 1, "board_code": "801001", "board_name": "芯片", "strength": 100},
                {"rank": 2, "board_code": "801002", "board_name": "机器人", "strength": 90},
            ]},
            {"trade_date": "2026-01-02", "boards": [
                {"rank": 1, "board_code": "801001", "board_name": "芯片", "strength": 80},
                {"rank": 2, "board_code": "801003", "board_name": "算力", "strength": 70},
            ]},
            {"trade_date": "2026-01-01", "boards": [
                {"rank": 1, "board_code": "801001", "board_name": "芯片", "strength": 60},
                {"rank": 2, "board_code": "801002", "board_name": "机器人", "strength": 50},
            ]},
        ],
        "rolling_rankings": {"top7": []},
    }
    boards = provider.get_repeated_top_hot_boards(payload, lookback_days=10, top_n=2, min_appearances=3)
    assert len(boards) == 1
    assert boards[0]["board_code"] == "801001"
    assert boards[0]["top_n_appearances"] == 3
    assert boards[0]["visible_days"] == 3


def test_bigamap_public_endpoints_smoke():
    provider = BigAmapProvider(timeout=20, min_interval=0.1)

    limit_up = provider.get_limit_up_review()
    assert "trade_date" in limit_up
    assert "limit_up" in limit_up
    assert isinstance(limit_up["limit_up"].get("items"), list)

    theme_stats = provider.extract_limit_up_theme_stats(limit_up)
    assert isinstance(theme_stats, list)
    if theme_stats:
        first = theme_stats[0]
        assert "theme" in first
        assert "limit_up_count" in first
        assert "stocks" in first

    rankings = provider.get_board_rankings()
    assert "latest_trade_date" in rankings
    assert "rolling_rankings" in rankings or "daily_rankings" in rankings

    concepts = provider.search_kaipanla_concepts("机器人")
    assert "items" in concepts
    assert isinstance(concepts["items"], list)
