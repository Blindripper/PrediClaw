function setActiveTab(target) {
  const tabs = document.querySelectorAll("[data-tab]");
  const panels = document.querySelectorAll("[data-panel]");

  tabs.forEach((tab) => {
    tab.classList.toggle("active", tab.dataset.tab === target);
    tab.setAttribute("aria-selected", tab.dataset.tab === target ? "true" : "false");
  });

  panels.forEach((panel) => {
    panel.classList.toggle("active", panel.dataset.panel === target);
  });
}

function bindTabs() {
  const tabs = document.querySelectorAll("[data-tab]");
  if (!tabs.length) return;

  tabs.forEach((tab) => {
    tab.addEventListener("click", () => setActiveTab(tab.dataset.tab));
  });
}

function bindScroll() {
  const triggers = document.querySelectorAll("[data-scroll]");
  triggers.forEach((trigger) => {
    trigger.addEventListener("click", () => {
      const targetId = trigger.dataset.scroll;
      const target = document.getElementById(targetId);
      if (target) {
        target.scrollIntoView({ behavior: "smooth", block: "start" });
      }
    });
  });
}

document.addEventListener("DOMContentLoaded", () => {
  bindTabs();
  bindScroll();
});
