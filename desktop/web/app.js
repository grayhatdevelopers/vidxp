const invoke = window.__TAURI__.core.invoke;

const capabilitiesNode = document.querySelector("#capabilities");
const installButton = document.querySelector("#install");
const launchButton = document.querySelector("#launch");
const prepareModels = document.querySelector("#prepare-models");
const statusNode = document.querySelector("#status");

let manifest;

function setBusy(busy) {
  installButton.disabled = busy;
  launchButton.disabled = busy || launchButton.dataset.ready !== "true";
}

function selectedCapabilities() {
  return [...document.querySelectorAll("[data-capability]:checked")].map(
    (node) => node.dataset.capability,
  );
}

function renderCapabilities() {
  capabilitiesNode.replaceChildren();
  Object.entries(manifest.capabilities).forEach(([id, capability]) => {
    const label = document.createElement("label");
    label.className = "capability";

    const input = document.createElement("input");
    input.type = "checkbox";
    input.checked = true;
    input.dataset.capability = id;

    const copy = document.createElement("span");
    const name = document.createElement("strong");
    name.textContent = capability.label;
    const detail = document.createElement("small");
    detail.textContent = `Installs the ${capability.extra} capability`;
    copy.append(name, detail);
    label.append(input, copy);
    capabilitiesNode.append(label);
  });
}

async function refreshStatus() {
  const status = await invoke("runtime_status");
  if (status.ready) {
    document.querySelectorAll("[data-capability]").forEach((node) => {
      node.checked = status.capabilities.includes(node.dataset.capability);
    });
  }
  launchButton.dataset.ready = String(status.ready);
  launchButton.disabled = !status.ready;
  statusNode.textContent = status.ready
    ? `Ready · VidXP ${status.package_version} · ${status.capabilities.join(", ")}`
    : status.detail;
}

installButton.addEventListener("click", async () => {
  const capabilities = selectedCapabilities();
  if (capabilities.length === 0) {
    statusNode.textContent = "Select at least one capability.";
    return;
  }

  setBusy(true);
  statusNode.textContent = prepareModels.checked
    ? "Installing and preparing models. Large downloads can take a while…"
    : "Installing an isolated VidXP runtime…";
  try {
    const result = await invoke("install_runtime", {
      request: {
        capabilities,
        prepare_models: prepareModels.checked,
      },
    });
    statusNode.textContent = result.prepared
      ? "Installed, validated, and models prepared."
      : "Installed and validated.";
    await refreshStatus();
  } catch (error) {
    statusNode.textContent = `Setup failed: ${error}`;
  } finally {
    setBusy(false);
  }
});

launchButton.addEventListener("click", async () => {
  setBusy(true);
  statusNode.textContent = "Starting the local interface…";
  try {
    const url = await invoke("launch_ui");
    window.location.replace(url);
  } catch (error) {
    statusNode.textContent = `Launch failed: ${error}`;
    setBusy(false);
  }
});

async function start() {
  try {
    manifest = await invoke("runtime_manifest");
    renderCapabilities();
    await refreshStatus();
  } catch (error) {
    statusNode.textContent = `Desktop initialization failed: ${error}`;
    setBusy(true);
  }
}

start();
