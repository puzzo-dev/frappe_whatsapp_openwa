"""Pytest fixtures for frappe_whatsapp_openwa unit tests.

These tests run outside Frappe's request context — no bench, no database.
Imports that would touch frappe are mocked at module level below.
"""

from __future__ import annotations

import sys
from types import ModuleType
from unittest.mock import MagicMock

import pytest


def _make_frappe_stub() -> ModuleType:
	"""Minimal frappe stub so non-Frappe modules can be imported."""
	mod = ModuleType("frappe")
	mod._ = lambda x: x
	mod.get_doc = MagicMock()
	mod.get_single = MagicMock()
	mod.new_doc = MagicMock()
	mod.cache = MagicMock(return_value=MagicMock())
	mod.throw = MagicMock(side_effect=ValueError)
	mod.log_error = MagicMock()

	# Decorator used at module import time — must be present before any app module loads.
	mod.whitelist = lambda allow_guest=False, **kw: (lambda fn: fn)

	# Exception classes referenced in module bodies.
	mod.ValidationError = type("ValidationError", (Exception,), {})
	mod.AuthenticationError = type("AuthenticationError", (Exception,), {})
	mod.TooManyRequestsError = type("TooManyRequestsError", (Exception,), {})
	mod.DoesNotExistError = type("DoesNotExistError", (Exception,), {})
	mod.PermissionError = type("PermissionError", (Exception,), {})

	# db stub — many modules call frappe.db.get_value / get_all / sql / commit
	mod.db = MagicMock()

	return mod


# Install stubs at module import time so test modules that do
# `import frappe` at the top level can be collected successfully.
if "frappe" not in sys.modules:
	sys.modules["frappe"] = _make_frappe_stub()
if "frappe_whatsapp" not in sys.modules:
	fwa = ModuleType("frappe_whatsapp")
	fwa.utils = ModuleType("frappe_whatsapp.utils")
	sys.modules["frappe_whatsapp"] = fwa
	sys.modules["frappe_whatsapp.utils"] = fwa.utils

# frappe.model.document.Document is used as a fallback base class in overrides.
_frappe_model = ModuleType("frappe.model")
_frappe_model_doc = ModuleType("frappe.model.document")
_frappe_model_doc.Document = type("Document", (), {})
sys.modules["frappe.model"] = _frappe_model
sys.modules["frappe.model.document"] = _frappe_model_doc


@pytest.fixture(autouse=True, scope="session")
def patch_frappe():
	"""Ensure stubs remain installed for the entire test session."""
	if "frappe" not in sys.modules:
		sys.modules["frappe"] = _make_frappe_stub()
	if "frappe_whatsapp" not in sys.modules:
		fwa = ModuleType("frappe_whatsapp")
		fwa.utils = ModuleType("frappe_whatsapp.utils")
		sys.modules["frappe_whatsapp"] = fwa
		sys.modules["frappe_whatsapp.utils"] = fwa.utils
