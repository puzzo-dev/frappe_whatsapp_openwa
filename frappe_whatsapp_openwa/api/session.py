import frappe


@frappe.whitelist()
def get_qr(session_name):
	doc = frappe.get_doc("OpenWA Session", session_name)
	doc.check_permission("read")
	return {"qr_code_data": doc.qr_code_data, "status": doc.status}


@frappe.whitelist()
def get_status(session_name):
	doc = frappe.get_doc("OpenWA Session", session_name)
	doc.check_permission("read")
	return {
		"status": doc.status,
		"requires_human_attention": doc.requires_human_attention,
		"last_health_check": doc.last_health_check,
	}
