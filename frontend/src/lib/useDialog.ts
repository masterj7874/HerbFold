import { useLayoutEffect, useRef } from "react";

type ModalEntry = {
  element: HTMLElement;
  close: () => void;
  opener: HTMLElement | null;
};
const modals: ModalEntry[] = [];
const backgroundState = new Map<HTMLElement, boolean>();
let observer: MutationObserver | null = null;
let originalOverflow = "";
let originalPaddingRight = "";

const FOCUSABLE = [
  "button:not([disabled])",
  "a[href]",
  "area[href]",
  'input:not([disabled]):not([type="hidden"])',
  "select:not([disabled])",
  "textarea:not([disabled])",
  "iframe",
  "summary",
  '[contenteditable="true"]',
  "[tabindex]",
].join(",");

function visible(element: HTMLElement): boolean {
  return (
    element.getClientRects().length > 0 &&
    getComputedStyle(element).visibility !== "hidden" &&
    !element.closest("[inert]") &&
    !element.closest('[aria-hidden="true"]')
  );
}

function tabbables(element: HTMLElement): HTMLElement[] {
  return [...element.querySelectorAll<HTMLElement>(FOCUSABLE)].filter(
    (item) => item.tabIndex >= 0 && !item.matches(":disabled") && visible(item),
  );
}

function focusInitial(entry: ModalEntry): void {
  const requested = entry.element.querySelector<HTMLElement>(
    "[data-dialog-initial-focus], [autofocus]",
  );
  const target =
    requested && visible(requested)
      ? requested
      : (tabbables(entry.element)[0] ?? entry.element);
  target.focus({ preventScroll: true });
}

/** Inert only siblings along the active dialog's ancestor path, never its own ancestor. */
function reconcileBackground(): void {
  const top = modals.at(-1);
  const desired = new Set<HTMLElement>();
  if (top) {
    let branch: HTMLElement = top.element;
    while (branch.parentElement) {
      const parent = branch.parentElement;
      for (const sibling of parent.children) {
        if (sibling !== branch && sibling instanceof HTMLElement)
          desired.add(sibling);
      }
      if (parent === document.body) break;
      branch = parent;
    }
  }
  for (const [element, wasInert] of backgroundState) {
    if (!desired.has(element)) {
      element.inert = wasInert;
      backgroundState.delete(element);
    }
  }
  for (const element of desired) {
    if (!backgroundState.has(element))
      backgroundState.set(element, element.inert);
    element.inert = true;
  }
}

/**
 * Focus/scroll ownership for a conditionally mounted `role="dialog" aria-modal="true"`.
 * Attach the returned ref and `tabIndex={-1}` to that dialog. Optionally mark its
 * heading with `data-dialog-initial-focus tabIndex={-1}` for a long form.
 * Follows https://www.w3.org/WAI/ARIA/apg/patterns/dialog-modal/.
 */
export function useDialog<T extends HTMLElement = HTMLElement>(
  onClose: () => void,
  active = true,
) {
  const ref = useRef<T>(null);
  const closeRef = useRef(onClose);
  useLayoutEffect(() => {
    closeRef.current = onClose;
  }, [onClose]);

  useLayoutEffect(() => {
    const element = ref.current;
    if (!active || !element) return;
    const entry: ModalEntry = {
      element,
      close: () => closeRef.current(),
      opener:
        document.activeElement instanceof HTMLElement
          ? document.activeElement
          : null,
    };
    if (modals.length === 0) {
      originalOverflow = document.body.style.overflow;
      originalPaddingRight = document.body.style.paddingRight;
      const scrollbarWidth = Math.max(
        0,
        window.innerWidth - document.documentElement.clientWidth,
      );
      const padding =
        Number.parseFloat(getComputedStyle(document.body).paddingRight) || 0;
      document.body.style.overflow = "hidden";
      if (scrollbarWidth > 0)
        document.body.style.paddingRight = `${padding + scrollbarWidth}px`;
      observer = new MutationObserver(reconcileBackground);
      observer.observe(document.body, { childList: true, subtree: true });
    }
    modals.push(entry);
    reconcileBackground();
    focusInitial(entry);

    const onKeyDown = (event: KeyboardEvent) => {
      if (modals.at(-1) !== entry || event.isComposing) return;
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        entry.close();
        return;
      }
      if (event.key !== "Tab" || event.altKey || event.ctrlKey || event.metaKey)
        return;
      const items = tabbables(element);
      if (!items.length) {
        event.preventDefault();
        element.focus({ preventScroll: true });
        return;
      }
      const first = items[0];
      const last = items[items.length - 1];
      const current = document.activeElement;
      const currentIsTabbable =
        current instanceof HTMLElement && items.includes(current);
      if (event.shiftKey && (current === first || !currentIsTabbable)) {
        event.preventDefault();
        last.focus({ preventScroll: true });
      } else if (!event.shiftKey && (current === last || !currentIsTabbable)) {
        event.preventDefault();
        first.focus({ preventScroll: true });
      }
    };
    const onFocusIn = (event: FocusEvent) => {
      if (
        modals.at(-1) === entry &&
        event.target instanceof Node &&
        !element.contains(event.target)
      )
        focusInitial(entry);
    };
    document.addEventListener("keydown", onKeyDown, true);
    document.addEventListener("focusin", onFocusIn, true);

    return () => {
      document.removeEventListener("keydown", onKeyDown, true);
      document.removeEventListener("focusin", onFocusIn, true);
      const wasTop = modals.at(-1) === entry;
      const index = modals.indexOf(entry);
      if (index >= 0) modals.splice(index, 1);
      reconcileBackground();
      if (modals.length === 0) {
        observer?.disconnect();
        observer = null;
        document.body.style.overflow = originalOverflow;
        document.body.style.paddingRight = originalPaddingRight;
      }
      if (wasTop) {
        if (entry.opener?.isConnected && visible(entry.opener))
          entry.opener.focus({ preventScroll: true });
        else if (modals.at(-1)) focusInitial(modals.at(-1)!);
      }
    };
  }, [active]);

  return ref;
}

export default useDialog;
