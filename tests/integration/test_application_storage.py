from fund_agent.application import FundAgentApplication
from fund_agent.config.settings import Settings
from fund_agent.persistence.mysql import MySqlRepository


def test_application_uses_mysql_repository_when_database_fields_are_configured():
    settings = Settings.from_env(
        {
            "FUND_AGENT_MYSQL_HOST": "mysql.internal",
            "FUND_AGENT_MYSQL_DATABASE": "funds",
            "FUND_AGENT_MYSQL_USER": "app",
            "FUND_AGENT_MYSQL_PASSWORD": "secret",
        }
    )
    repository = FundAgentApplication.repository_from_settings(settings, connect=lambda: None)

    assert isinstance(repository, MySqlRepository)


def test_application_initializes_configured_mysql_schema():
    class TestRepository(MySqlRepository):
        def __init__(self):
            self.initialized = False

        def initialize_schema(self):
            self.initialized = True

        def list_alerts(self):
            return []

    mysql = TestRepository()
    FundAgentApplication(repository=mysql, settings=Settings.from_env({}), source_adapters=[])

    assert mysql.initialized is True
