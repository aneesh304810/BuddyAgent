"""Connectors: one systematic way to bring your apps' data into Buddy.

Every app is a small plug-in (a Connector) with the same shape: the secrets it
needs, how often it syncs, and a fetch() that returns Records. The hub runs
the schedule, masks and stores the records in the encrypted database, tracks
health, and gives Buddy tools to search them. Connectors only read; anything
that changes things in an app goes through an agent with approvals.
"""

from .base import Connector, Record
from .hub import (
    CONNECTOR_TOOL_NAMES, CONNECTOR_TOOLS, REGISTRY, ConnectorHub, agenda, run_connector_tool,
)

__all__ = [
    "Connector", "Record", "ConnectorHub", "REGISTRY", "CONNECTOR_TOOLS", "CONNECTOR_TOOL_NAMES",
    "agenda", "run_connector_tool",
]
