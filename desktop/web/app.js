const invoke = window.__TAURI__.core.invoke;

const surfacesNode = document.querySelector("#surfaces");
const capabilitiesNode = document.querySelector("#capabilities");
const installButton = document.querySelector("#install");
const launchButton = document.querySelector("#launch");
const prepareModels = document.querySelector("#prepare-models");
const modelDirectoryNode = document.querySelector("#model-directory");
const chooseModelDirectoryButton = document.querySelector(
  "#choose-model-directory",
);
const statusNode = document.querySelector("#status");

let manifest;
let selectedModelDirectory;

function setBusy(busy) {
  installButton.disabled = busy;
  chooseModelDirectoryButton.disabled = busy;
  launchButton.disabled = busy || launchButton.dataset.ready !== "true";
}

function selectedCapabilities() {
  return [...document.querySelectorAll("[data-capability]:checked")].map(
    (node) => node.dataset.capability,
  );
}

function selectedSurfaces() {
  return [...document.querySelectorAll("[data-surface]:checked")].map(
    (node) => node.dataset.surface,
  );
}

function renderOptions(entries, container, dataAttribute, className) {
  container.replaceChildren();
  Object.entries(entries).forEach(([id, option]) => {
    const label = document.createElement("label");
    label.className = className;

    const input = document.createElement("input");
    input.type = "checkbox";
    input.checked = option.default ?? true;
    input.dataset[dataAttribute] = id;

    const copy = document.createElement("span");
    const name = document.createElement("strong");
    name.textContent = option.label;
    const detail = document.createElement("small");
    detail.textContent =
      option.description ?? `Installs the ${option.extra} capability`;
    copy.append(name, detail);
    label.append(input, copy);
    container.append(label);
  });
}

function renderManifest() {
  renderOptions(manifest.surfaces, surfacesNode, "surface", "surface");
  renderOptions(
    manifest.capabilities,
    capabilitiesNode,
    "capability",
    "capability",
  );
}

function applySelection(attribute, selected) {
  document.querySelectorAll(`[data-${attribute}]`).forEach((node) => {
    node.checked = selected.includes(node.dataset[attribute]);
  });
}

async function refreshStatus() {
  const status = await invoke("runtime_status");
  selectedModelDirectory ??= status.model_directory;
  modelDirectoryNode.textContent = selectedModelDirectory;

  if (status.ready) {
    applySelection("capability", status.capabilities);
    applySelection("surface", status.surfaces);
  }

  const browserReady = status.ready && status.surfaces.includes("browser");
  launchButton.dataset.ready = String(browserReady);
  launchButton.disabled = !browserReady;
  statusNode.textContent = status.ready
    ? `Ready · VidXP ${status.package_version} · ${status.capabilities.join(", ")}`
    : status.detail;
  return status;
}

async function ensureMediaRuntime() {
  const status = await invoke("media_runtime_status");
  if (status.ready) {
    return status;
  }
  if (!status.install_command) {
    throw new Error(
      `${status.errors.join(" ")} Install FFmpeg and ffprobe, then retry.`,
    );
  }
  statusNode.textContent = "Waiting for FFmpeg setup confirmation…";
  return invoke("install_media_runtime");
}

async function launchBrowser() {
  setBusy(true);
  statusNode.textContent = "Starting VidXP…";
  try {
    await invoke("launch_ui");
    statusNode.textContent = "VidXP is running in the system tray.";
  } catch (error) {
    statusNode.textContent = `Launch failed: ${error}`;
    setBusy(false);
  }
}

chooseModelDirectoryButton.addEventListener("click", async () => {
  try {
    const selection = await invoke("choose_model_directory");
    if (selection) {
      selectedModelDirectory = selection;
      modelDirectoryNode.textContent = selection;
    }
  } catch (error) {
    statusNode.textContent = `Could not select the model folder: ${error}`;
  }
});

installButton.addEventListener("click", async () => {
  const capabilities = selectedCapabilities();
  if (capabilities.length === 0) {
    statusNode.textContent = "Select at least one capability.";
    return;
  }

  setBusy(true);
  try {
    statusNode.textContent = "Checking FFmpeg and required codecs…";
    await ensureMediaRuntime();
    statusNode.textContent = prepareModels.checked
      ? "Configuring local processing and downloading selected models…"
      : "Configuring local processing…";
    const result = await invoke("install_runtime", {
      request: {
        capabilities,
        surfaces: selectedSurfaces(),
        prepare_models: prepareModels.checked,
        model_directory: selectedModelDirectory,
      },
    });
    selectedModelDirectory = result.model_directory;
    statusNode.textContent = result.prepared
      ? "Local processing and models are ready."
      : "Local processing is ready. Model downloads were deferred.";
    await refreshStatus();
    if (result.surfaces.includes("browser")) {
      await launchBrowser();
    } else {
      await invoke("hide_to_tray");
    }
  } catch (error) {
    statusNode.textContent = `Configuration failed: ${error}`;
  } finally {
    setBusy(false);
  }
});

launchButton.addEventListener("click", launchBrowser);

async function start() {
  try {
    manifest = await invoke("runtime_manifest");
    renderManifest();
    const status = await refreshStatus();
    if (status.ready && status.surfaces.includes("browser")) {
      await launchBrowser();
    } else if (status.ready) {
      await invoke("hide_to_tray");
    }
  } catch (error) {
    statusNode.textContent = `Desktop initialization failed: ${error}`;
    setBusy(true);
  }
}

start();
