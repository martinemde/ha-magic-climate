"""Shared pytest fixtures.

The preset tests are pure logic and need nothing here. The climate tests load
the integration inside a real Home Assistant instance, which requires the
custom-integration loader to be enabled.
"""
import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Allow Home Assistant to import custom_components/magic_climate in tests."""
    yield
