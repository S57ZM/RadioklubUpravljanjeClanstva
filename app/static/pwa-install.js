(() => {
  "use strict";

  if ("serviceWorker" in navigator) {
    window.addEventListener("load", () => {
      navigator.serviceWorker.register("/service-worker.js", { scope: "/" })
        .catch((error) => console.error("Service worker registration failed:", error));
    });
  }

  let deferredPrompt = null;

  const createInstallButton = () => {
    if (document.getElementById("pwa-install-button")) {
      return;
    }

    const button = document.createElement("button");
    button.id = "pwa-install-button";
    button.type = "button";
    button.textContent = "Namesti aplikacijo";
    button.setAttribute("aria-label", "Namesti aplikacijo S50TTT");
    button.style.position = "fixed";
    button.style.right = "16px";
    button.style.bottom = "16px";
    button.style.zIndex = "1080";
    button.style.border = "0";
    button.style.borderRadius = "999px";
    button.style.padding = "12px 18px";
    button.style.fontWeight = "700";
    button.style.background = "#0d6efd";
    button.style.color = "#ffffff";
    button.style.boxShadow = "0 4px 14px rgba(0,0,0,.25)";
    button.style.display = "none";

    button.addEventListener("click", async () => {
      if (!deferredPrompt) {
        return;
      }

      deferredPrompt.prompt();
      await deferredPrompt.userChoice;
      deferredPrompt = null;
      button.style.display = "none";
    });

    document.body.appendChild(button);
  };

  window.addEventListener("DOMContentLoaded", createInstallButton);

  window.addEventListener("beforeinstallprompt", (event) => {
    event.preventDefault();
    deferredPrompt = event;
    const button = document.getElementById("pwa-install-button");
    if (button) {
      button.style.display = "block";
    }
  });

  window.addEventListener("appinstalled", () => {
    deferredPrompt = null;
    const button = document.getElementById("pwa-install-button");
    if (button) {
      button.style.display = "none";
    }
  });
})();