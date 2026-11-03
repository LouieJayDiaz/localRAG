# LocalRAG

I built LocalRAG because I wanted to ask questions about my own files. It runs
as a website on your computer and uses Ollama or LM Studio to answer questions.
It can read `.txt`, `.md`, `.pdf`, `.docx`, `.html`, `.csv`, `.xlsx`, and `.pptx`
files. Scanned PDF and image OCR are not supported yet.

This is still a work in progress, so some parts may be rough.

## Run it

You need Python 3.11 or newer. Install [Ollama](https://ollama.com/download)
or [LM Studio](https://lmstudio.ai/) if you want to chat with a model.

### Windows (PowerShell)

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
py -m pip install -e ".[documents,web]"
py -m localrag.web
```

### macOS and Linux

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
English synonym map for common phrasing differences. To use ChromaDB and Ollama's
`nomic-embed-text` model instead, install them and set the backend before starting:

```powershell
py -m pip install -e ".[documents,web,rag]"
ollama pull nomic-embed-text
$env:LOCALRAG_VECTOR_BACKEND = "chroma"
py -m localrag.web
```

The SQLite and ChromaDB indexes are stored separately. If you switch between
them, upload your files again so the selected index has them. SQLite search is
fast local keyword search, not semantic search, so it may miss relevant passages
when a question uses different words from the document. ChromaDB uses Ollama embeddings for semantic search;
Ollama and `nomic-embed-text` must be available while indexing and chatting with
the Chroma backend.

The default retrieval benchmark is in `tests/test_retrieval_eval.py`. It checks
recall@3 against a small policy and IT corpus; it is a regression check, not a
general language understanding score.

If you installed LocalRAG in the active virtual environment, you can also run
it with `localrag`.

## Run the tests

```powershell
py -m pip install -e ".[documents,test,web]"
py -m pytest
```
