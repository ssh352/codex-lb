(function (root, factory) {
	const api = factory();
	if (typeof module === "object" && module && typeof module.exports === "object") {
		module.exports = api;
		return;
	}
	root.CodexLbStateDefaults = api;
})(typeof globalThis !== "undefined" ? globalThis : this, () => {
	"use strict";

		const createDefaultAccountsState = () => ({
			selectedId: "",
			selectedIds: [],
			selectionAnchorId: "",
			sortKey: "quotaResetSecondary",
			sortDirection: "asc",
			rows: [],
			searchQuery: "",
			pinnedOnly: false,
		});

	return {
		createDefaultAccountsState,
	};
});
