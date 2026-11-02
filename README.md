# InvoSift Desktop

A Windows desktop wrapper for the InvoSift local invoice extraction backend. The app window is launched by Electron; the Python API runs in the background and is stopped when the desktop app exits. User data is stored under Electron's per-user application data folder, not in the source directory.

## Run the app without building an installer (recommended for development/testing)

1. Extract this folder somewhere convenient.
2. Install Node.js LTS and Python 3.12 x64 if they are not already installed.
3. Open the extracted folder and double-click **`RUN-APP.bat`**.
4. The first launch creates a private `.run-venv` and installs the Python/OCR dependencies. This can take a while and requires a working internet connection. Later launches reuse that environment.
5. The app runs in its own desktop window. Close the window to stop it. It does not register an installed app or create Start menu entries.

If `npm install` warned that Electron's install script was blocked and the app says Electron is unavailable, run this once from Command Prompt in the project folder:

```bat
npm install-scripts approve electron
npm install
```

Then run `RUN-APP.bat` again. Do not use `npm audit fix --force` as a first troubleshooting step; it can introduce breaking dependency upgrades.

## Build a Windows installer and portable EXE

Run **`BUILD-WINDOWS.bat`** or use:

```bat
npm install
npm run dist
```

The build creates a private `.build-venv`, installs the backend requirements and PyInstaller, bundles the Python backend, and then invokes electron-builder. On success, outputs are placed in `release/`:

- `Invoice-AI-<version>-nsis.exe` — installer; creates Start menu and desktop shortcuts.
- `Invoice-AI-<version>-portable.exe` — portable executable; does not install/register the app.

The first build downloads large OCR/ML packages. A slow or unavailable network can cause package installation to fail. The build script uses longer pip timeouts and retries, does not waste time upgrading pip, and stops immediately on errors instead of trying to copy a nonexistent backend. **The `release` folder is only expected after the build completes successfully.**

## Notes

- The source-run mode needs Node.js and Python installed on the computer. The installer/portable build aims to bundle the Python backend so end users do not need to set up a Python environment.
- The local AI model is managed separately by the app's **Connect AI Model** workflow through Ollama. Downloading the model requires internet and several GB of disk space.
- This project is intended to process invoices locally. Treat invoice files as sensitive and keep your Windows account secured.


### Electron startup repair

If `RUN-APP.bat` reports that `electron` is not recognized, the Electron package may have been installed without running its postinstall script. The launcher now checks for the local Electron executable and attempts to approve Electron's install script and repair the npm installation automatically. If your network blocks the download, open a terminal in this folder and run:

```bat
npm install-scripts approve electron
npm install --include=dev --foreground-scripts
```

Then run `RUN-APP.bat` again. This is a one-time dependency repair; it is not a command you need to repeat for every launch.


## First launch and Electron repair
Run `RUN-APP.bat`. The launcher installs the dependency tree if needed, then invokes Electron's own `install.js` directly to download `electron.exe`; this avoids npm lifecycle-script allowlist blocks. A network connection is required only for first-time setup and dependency/model downloads. Later launches reuse the installed runtime and local Python environment.

The invoice extraction progress indicator is driven by backend stage callbacks (document render, OCR/text recognition, AI field extraction, validation, and save), rather than an indeterminate CSS animation.


## Extraction quality and exports
The pipeline now reconciles explicit pipe-delimited invoice table columns against the AI response. It keeps line total, tax rate and tax amount distinct, and the TXT export is a readable sectioned report (not a raw Python-style representation). General chat is available without selecting an invoice. Existing installed Ollama services are detected and reused at startup; the app attempts to start an already-installed Ollama service automatically.


## Extraction quality and exports
The pipeline now reconciles explicit pipe-delimited invoice table columns against the AI response. It keeps line total, tax rate and tax amount distinct, and the TXT export is a readable sectioned report (not a raw Python-style representation). General chat is available without selecting an invoice. Existing installed Ollama services are detected and reused at startup; the app attempts to start an already-installed Ollama service automatically.

## Regression repair notes (2026-11-01)

- The first Excel worksheet is now `Invoice Data` and includes invoice overview, supplier/customer information, the full line-item table, and financial summary. Dedicated `Line Items`, `Supplier`, `Customer`, `Financials`, `Validation`, and `Processing` worksheets remain available.
- Line-item reconciliation now prefers exact source table headers before fuzzy matching, preventing `tax_amount` from being mistaken for `line_total` when the source columns are reordered.
- Explicit tax amounts are preserved; a missing row tax can only be calculated when the source contains a tax rate and a clearly available pre-tax line amount.
- Clearly labelled invoice metadata and financial values are used to fill missing AI fields without replacing values already extracted by the model.
- Removed duplicate deterministic reconciliation and a duplicate Ollama-model route registration.
- Added regression tests: `PYTHONPATH=backend_src pytest -q tests/test_invoice_regressions.py`.

The regression tests use a representative text fixture. They do not replace end-to-end testing with real scanned PDFs, OCR and a running local Ollama model.


## UI refinement notes (November 2026)

This update applies a targeted UI refinement to the existing desktop app:
- Warm orange upload panel inspired by the supplied reference image.
- Persistent extracted-information panel and compact empty state.
- Extraction progress now avoids displaying fabricated percentages when backend progress is unavailable.
- Upload controls transition out of view while extraction is active.
- Frameless Electron window controls are wired through the secure preload bridge.

