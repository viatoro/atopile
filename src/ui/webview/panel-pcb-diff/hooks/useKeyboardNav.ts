import { useEffect } from "react";

interface KeyboardNavOptions {
    items: string[];
    selectedId: string | null;
    onSelect: (id: string | null) => void;
    searchInputRef: React.RefObject<HTMLInputElement | null>;
}

/**
 * Keyboard navigation for the diff sidebar.
 * j/k: navigate items, /: focus search, Escape: clear selection, Enter: select first.
 */
export function useKeyboardNav({
    items,
    selectedId,
    onSelect,
    searchInputRef,
}: KeyboardNavOptions) {
    useEffect(() => {
        const handler = (e: KeyboardEvent) => {
            const target = e.target as HTMLElement;
            const isInput = target.tagName === "INPUT" || target.tagName === "TEXTAREA";

            if (e.key === "/" && !isInput) {
                e.preventDefault();
                searchInputRef.current?.focus();
                return;
            }

            if (e.key === "Escape") {
                onSelect(null);
                if (isInput) {
                    (target as HTMLInputElement).blur();
                }
                return;
            }

            if (isInput) return;

            if (e.key === "j" || e.key === "k") {
                e.preventDefault();
                if (items.length === 0) return;

                const currentIdx = selectedId ? items.indexOf(selectedId) : -1;
                let nextIdx: number;
                if (e.key === "j") {
                    nextIdx = currentIdx < items.length - 1 ? currentIdx + 1 : 0;
                } else {
                    nextIdx = currentIdx > 0 ? currentIdx - 1 : items.length - 1;
                }
                onSelect(items[nextIdx]!);
                return;
            }

            if (e.key === "Enter" && items.length > 0 && !selectedId) {
                e.preventDefault();
                onSelect(items[0]!);
            }
        };

        document.addEventListener("keydown", handler);
        return () => document.removeEventListener("keydown", handler);
    }, [items, selectedId, onSelect, searchInputRef]);
}
