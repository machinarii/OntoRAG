# Visual retrieval and FoundYou

OntoRAG now indexes local figures referenced by document chunks, retrieves images
with SigLIP, optionally reranks the shortlist with FoundYou, and supplies the
matched source passages to the existing RAG pipeline. Text search and visual
search run concurrently and combine through reciprocal-rank fusion. Original
OCR, captions, descriptions, file names and chunk provenance remain intact.

FoundYou is an **object-instance retrieval and segmentation model**, not a text
embedding model or a face-recognition service. Its upstream large-gallery
retrieval example uses a SigLIP shortlist followed by prompted FoundYou scoring.
This integration implements that retrieval path. It does not introduce a video
crawler, face recognition, segmentation-mask output, or a WebUI image picker.

## Install the model worker

Use a dedicated environment. The ordinary API needs no torch or transformers
installation. Install OntoRAG's API dependencies in the worker environment and
then the tested model dependencies:

```bash
pip install -e '/path/to/OntoRAG[api]'
pip install -r /path/to/OntoRAG/requirements-visual-worker.txt

git clone https://github.com/ga1i13o/FoundYou.git /path/to/FoundYou
git -C /path/to/FoundYou checkout d54bfecdf2709cfc850e6f4ed5bb9909d7f92a50
mkdir -p /path/to/FoundYou/pretrain
```

