import pytest

from injecteval.scenario import load_scenarios


@pytest.fixture(scope="session")
def scenarios():
    return load_scenarios()


@pytest.fixture(scope="session")
def by_id(scenarios):
    return {scenario.id: scenario for scenario in scenarios}
