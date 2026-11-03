# LocalRAG

I built LocalRAG because I wanted to ask questions about my own files. It runs
as a website on your computer and uses Ollama or LM Studio to answer questions.
It can read the following formats:

| Format | Notes |
| --- | --- |
| TXT, Markdown | UTF-8 text |
| PDF | Text-based PDFs; page references are preserved |
| DOCX | Paragraphs and tables |
| HTML | Visible page text |
| CSV, XLSX | Cell values and rows |
| PPTX | Slide text |

Scanned PDF and image OCR are not supported yet.

This is still a work in progress, so some parts may be rough.

## What LocalRAG is for

LocalRAG is designed for private, evidence-based questions about a personal
folder of documents. It is a good fit when you want answers with visible
citations without uploading files to a hosted AI service. It is not intended
to be a multi-user server or a replacement for a general web search engine.

## Run it

You need Python 3.11 or newer. Install [Ollama](https://ollama.com/download)
or [LM Studio](https://lmstudio.ai/) if you want to chat with a model.
After cloning the repository, the launcher creates a virtual environment,
installs LocalRAG's document and web dependencies, and starts the app.

### Windows (PowerShell)

```powershell
.\start_localrag.ps1
```

### macOS and Linux

```sh
bash start_localrag.sh
```

To run it manually instead, use the commands below.

#### Windows (PowerShell)

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
py -m pip install -e ".[documents,web]"
py -m localrag.web
```

#### macOS and Linux

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[documents,web]'
python -m localrag.web
```

Open http://127.0.0.1:8000, add some files, and scan for models. LocalRAG shows
models it finds in Ollama or LM Studio. It doesn't download or pick a model for
you. The model panel can download Ollama models, show progress, and cancel a
download. LM Studio models still need to be loaded in LM Studio before scanning.

The web app indexes uploads in a background job with file progress and
cancellation. It streams answers and stores conversations in the browser's local
storage. You can create, rename, delete, and export conversations. Chat history
is local to the browser profile and is not encrypted; clearing the document index
does not clear chat history. Follow-up questions use recent turns for context,
while retrieved document passages remain the evidence for factual answers.

When you chat, LocalRAG sends the question and matching passages from your
files to the model you chose. With Ollama or LM Studio running on your computer,
that stays on your computer. The server starts on `127.0.0.1`, so other devices
can't reach it by default. The API has no authentication. Keep it bound to
`127.0.0.1`; binding to `0.0.0.0` can expose document uploads, chats, model
downloads, and index deletion to other devices on your network. The server
refuses non-loopback hosts unless you pass `--allow-network`; that flag does not
add authentication. Network or multi-user use is not supported.

LocalRAG saves its index and uploaded copies in the platform's application-data
directory (`%LOCALAPPDATA%\LocalRAG` on Windows, `~/Library/Application
Support/LocalRAG` on macOS, or `$XDG_DATA_HOME/LocalRAG` / `~/.local/share/LocalRAG`
on Linux). Uploads can be up to 25 MB per file and 100 MB / 50 files per batch.
Extracted text is limited to 5 million characters per document. **Clear index**
removes the index and those uploaded copies. It doesn't remove the original files
from your computer.

## Search options

By default, LocalRAG uses SQLite FTS5 with BM25-ranked keyword search and a small
English synonym map for common phrasing differences. To use ChromaDB and Ollama
embeddings for semantic retrieval, install the optional backend and embedding
model before starting:

```powershell
py -m pip install -e ".[documents,web,rag]"
ollama pull nomic-embed-text
$env:LOCALRAG_VECTOR_BACKEND = "chroma"
py -m localrag.web
```

The default embedding model is `nomic-embed-text`. You can point LocalRAG at a
different Ollama server or compatible Ollama embedding model by setting
`LOCALRAG_OLLAMA_URL` (base URL, default `http://127.0.0.1:11434`) and
`LOCALRAG_EMBEDDING_MODEL` (default `nomic-embed-text`) before starting the app.
For macOS/Linux shells use `export LOCALRAG_VECTOR_BACKEND=chroma`,
`export LOCALRAG_OLLAMA_URL=http://127.0.0.1:11434`, and
`export LOCALRAG_EMBEDDING_MODEL=nomic-embed-text`. Chat generation can still use
Ollama or LM Studio; Chroma embeddings specifically use the configured Ollama
server. The diagnostics panel reports the configured embedding model and whether
Ollama has it installed.

The Chroma option combines semantic results with SQLite's BM25 keyword results
using reciprocal-rank fusion. SQLite remains the source of truth, so documents
already indexed there remain searchable when you enable Chroma; re-upload them
to add semantic vectors. If Ollama or the embedding model is unavailable, the
hybrid backend falls back to SQLite search instead of failing the upload or
returning stale vectors. Switching embedding models requires re-uploading files
to rebuild their vectors. The default SQLite backend remains keyword search and
may miss passages when a question uses different words from the document.

The default retrieval benchmark is in `tests/test_retrieval_eval.py`. It checks
recall@3 against a small policy and IT corpus; it is a regression check, not a
general language understanding score.

If you installed LocalRAG in the active virtual environment, you can also run
it with `localrag`.

## Troubleshooting

- **No model is detected:** start Ollama or load a model in LM Studio, then
  use the refresh button in the model panel.
- **Answers are empty or unavailable:** confirm that the selected model is
  still loaded and that the provider is listening on its local endpoint.
- **A PDF has no useful text:** it may be scanned or use a layout that needs
  OCR; text extraction alone cannot read those pages.
- **The app cannot start:** run the diagnostics panel and check that the
  virtual environment has the `documents` and `web` extras installed.

## Run the tests

```powershell
py -m pip install -e ".[documents,test,web]"
py -m pytest
```
