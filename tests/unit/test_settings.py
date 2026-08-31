from __future__ import annotations

from datetime import time, timedelta

from fund_agent.config.settings import Settings


def test_settings_use_safe_defaults_for_local_service_and_schedule():
    settings = Settings.from_env({})

    assert settings.host == "127.0.0.1"
    assert settings.port == 8000
    assert settings.full_analysis_time == time(16, 30)
    assert settings.analysis_timezone == "Asia/Shanghai"
    assert settings.analysis_weekdays == (0, 1, 2, 3, 4)
    assert settings.evidence_interval == timedelta(hours=4)


def test_settings_read_database_url_crawler_and_risk_thresholds():
    settings = Settings.from_env(
        {
            "FUND_AGENT_HOST": "0.0.0.0",
            "FUND_AGENT_PORT": "9010",
            "FUND_AGENT_DATABASE_URL": "mysql+pymysql://user:secret@db/funds",
            "FUND_AGENT_CRAWLER_ENDPOINT": "https://crawler.test/evidence",
            "FUND_AGENT_CRAWLER_API_KEY": "crawler-secret",
            "FUND_AGENT_MARKET_ENDPOINT": "https://market.test",
            "FUND_AGENT_OFFICIAL_ENDPOINT": "https://official.test",
            "FUND_AGENT_NEWS_ENDPOINT": "https://news.test",
            "FUND_AGENT_SENTIMENT_ENDPOINT": "https://sentiment.test",
            "FUND_AGENT_FULL_ANALYSIS_TIME": "17:05",
            "FUND_AGENT_ANALYSIS_TIMEZONE": "Asia/Shanghai",
            "FUND_AGENT_ANALYSIS_WEEKDAYS": "mon,tue,wed,thu,fri",
            "FUND_AGENT_EVIDENCE_INTERVAL_HOURS": "6",
            "FUND_AGENT_NAV_DROP_THRESHOLD": "-0.08",
            "FUND_AGENT_DRAWDOWN_THRESHOLD": "-0.25",
            "FUND_AGENT_VOLATILITY_THRESHOLD": "0.4",
        }
    )

    assert settings.host == "0.0.0.0"
    assert settings.port == 9010
    assert settings.database_url == "mysql+pymysql://user:secret@db/funds"
    assert settings.crawler_endpoint == "https://crawler.test/evidence"
    assert settings.market_endpoint == "https://market.test"
    assert settings.official_endpoint == "https://official.test"
    assert settings.news_endpoint == "https://news.test"
    assert settings.sentiment_endpoint == "https://sentiment.test"
    assert settings.full_analysis_time == time(17, 5)
    assert settings.evidence_interval == timedelta(hours=6)
    assert settings.risk_thresholds.nav_drop_threshold == -0.08
    assert settings.risk_thresholds.drawdown_threshold == -0.25
    assert settings.risk_thresholds.volatility_threshold == 0.4


def test_settings_can_build_database_url_from_individual_mysql_fields():
    settings = Settings.from_env(
        {
            "FUND_AGENT_MYSQL_HOST": "mysql.internal",
            "FUND_AGENT_MYSQL_PORT": "3307",
            "FUND_AGENT_MYSQL_DATABASE": "funds",
            "FUND_AGENT_MYSQL_USER": "app",
            "FUND_AGENT_MYSQL_PASSWORD": "db-secret",
        }
    )

    assert settings.database_url is None
    assert settings.mysql_host == "mysql.internal"
    assert settings.mysql_port == 3307
    assert settings.mysql_database == "funds"
    assert settings.mysql_user == "app"
    assert settings.mysql_password == "db-secret"
    assert settings.mysql_connection_url == "mysql+pymysql://app:db-secret@mysql.internal:3307/funds"


def test_settings_safe_repr_and_model_dump_do_not_expose_secrets():
    settings = Settings.from_env(
        {
            "FUND_AGENT_DATABASE_URL": "mysql+pymysql://user:db-secret@db/funds",
            "FUND_AGENT_CRAWLER_API_KEY": "crawler-secret",
            "FUND_AGENT_MYSQL_PASSWORD": "another-secret",
        }
    )

    safe_text = repr(settings) + repr(settings.model_dump())
    assert "db-secret" not in safe_text
    assert "crawler-secret" not in safe_text
    assert "another-secret" not in safe_text
    assert "crawler_api_key" not in settings.model_dump()
    assert "mysql_password" not in settings.model_dump()
    assert "db-secret" not in settings.model_dump_json()
    assert "crawler-secret" not in settings.model_dump_json()