### Run in development
1. Install Node.js and Python prerequisites.
2. Run `npm install` from this project directory.
3. Install backend dependencies from `backend_src/requirements.txt`.
4. Run `npm start` (the Electron main process starts the backend).

### Build for Windows
Run `npm run dist` on a Windows build environment with the required backend build dependencies. This package was not built into Windows portable/NSIS artifacts in the current environment.

### Test report for this refinement
- `node --check ui/app.js`: passed.
- `PYTHONPATH=backend_src python -m pytest -q tests/test_invoice_regressions.py`: 2 passed.
- Electron interactive launch and Windows packaging: not executed in this environment.
- Backend extraction cancellation: not implemented in the existing API, so no fake Stop Extraction control was added.

## UI refinement update

- Reworked the upload surface to a compact glass-style drop area with a stable document stack and a five-item recent-extractions list.
- Removed the Settings navigation and changed History/About into full workspace views.
- Added a dedicated extraction-details view with invoice fields, export actions, and an embedded original-document preview plus an open-document action.
- Added invoice-date and extraction-time sorting to the full history list, alongside alphabetical sorting and search.
- Normalized chat bubble alignment and model selector styling; removed the decorative assistant diamond/avatar.


## Windows build (installer + portable EXE)

Use **64-bit Python 3.12**, Node.js LTS and npm on Windows. From the project folder, run `BUILD-WINDOWS.bat`.

The script installs the Electron dependencies, enables/verifies the Electron runtime, builds the Python backend with PyInstaller, then runs Electron Builder for both Windows targets. Successful artifacts are written to `release/`:
- `InvoSift-<version>-Setup-x64.exe` — NSIS installer.
- `InvoSift-<version>-Portable-x64.exe` — portable executable.

The first build needs an internet connection to download Python/OCR dependencies. If it fails, read the first Python/PyInstaller/npm error in the console; the script now exits non-zero rather than claiming a successful build.

## Image invoice extraction

Image invoices use the local OCR engine to produce text, then the configured Ollama model extracts structured invoice fields from that text. This is the default because sending full-resolution image tensors to a small local vision model can take substantially longer on CPU/shared-memory systems.

Optional image-assisted vision can be enabled by setting `OLLAMA_USE_IMAGE=true` in the backend environment. The default is `false`; PDF text extraction is unchanged.


## Targeted repair notes (November 2026)

- Added `build/icon.ico`, generated from the existing transparent InvoSift logo at multiple Windows icon sizes. The existing Electron BrowserWindow and Electron Builder configuration already point to this resource; the ZIP previously did not contain the referenced `build/` asset, which explains the missing application branding and could also interrupt packaging.
- Made the Windows distribution command explicitly target x64 for both NSIS and portable outputs.
- Recent Extractions now always shows the five most recently created successful/non-processing invoice records, independently of the full History sort selector. The full History view retains its own search and sorting.
- History loading now validates the API response and displays an explicit retryable error state instead of leaving a misleading empty list when the API request fails.

Validation in the supplied non-Windows environment:
- Python regression suite: 16 passed with `PYTHONPATH=backend_src`.
- JavaScript syntax validation: performed where Node.js is available.
- Actual Windows PyInstaller, NSIS and portable packaging, plus Windows Explorer/taskbar icon verification, require a Windows build host and are not claimed as completed here.


## Security and GitHub publishing

- The FastAPI service is forced to bind to `127.0.0.1`; it does not accept a configurable public bind address.
- Electron generates a fresh 256-bit session token on every launch, passes it to the backend through the child-process environment, and injects it into requests to the local API. The token is not exposed through the preload bridge or renderer JavaScript.
- All `/api` routes require that session token. Missing or invalid tokens receive HTTP 401.
- Electron IPC handlers reject messages from frames other than the packaged/local application page. Navigation and new windows are restricted, and external links are limited to approved HTTPS hosts. Invoice preview opening is proxied through the authenticated main process.
- Local Ollama remains local; no cloud AI service or API credential was added.
- `.env`, databases, invoice data, exports, runtime files, and build/dependency artifacts are excluded by `.gitignore`. `.env.example` contains placeholders/configuration only.

### Important security limitation

This is a local desktop-app boundary, not protection against malware or another process running as the same Windows user. A process with equivalent user permissions may be able to inspect or interfere with the app/backend. Do not store highly sensitive credentials in this app under the assumption that the session token prevents same-user compromise.

### Before publishing

1. Review the repository and the exact files being staged (`git status` and `git diff --cached`).
2. Never commit `.env`, real invoice documents, local databases, exports, or customer data.
3. If a credential was ever committed, revoke/rotate it at its provider. Removing it from the latest files or adding it to `.gitignore` does not remove it from Git history.
4. This ZIP did not contain a `.git` directory, so existing Git history could not be inspected or scrubbed. If your separate working repository has history, scan it before making it public.

### Running

Run the project using the existing `RUN-APP.bat` / `npm start` workflow. The Electron shell creates the per-launch API token and starts the backend. Starting the backend by itself without the Electron shell intentionally leaves API routes unauthorized.
