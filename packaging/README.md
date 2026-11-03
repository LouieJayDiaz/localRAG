# LocalRAG desktop builds

The supported desktop packaging path is PyInstaller's **onedir** build. It
produces a `dist/LocalRAG` directory on Windows and a `dist/LocalRAG.app`
bundle on macOS. Onedir starts faster and is easier to troubleshoot than a
single compressed executable, especially with pywebview's platform backends.

Build on the target operating system; PyInstaller does not cross-compile:

```powershell
.\scripts\build_desktop.ps1
```

```sh
./scripts/build_desktop.sh
```

The scripts install the web, document, image, pywebview, and PyInstaller
extras, then run `packaging/localrag.spec`. The build does not download, discover, or bundle Ollama/LM Studio models.
Tesseract OCR remains optional; set `LOCALRAG_TESSERACT_DIR` to a directory
containing `tesseract.exe` before building to include it in the Windows bundle.

The executable keeps using LocalRAG's normal per-user data directory. It binds
only to `127.0.0.1` and chooses an available port by default. If pywebview is
not installed or its native backend cannot initialize, the same local server
opens in the default browser; browser fallback remains attached until the
launcher is closed or interrupted with Ctrl+C.

For development, install the optional desktop extra and run the launcher
without building:

```powershell
py -m pip install -e ".[documents,images,web,desktop]"
py run_desktop.py
```

Use `--browser` to force the browser fallback. The existing `run_web.py`,
`localrag.web`, and `start_localrag.*` launchers remain the lightweight web
path and are not replaced by the packaged build.
