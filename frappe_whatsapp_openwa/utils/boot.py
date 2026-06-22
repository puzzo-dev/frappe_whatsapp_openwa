def get_bootinfo(bootinfo):
	from frappe_whatsapp_openwa import __version__
	bootinfo["frappe_whatsapp_openwa"] = {"version": __version__}
