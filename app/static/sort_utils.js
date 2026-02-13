(function (root, factory) {
	const api = factory();
	if (typeof module === "object" && module && typeof module.exports === "object") {
		module.exports = api;
		return;
	}
	root.CodexLbSortUtils = api;
})(typeof globalThis !== "undefined" ? globalThis : this, () => {
	"use strict";

	const toNumber = (value) => {
		const num = Number(value);
		return Number.isFinite(num) ? num : null;
	};

	const parseTimestamp = (value) => {
		if (typeof value !== "string") {
			return null;
		}
		const parsed = Date.parse(value);
		return Number.isFinite(parsed) ? parsed : null;
	};

	const compareNullable = (a, b) => {
		const aNull = a === null || a === undefined || a === "";
		const bNull = b === null || b === undefined || b === "";
		if (aNull && bNull) {
			return 0;
		}
		if (aNull) {
			return 1;
		}
		if (bNull) {
			return -1;
		}
		if (typeof a === "number" && typeof b === "number") {
			return a - b;
		}
		const collator = new Intl.Collator(undefined, { numeric: true, sensitivity: "base" });
		return collator.compare(String(a), String(b));
	};

	const accountValueForKey = (account, key) => {
		if (!account) {
			return null;
		}
		if (key === "email") {
			return account.email || "";
		}
		if (key === "id") {
			return account.id || "";
		}
		if (key === "plan") {
			return account.plan || "";
		}
		if (key === "status") {
			return account.status || "";
		}
		if (key === "remainingSecondaryPercent") {
			return toNumber(account.usage?.secondaryRemainingPercent);
		}
		if (key === "quotaResetSecondary") {
			return parseTimestamp(account.resetAtSecondary);
		}
		return account.id || "";
	};

	const sortAccounts = (accounts, { sortKey, sortDirection } = {}) => {
		const list = Array.isArray(accounts) ? [...accounts] : [];
		if (list.length <= 1) {
			return list;
		}

		const key = String(sortKey || "email");
		const direction = String(sortDirection || "asc") === "desc" ? -1 : 1;
		const isQuotaExceeded = (account) => {
			const status = String(account?.status ?? "").trim().toLowerCase();
			if (status === "exceeded" || status === "quota_exceeded") {
				return true;
			}
			const remaining = toNumber(account?.usage?.secondaryRemainingPercent);
			return remaining !== null && remaining <= 0;
		};

		const compareKey = (a, b, k, dir = 1) => {
			const av = accountValueForKey(a, k);
			const bv = accountValueForKey(b, k);
			return compareNullable(av, bv) * dir;
		};

		return list.sort((a, b) => {
			const aExceeded = isQuotaExceeded(a);
			const bExceeded = isQuotaExceeded(b);
			if (aExceeded !== bExceeded) {
				return aExceeded ? 1 : -1;
			}
			if (aExceeded && bExceeded) {
				const exceededDir = key === "quotaResetSecondary" ? direction : 1;
				const byReset = compareKey(a, b, "quotaResetSecondary", exceededDir);
				if (byReset !== 0) {
					return byReset;
				}
			}

			const primary = compareKey(a, b, key, direction);
			if (primary !== 0) {
				return primary;
			}

			// Tiebreaks (stable operator expectations):
			// - Quota reset(7D) ties break by Remaining(7D) (ascending).
			// - Remaining(7D) ties break by Quota reset(7D) (earlier reset first).
			if (key === "quotaResetSecondary") {
				const byRemaining = compareKey(a, b, "remainingSecondaryPercent", 1);
				if (byRemaining !== 0) {
					return byRemaining;
				}
			} else if (key === "remainingSecondaryPercent") {
				const byReset = compareKey(a, b, "quotaResetSecondary", 1);
				if (byReset !== 0) {
					return byReset;
				}
			}

			const byEmail = compareKey(a, b, "email", 1);
			if (byEmail !== 0) {
				return byEmail;
			}
			return compareKey(a, b, "id", 1);
		});
	};

	return {
		sortAccounts,
	};
});
