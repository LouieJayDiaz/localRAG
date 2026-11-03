# LocalRAG

LocalRAG is a thing i made so i can ask questions about my own files without
uploading them to some random website. it runs on your computer and talks to
Ollama or LM Studio.

it is still being worked on. it is not some huge perfect company app lol.

## what it can read

| file | what happens |
| --- | --- |
| TXT, Markdown | reads the text |
| PDF | reads text PDFs and keeps page labels |
| DOCX | reads paragraphs and tables |
| HTML | gets the visible text |
| CSV, XLSX | reads rows and cells |
| PPTX | reads slide text and tables |
| PNG, JPEG, GIF, BMP, TIFF, WebP | gets image info and OCR text |

for image OCR, install the `images` extra. it uses Pillow and pytesseract.
LocalRAG tries to find Tesseract on Windows by itself, which is nice. if it
still cannot find it, set `LOCALRAG_TESSERACT_PATH` to the path of
`tesseract.exe`.

## quick start

### Windows

open PowerShell in this folder and run:

```powershell
.\start_localrag.ps1
```

### macOS or Linux

```sh
bash start_localrag.sh
```

those scripts make a `.venv`, install the stuff, and start the app. so you
dont have to remember every command.

you need Python 3.11 or newer. for actually chatting, you also need
[Ollama](https://ollama.com/download) or
[LM Studio](https://lmstudio.ai/). LocalRAG does not magically download a
chat model for you.

## desktop app mode

you can run it in a normal app-looking window instead of a browser:

```powershell
py -m pip install -e ".[documents,images,web,desktop]"
py run_desktop.py
```

it uses pywebview if it works. if not, it opens your normal browser. still
local either way.

you can also make a Windows build or a macOS app:

```powershell
.\scripts\build_desktop.ps1
```

```sh
./scripts/build_desktop.sh
```

the build needs to happen on the same kind of computer you want to run it on.
PyInstaller does not cross-build. the output does not include Ollama, LM
Studio, or giant AI models.

## running it manually

if the scripts are being weird, these are the longer commands.

### Windows

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
py -m pip install -e ".[documents,images,web]"
py -m localrag.web
```

### macOS or Linux

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[documents,images,web]'
python -m localrag.web
```

then open http://127.0.0.1:8000.

## models

LocalRAG can see models from Ollama and LM Studio. start one of them, load a
model, then press refresh in the app.

the model panel can download Ollama models and show download progress. LM
Studio models need to already be loaded in LM Studio.

### semantic search without Ollama embeddings

the normal search uses SQLite keyword search, and honestly that is the easiest
option. if you want semantic search without running an Ollama embedding model,
you can use the optional local ONNX backend:

```powershell
py -m pip install -e ".[documents,images,web,local-embeddings]"
$env:LOCALRAG_VECTOR_BACKEND = "chroma"
$env:LOCALRAG_EMBEDDING_PROVIDER = "fastembed"
py -m localrag.web
```

the embedding model downloads the first time and then runs on your computer.
chat still needs Ollama or LM Studio. changing embedding providers means you
should re-upload your files so the vectors are made with the same model.

## privacy stuff

LocalRAG does not have telemetry or a hosted AI service. your files stay on
your computer and the server starts on `127.0.0.1`.

the API has no login. do not bind it to `0.0.0.0` unless you really know what
you are doing, because that can let other devices on your network upload files,
chat, download models, or delete the index. network and multi-user use is not
supported.

chat history is saved in your browser's local storage. it is not encrypted.
clearing the document index does not clear the chat history.

LocalRAG keeps its data here:

- Windows: `%LOCALAPPDATA%\LocalRAG`
- macOS: `~/Library/Application Support/LocalRAG`
- Linux: `$XDG_DATA_HOME/LocalRAG` or `~/.local/share/LocalRAG`

uploads can be 25 MB each, with a 100 MB / 50 file batch limit. extracted text
has a 5 million character limit per document.

## search details

the default search is SQLite FTS5 with BM25 ranking and a small synonym list.
the optional Chroma mode combines keyword and semantic results.

the retrieval test is here:

```powershell
py -m pytest tests/test_retrieval_eval.py
```

it checks recall on a small test collection. it is not claiming LocalRAG is
an all-knowing genius.

## troubleshooting

- **no model shows up:** start Ollama or load a model in LM Studio, then press
  refresh.
- **answers are empty:** check that the model is still loaded and the provider
  is running.
- **PDF has no useful text:** it might be scanned. OCR currently works for
  uploaded image files, not PDF pages.
- **image OCR does not work:** install `.[images]` and Tesseract. LocalRAG
  finds common Windows installs, or use `LOCALRAG_TESSERACT_PATH`.
- **the app will not start:** make sure the `documents` and `web` extras are
  installed.

## tests

```powershell
py -m pip install -e ".[documents,images,test,web]"
py -m pytest
```

there are currently tests for ingestion, searching, chat, the web API, image
OCR handling, and the desktop launcher.

## license / status

this is a work in progress project. things might break. if you find a bug,
feel free to open an issue with what you did and what happened.
