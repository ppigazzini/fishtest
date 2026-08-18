(async () => {
  await DOMContentLoaded();

  const button = document.getElementById("machines-button");
  const panel = document.getElementById("machines-panel");
  const target = document.getElementById("machines");
  const filtersForm = document.getElementById("machines-filters");

  if (
    !(button instanceof HTMLElement) ||
    !(panel instanceof HTMLElement) ||
    !(target instanceof HTMLElement)
  ) {
    return;
  }

  const toggleCookieMaxAge = Number(button.dataset.toggleCookieMaxAge || "0");

  const syncPanelState = (isExpanded) => {
    const nextState = isExpanded ? "Hide" : "Show";
    button.textContent = nextState;
    button.setAttribute("aria-expanded", String(isExpanded));
    if (Number.isFinite(toggleCookieMaxAge) && toggleCookieMaxAge > 0) {
      writeUiCookie("machines_state", nextState, toggleCookieMaxAge);
    }
  };

  const resetMachinesPage = () => {
    const pageInput = document.getElementById("machines_page");
    if (pageInput instanceof HTMLInputElement) {
      pageInput.value = "1";
    }
  };

  filtersForm?.addEventListener("input", (event) => {
    if (
      event.target instanceof HTMLElement &&
      event.target.id === "machines_q"
    ) {
      resetMachinesPage();
    }
  });

  filtersForm?.addEventListener("change", (event) => {
    if (
      event.target instanceof HTMLElement &&
      event.target.id === "machines_my_workers"
    ) {
      resetMachinesPage();
    }
  });

  panel.addEventListener("shown.bs.collapse", () => {
    syncPanelState(true);
    if (target.dataset.machinesLoaded !== "1") {
      target.dataset.machinesLoaded = "loading";
    }
    htmx.trigger(target, "machines:load");
  });

  panel.addEventListener("hidden.bs.collapse", () => {
    syncPanelState(false);
  });

  // #machines is both the requesting element and the swap target, so htmx
  // dispatches these events here.
  target.addEventListener("htmx:before:request", () => {
    if (target.dataset.machinesLoaded !== "1") {
      target.dataset.machinesLoaded = "loading";
    }
  });

  // Match on the swap target, not on the element that asked for it. htmx
  // dispatches swap events on the requesting element, and #machines is filled
  // by two of them: its own load/poll triggers, and #machines-filters when the
  // reader types. onHtmxSwap already ignores responses htmx did not swap, so a
  // 4xx or 5xx leaves the retry state below intact.
  onHtmxSwap(
    (swapped) => swapped.id === "machines",
    () => {
      target.dataset.machinesLoaded = "1";
    },
  );

  const restoreRetryState = () => {
    if (target.dataset.machinesLoaded !== "1") {
      target.dataset.machinesLoaded = "0";
    }
  };

  target.addEventListener("htmx:response:error", restoreRetryState);
  target.addEventListener("htmx:error", (event) => {
    // htmx reports an aborted fetch here too. #machines-filters shares a
    // request queue with this panel and carries no hx-sync, so typing in the
    // filter aborts an in-flight poll tick and a replacement request is
    // already on its way. Showing the retry state for that is wrong.
    if (htmxRequestAborted(event)) {
      return;
    }
    restoreRetryState();
  });
})();
