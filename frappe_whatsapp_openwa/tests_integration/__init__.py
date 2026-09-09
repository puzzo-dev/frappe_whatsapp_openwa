"""Tests that run against a real site database.

The sibling `tests/` package stubs frappe wholesale — its conftest sets
`frappe.db` to a MagicMock — which is the right trade for the pure-logic units
but means a stubbed call can never disagree with the real schema. A MagicMock
accepts any keyword and returns a Mock, so a query that MariaDB would reject
looks perfectly healthy there. Tests in this package use the real connection
and exist to catch exactly that class of bug.
"""
