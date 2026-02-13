(function (root, factory) {
	const api = factory();
	if (typeof module === "object" && module && typeof module.exports === "object") {
		module.exports = api;
		return;
	}
	root.CodexLbUiUtils = api;
})(typeof globalThis !== "undefined" ? globalThis : this, () => {
	"use strict";

	const formatAccountIdShort = (value) => {
		const raw = String(value ?? "").trim();
		if (!raw) {
			return "";
		}
		if (raw.length <= 16) {
			return raw;
		}
		const head = raw.slice(0, 8);
		const tail = raw.slice(-4);
		return `${head}…${tail}`;
	};

	return {
		formatAccountIdShort,
	};
});

