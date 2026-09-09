"""FrappeTestCase under the name each version actually provides.

v15 exposes it as `frappe.tests.utils.FrappeTestCase`. In v16 `frappe.tests`
became a package whose DB-backed base class is `IntegrationTestCase`; the old
name survives there only as a deprecation shim, so prefer the new one and fall
back rather than the other way round.
"""

try:  # Frappe v16 and later
	from frappe.tests import IntegrationTestCase as FrappeTestCase
except ImportError:  # Frappe v15
	from frappe.tests.utils import FrappeTestCase

__all__ = ["FrappeTestCase"]
