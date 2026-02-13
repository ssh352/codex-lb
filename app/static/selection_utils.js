(function (root, factory) {
	const api = factory();
	if (typeof module === "object" && module && typeof module.exports === "object") {
		module.exports = api;
		return;
	}
	root.CodexLbSelection = api;
})(typeof globalThis !== "undefined" ? globalThis : this, () => {
	"use strict";

	const normalizeIds = (value) => {
		if (!Array.isArray(value)) {
			return [];
		}
		const seen = new Set();
		const out = [];
		for (const item of value) {
			const id = String(item ?? "").trim();
			if (!id || seen.has(id)) {
				continue;
			}
			seen.add(id);
			out.push(id);
		}
		return out;
	};

	const rangeBetween = (orderedIds, fromId, toId) => {
		const ids = normalizeIds(orderedIds);
		const startIndex = ids.indexOf(fromId);
		const endIndex = ids.indexOf(toId);
		if (startIndex === -1 || endIndex === -1) {
			return [toId].filter(Boolean);
		}
		const start = Math.min(startIndex, endIndex);
		const end = Math.max(startIndex, endIndex);
		return ids.slice(start, end + 1).filter(Boolean);
	};

	const orderedSelection = (orderedIds, selectedSet) => {
		const ordered = normalizeIds(orderedIds);
		const out = [];
		for (const id of ordered) {
			if (selectedSet.has(id)) {
				out.push(id);
			}
		}
		for (const id of selectedSet) {
			if (!ordered.includes(id)) {
				out.push(id);
			}
		}
		return out;
	};

	const nextSelection = ({
		orderedIds,
		clickedId,
		selectedIds,
		anchorId,
		shift = false,
		ctrl = false,
		meta = false,
	}) => {
		const clicked = String(clickedId ?? "").trim();
		if (!clicked) {
			return { selectedIds: normalizeIds(selectedIds), anchorId: anchorId || "" };
		}

		const ordered = normalizeIds(orderedIds);
		const selected = new Set(normalizeIds(selectedIds));
		const toggle = Boolean(ctrl || meta);

		const hasAnchor = typeof anchorId === "string" && anchorId.trim();
		const effectiveAnchor = hasAnchor
			? anchorId.trim()
			: selected.size === 1
				? Array.from(selected)[0]
				: "";

		if (shift) {
			const base = ordered.includes(effectiveAnchor) ? effectiveAnchor : clicked;
			const range = rangeBetween(ordered, base, clicked);
			const next = toggle ? new Set([...selected, ...range]) : new Set(range);
			return {
				selectedIds: orderedSelection(ordered, next),
				anchorId: effectiveAnchor || clicked,
			};
		}

		if (toggle) {
			if (selected.has(clicked)) {
				selected.delete(clicked);
			} else {
				selected.add(clicked);
			}
			return {
				selectedIds: orderedSelection(ordered, selected),
				anchorId: clicked,
			};
		}

		return {
			selectedIds: [clicked],
			anchorId: clicked,
		};
	};

	return {
		reconcileSelection: ({ existingIds, selectedIds, anchorId }) => {
			const existing = new Set(normalizeIds(existingIds));
			const selected = normalizeIds(selectedIds).filter((id) => existing.has(id));
			const rawAnchor = typeof anchorId === "string" ? anchorId.trim() : "";
			const nextAnchor = rawAnchor && existing.has(rawAnchor) ? rawAnchor : "";
			return { selectedIds: selected, anchorId: nextAnchor };
		},
		nextSelection,
	};
});
