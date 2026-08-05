"""Session alert monitoring — disabled.

Previously sent Frappe Notifications every 5 minutes when sessions needed
attention. This caused notification spam. Session status is now monitored
via the workspace dashboard and Error Log entries only.
"""


def check_session_alerts():
	"""No-op stub — kept so any stale scheduler entry doesn't raise."""
	pass