Install the [published FoundYou checkpoint](https://drive.google.com/file/d/1o_P5myXJiXH9wOhl95YQZ179xUgkIwhk/view)
as `/path/to/FoundYou/pretrain/foundyou.pth` and the
[SAM2 small backbone](https://dl.fbaipublicfiles.com/segment_anything_2/072824/sam2_hiera_small.pt)
as `/path/to/FoundYou/pretrain/sam2_hiera_small.pt`. The tested FoundYou checkpoint
SHA256 is `9a56b19d7ff25889ee8f1a0db7b9118151d8c1c4ed272069184dad8e9214adf0`.
The checkpoint contains trained adapters and retrieval layers only; frozen
weights come from the SAM2 backbone. Missing trained weights fail startup.

Set `VISUAL_WORKER_API_KEY` in both environments to the same private value. Start
one model worker process:

```bash
python -m ontorag.visual.worker \
  --siglip-model google/siglip-base-patch16-224 \
  --siglip-revision 7fd15f0689c79d79e38b1c2e2e2370a7bf2761ed \
  --foundyou-repo /path/to/FoundYou \
  --checkpoint /path/to/FoundYou/pretrain/foundyou.pth \
  --device cpu
```

For NVIDIA deployment install the matching torch/torchvision CUDA wheels and use
`--device cuda`. The worker defaults to `127.0.0.1:9630`; change `--host` only when
needed for a private network deployment. All routes require the shared bearer
key. Inference is serialized per process, including cancellation cleanup, and
the launcher caps concurrent HTTP requests at eight. CPU inference defaults to
two torch threads (`--cpu-threads` changes it), preventing thread oversubscription. The main API retains its
normal authentication. No public image URLs or filesystem paths are accepted in
worker requests. Images travel as bytes to the configured worker.

The immutable SigLIP revision pins both weights and preprocessing; the worker
uses the saved slow processor explicitly. A changed embedding space or dimension
requires rebuilding the visual index. FoundYou reranking scores are not stored
in that index, so replacing its checkpoint does not require re-embedding.
Omit both `--foundyou-repo` and `--checkpoint` for SigLIP-only operation, and send
`visual_rerank=false` for image-reference RAG queries (`rerank=false` on
`/visual/search`). Requesting FoundYou without configured weights is an error,
not a silent fallback.

## Enable indexing

In the API environment:

```dotenv
ENABLE_VISUAL_SEARCH=true
VISUAL_WORKER_URL=http://127.0.0.1:9630
VISUAL_WORKER_API_KEY=your-shared-private-value
```

The SDK equivalent is `OntoRAG(enable_visual_search=True, ...)`, followed by
`await rag.initialize_storages()`. The worker URL/key use the environment.

Indexing follows chunk upsert/delete/drop automatically. Figures must have local
sidecar assets and drawing references in their chunks, normally created by the
existing image-analysis ingestion option `i`. Missing external images represented
by empty sidecar paths are skipped because no pixels exist. Text-only chunks are
unaffected. Supported assets: PNG, JPEG and WebP, up to 4 MiB and 20 megapixels.
Malformed, oversized or unreadable assets fail indexing rather than falsely
reporting a complete index. Remote sidecar locations are not supported yet.

For an existing corpus, call authenticated `POST /visual/rebuild` or
`await rag.arebuild_visual_index()`. Rebuild takes the same exclusive reservation
as retrieval maintenance, rejects concurrent ingestion/scans/deletes, and leaves
the index unavailable if interrupted. Retry rebuild to recover. Pending
processing requests deferred by the maintenance fence are driven after release.
Enabling visual search does not retroactively create image-analysis chunks for
documents ingested without them; reprocess those documents with option `i` first.

The workspace-local `visual.sqlite3` contains normalized vectors and links to
chunks, not image bytes. It uses an exact cosine scan in batches of 512 to bound
working memory. This is suitable for document figure galleries; it is not an ANN
index for hundred-million-image collections. API workers must share a persistent
local working directory on one host, as with the lexical index. Source chunks
are rehydrated and version/date-filtered before candidate images are sent for
reranking and again after inference. Deleted chunks cannot return through this
index. Changed asset content is detected by SHA256 and requires re-indexing.

## Search and answer

Reference boxes are normalized `[x1, y1, x2, y2]` coordinates in the displayed,
EXIF-oriented image. Omit the box to use the full image. One to four reference
views of the **same object** are supported. SigLIP averages normalized embeddings
of the reference crops; FoundYou receives full reference images and box prompts.

```python
import base64
from pathlib import Path
import httpx

image = base64.b64encode(Path("object.png").read_bytes()).decode("ascii")
references = [{"image": image, "box": [0.1, 0.1, 0.9, 0.9]}]
# Add the normal OntoRAG API authentication header when configured.
with httpx.Client(base_url="http://localhost:9621", timeout=120) as client:
    matches = client.post("/visual/search", json={
        "references": references,
        "top_k": 40,
        "rerank": True
    })
    matches.raise_for_status()
    print(matches.json()["chunks"])

    answer = client.post("/query", json={
        "query": "What maintenance does this component require?",
        "mode": "mix",
        "enable_visual": True,
        "visual_references": references,
        "visual_top_k": 40,
        "visual_rerank": True,
        "include_references": True,
        "verify_answer": True
    })
    answer.raise_for_status()
    print(answer.json())
```

For text-to-image search, send `{"query":"red centrifugal pump"}` to
`/visual/search`, or `enable_visual=true` without references to a RAG query.
FoundYou is skipped in that case: it requires a prompted image. The ordinary
`/query/data` and `/query/stream` endpoints accept the same visual query options.
`/visual/search` searches only figures; RAG queries fuse visual and text candidates.

The main server's default raw request ceiling is 1 MiB, including base64 overhead.
For larger or multiple references, explicitly configure an appropriate
`MAX_REQUEST_BODY_BYTES` (for example `25165824` for four maximum-size references).
The per-image and reference-count limits still apply. No server body-limit
settings are changed automatically by enabling this feature.

Results carry `visual_matches` with drawing ID, cosine `visual_score`, and
`foundyou_score` when reranked. These are ranking signals, not calibrated
probabilities or proof of identity. RAG generation uses the matched passages'
OCR/descriptions and source evidence; it does not send the user's raw image to
the answering LLM. Citation verification checks those passages, not visual
identity. Empty or irrelevant galleries require corpus-specific evaluation and
threshold policy before using the output for consequential decisions.

SDK calls:

```python
param = QueryParam(enable_visual=True, visual_references=references, visual_top_k=40)
chunks = await rag.avisual_search("", param)
data = await rag.aquery_data("What maintenance does this component require?", param)
```

## Verification and evaluation

Offline tests cover index isolation/model identity, chunk lifecycle, stale assets,
normalized boxes, path boundaries, worker authentication and body limits, cancelled
inference serialization, SDK query paths (`naive`, `mix`, `local`), and HTTP routes.

A real CPU checkpoint smoke test was run with torch 2.8.0 / torchvision 0.23.0 /
transformers 4.57.6 on Linux aarch64, using the pinned models above. Image, text,
and reference embeddings were finite normalized 768-vectors; FoundYou returned a
finite candidate score. This is a compatibility test, not an accuracy benchmark.
Repeat it with:

```bash
ONTORAG_TEST_FOUNDYOU_REPO=/path/to/FoundYou \
ONTORAG_TEST_FOUNDYOU_CHECKPOINT=/path/to/FoundYou/pretrain/foundyou.pth \
python -m pytest tests/visual/test_model_smoke.py --run-integration -q
```

The existing [retrieval benchmark](RetrievalQuality.md#evaluation) accepts visual
query options per case. Label the expected source chunk IDs/files, include hard
negative objects and visually similar but different components, and compare
SigLIP-only against FoundYou reranking. No quality or GPU throughput improvement
is claimed from the smoke test.

Implementation references: [FoundYou repository and retrieval example](https://github.com/ga1i13o/FoundYou),
[SigLIP model documentation](https://huggingface.co/docs/transformers/model_doc/siglip).
FoundYou code and model dependencies remain separately installed; this repository
does not vendor their source or redistribute their checkpoints.
