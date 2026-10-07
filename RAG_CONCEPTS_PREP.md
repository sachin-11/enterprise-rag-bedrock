# RAG concepts → mere project se jode hue answers

Har sawaal ka format same hai:

1. **Concept (30 sec):** pehle generic definition, short mein.
2. **Mere project mein:** "I used this approach…", code ka exact reference ke saath.
3. **Kyun yahi approach:** reasoning.
4. **Kya use nahi kiya aur kyun:** alternative jo consciously chhoda, ya jo abhi next step hai.

Golden rule: kabhi bhi "humne best practice follow ki" mat bolna. Hamesha bolna
*"maine X kiya kyunki mere case mein Y constraint tha, aur Z ka cost yeh tha."*

> **Bedrock KB ka actual config (AWS CLI se verify kiya, KB `REGMU07TT1`):**
> - KB type: **`MANAGED`** (Quick-create), status ACTIVE.
> - Embedding model: **`embeddingModelType: MANAGED`** — AWS khud choose karta
>   hai, koi model ID expose nahi hota. Interview mein *koi specific model naam
>   (Titan etc.) mat bolna* — bolo "AWS-managed embedding".
> - Chunking: data source pe **koi `chunkingConfiguration` hai hi nahi** —
>   managed KB mein chunking AWS handle karta hai, configurable nahi.
> - Parsing: **`SMART_PARSING`**; image / audio / video extraction **ENABLED**.
> - Vector store: managed (alag OpenSearch collection dikhta nahi).
> - Data source: S3 managed connector, `dataDeletionPolicy: DELETE`.
>
> Yahi asli wajah hai ki maine standalone chunker + OpenSearch path khud banaya —
> managed KB mein in knobs pe control hi nahi hai.

---

## Pipeline ek line mein (sabse pehle yaad rakho)

```
input guardrail → query rewrite (history se "it/that" resolve) → semantic cache lookup
→ (miss) HyDE passage → Bedrock KB retrieve top-20 (tenant + sharing filter ke saath)
→ Cohere rerank 20 → 4 → score < 0.2 drop → cited generation (gpt-5.4-mini, max 1024 tokens)
→ output guardrail → PII redaction → cache store
```

Code: `backend/app/api/chat.py` → `_run_rag_pipeline`.

Do retrieval paths hain codebase mein — yeh baat interview mein strong point hai:
- **Live path:** Bedrock Knowledge Base (managed chunking + embedding + hybrid search).
- **Standalone path (wired nahi hai `/chat/query` mein):** khud ka chunker
  (`chunking_service.py`) → OpenAI embeddings → OpenSearch → dense + BM25 + RRF
  (`retrieval_service.py`). Isse maine woh sab khud implement kiya jo Bedrock
  andar chhupa deta hai — isliye har concept dono angles se explain kar sakta hoon.

---

## 1. RAG kya hai aur fine-tuning se kab better hai

**Concept:** RAG = query time pe external knowledge retrieve karke LLM ke prompt
mein daalna. Fine-tuning = model ke weights mein knowledge/behaviour bake karna.
Rule of thumb: *knowledge* change hoti hai → RAG; *behaviour/format/style*
sikhana hai → fine-tuning.

**Mere project mein:** Pure RAG, koi fine-tuning nahi. Ek multi-tenant SaaS hai
jahan har org apne documents upload karta hai.

**Kyun RAG:**
- **Access control:** mere system mein ek hi org ke andar bhi member A ko doc X
  dikhna chahiye, member B ko nahi (`uploaded_by` / `shared_with` filter,
  `bedrock_kb_service.py::_build_retrieval_filter`). Fine-tuned model mein
  knowledge weights mein hoti hai — usse per-user permission enforce karna
  *impossible* hai. Model jo jaanta hai, wo kisi ko bhi bata sakta hai.
- **Un-share / delete turant effect kare:** admin ne doc un-share kiya → agle
  query se wo retrieve hi nahi hota (aur us tenant ka semantic cache bhi
  invalidate hota hai). Fine-tuning mein "unlearn" karne ke liye retrain karna
  padega.
- **Citations:** har answer `[1][2]` cite karta hai aur UI source excerpt
  dikhata hai. Fine-tuned model source nahi bata sakta.
- **Cost:** har tenant ke liye alag model fine-tune karna economically pagalpan hai.

**Fine-tuning kyun nahi (aur kab karunga):** agar kabhi model ko domain-specific
*format* sikhana ho (jaise HR-policy answers hamesha ek fixed template mein) ya
ek chhota model distill karna ho cost ke liye — tab fine-tuning *RAG ke saath*
use karunga, uski jagah nahi.

---

## 2. RAG pipeline end to end

**Concept:** Ingestion (load → chunk → embed → index) + Query time (query
process → retrieve → post-process → generate).

**Mere project mein:**

*Ingestion:*
1. Admin `POST /documents/upload` → file S3 mein, saath mein `<key>.metadata.json`
   sidecar (`tenant_id`, `uploaded_by`, `shared_with`).
2. `POST /documents/sync-kb` → Bedrock ingestion job (`sync_knowledge_base`) —
   Bedrock khud chunk + embed + index karta hai, aur sidecar metadata har chunk
   pe attach kar deta hai.

*Query time* (upar wali pipeline line). Har stage `@traceable` hai, to LangSmith
mein ek bad answer ko trace karke dekh sakta hoon — kaunse chunks aaye, unke
scores kya the, rerank ke baad kya bacha, LLM ne actually kya dekha.

**Kyun aisa:** pre-retrieval (rewrite, HyDE), retrieval (filtered), post-retrieval
(rerank + threshold), generation (grounded + cited), aur safety layers dono taraf.

**Interview tip:** pipeline bolte waqt *security* zaroor mention karo — tenant
filter retrieval call ke andar hai, retrieval ke baad Python mein nahi. Yeh
differentiator hai.

---

## 3. Chunking strategies — fixed vs semantic vs recursive

**Concept:**
- **Fixed-size:** har N tokens pe kaato (overlap ke saath). Simple, fast, lekin
  sentence/section beech mein toot jaata hai.
- **Recursive:** separators ki hierarchy (`\n\n` → `\n` → `.` → space) se
  kaato jab tak size fit na ho. Structure ko respect karta hai.
- **Semantic:** har sentence embed karo, jahan consecutive sentences ki
  similarity girti hai wahan boundary lagao. Topic-aware, lekin mehenga.

**Mere project mein:** do jagah chunking hai —
- **Live path:** Bedrock managed KB — chunking AWS ke haath mein hai (data source
  pe koi chunking config hi nahi), saath mein `SMART_PARSING` jo document
  structure / tables / images parse karta hai.
- **Standalone path:** maine khud ka **structure-aware, sentence-based chunker**
  likha — `chunking_service.py`. Yeh basically *recursive* ka smarter version hai:
  1. PDF se text nikalte waqt heading detect karta hoon — font size median se
     ≥ 1.15× ya bold → heading (`_extract_pdf_text`). DOCX mein paragraph style
     "Heading N" se.
  2. spaCy sentencizer se sentences — kabhi sentence beech mein nahi tootega.
  3. Sentences ko ~400 tokens tak group karta hoon; agar **naya section shuru ho
     raha hai aur current chunk already target ka 50% hai**, to wahin break —
     taaki ek chunk do alag topics mix na kare.
  4. 15% overlap, lekin *sentence granularity* pe (aadha sentence overlap nahi).
  5. Har chunk ke saath `section_heading`, `char_start`, `char_end` store —
     citation aur debugging ke liye.

**Kyun yeh approach:** enterprise documents (HR policy, resumes, SOPs) mein
headings hi natural topic boundaries hain. Semantic chunking jo embeddings se
dhoondta, wo document ka author already headings se bata chuka hai — free mein.

**Semantic chunking kyun nahi:**
- Ingestion pe har sentence embed karna padta → cost aur time kaafi badh jaata.
- Threshold pe sensitive aur thoda non-deterministic — same doc, alag model
  version, alag chunks. Debug karna mushkil.
- Headings wala signal mere docs ke liye utna hi accha tha.
Agar unstructured docs aayein (chat transcripts, emails jinmein headings nahi),
tab semantic chunking try karunga.

**Pure fixed-size kyun nahi:** policy clause beech mein toot jaata, aur uski
condition ("…unless approved by manager") doosre chunk mein chali jaati.

---

## 4. Chunk size select karne ka criteria

**Concept:** chunk chhota → precise retrieval lekin context kam (answer adhoora);
bada → context zyada lekin embedding "diluted" (ek vector mein kai topics), aur
prompt mein kam chunks fit honge. Criteria: document type, typical question ka
answer kitna bada hai, embedding model ki sweet spot, aur kitne chunks prompt
mein jaayenge.

**Mere project mein:** ~400 tokens, 15% overlap (`DEFAULT_TARGET_TOKENS`,
`DEFAULT_OVERLAP_RATIO`).

**Kyun 400:**
- Mere docs mein typical answer ek paragraph ya ek policy clause hai — 400 tokens
  ek poora clause + uska context aaram se pakadta hai.
- Rerank ke baad top **4** chunks LLM ko jaate hain → ~1,600 tokens context.
  Chhota, focused prompt — cost bhi kam, aur lost-in-the-middle ka risk bhi nahi.
- Overlap 15% taaki boundary pe pada sentence dono chunks mein mile.
- Token count spaCy tokens se approximate hai, exact LLM tokenizer nahi — chunk
  *sizing* ke liye utna accuracy kaafi hai, aur ek heavy tokenizer dependency
  bachti hai.

**Honest part (yeh bolna, interviewer impress hota hai):** 400 maine reasoning se
choose kiya, eval sweep se tune nahi kiya. Sahi tareeka hai 256 / 400 / 800 pe
ragas `context_recall` aur `context_precision` compare karna — mera eval harness
ready hai (section 10), bas test set bada karna hai.

---

## 5. Embedding model kaise choose karte ho

**Concept:** criteria — retrieval quality (MTEB retrieval benchmark, lekin apne
data pe test zyada important), dimension (storage + latency), cost per token,
max input length, multilingual zaroorat, aur hosting (API vs self-host / data
residency).

**Mere project mein:**
- Standalone path + semantic cache: OpenAI **`text-embedding-3-small`**, 1536 dims
  (`retrieval_service.py`).
- Live path: Bedrock managed KB — `embeddingModelType: MANAGED`, yaani AWS khud
  model choose aur maintain karta hai (model ID expose nahi hota). Tradeoff:
  zero ops, lekin model swap / benchmark nahi kar sakta.

**Kyun `3-small`:** cost bahut kam, quality retrieval ke liye solid, aur maine
already OpenAI generation ke liye use kiya hua tha — ek vendor kam.

**Ek important engineering point:** `EMBEDDING_MODEL_ID` aur
`EMBEDDING_DIMENSION` ek hi jagah defined hain, aur comment mein likha hai ki
ingestion aur query **same model** use karein — warna vector search *silently*
degrade hota hai (koi error nahi aata, bas results bakwaas). Interviewer ko yeh
bolna: "embedding model badalna = poora index re-embed karna."

**Real measurement jo kiya:** semantic cache ke liye `3-small` pe cosine scores
khud measure kiye — "vacation days?" vs "sick days?" = **0.71**, aur ek genuine
paraphrase pair = **0.70**. Isse samjha ki embedding similarity "same topic" aur
"same question" mein farq nahi karti — isliye cache threshold 0.93 rakha.

**`3-large` kyun nahi:** 2× dimension, zyada cost, aur mera bottleneck embedding
quality nahi, retrieval precision tha (eval se pata chala — section 10). Wahan
reranker zyada impact deta hai.

---

## 6. Vector DB — Pinecone vs Weaviate vs pgvector

**Concept:**
- **Pinecone:** fully managed, scale easy, lekin data apne cloud ke bahar,
  vendor lock-in, cost.
- **Weaviate:** open-source, built-in hybrid search + modules, self-host ya cloud;
  ops ka bojh.
- **pgvector:** Postgres extension — agar Postgres already hai to sabse simple,
  SQL joins + metadata ek jagah; bahut bade scale pe tuning chahiye.

**Mere project mein:** Bedrock Knowledge Base ka **managed vector store** (live),
aur standalone path ke liye **OpenSearch** (HNSW index, `cosinesimil`,
`ensure_index_exists`).

**Kyun Bedrock KB:**
- Poora stack AWS pe hai (Cognito, S3, DynamoDB, SES) — data AWS ke bahar nahi
  jaata, IAM se hi access control.
- Ingestion pipeline (chunk + embed + index) managed — S3 mein file daalo, sync karo.
- **Metadata filtering native** — mera poora tenant isolation isi pe tika hai:
  `equals`, `andAll`, `orAll`, `listContains` filter vector store khud retrieval
  ke andar evaluate karta hai.
- Hybrid search andar hi built-in.

**pgvector kyun nahi:** mere stack mein Postgres hai hi nahi (chat history
DynamoDB mein hai). Sirf vectors ke liye ek RDS chalana + ingestion pipeline khud
maintain karna — extra ops for no gain at this scale.

**Pinecone / Weaviate kyun nahi:** data ek aur vendor ke paas jaata, alag auth,
alag billing. Enterprise customer ke liye "aapka data AWS account ke bahar nahi
jaata" ek selling point hai.

**Tradeoff jo accept kiya (zaroor bolna):** managed = kam control. Hybrid weights
tune nahi kar sakta, chunking aur embedding model dono AWS-managed hain (configure
hi nahi hote), aur ek real gotcha mila —
Bedrock KB empty `STRING_LIST` (`shared_with: []`) pe **poore document ki
ingestion fail** kar deta hai. CloudWatch logs se pakda, fix: key omit karo.
Isiliye maine OpenSearch wala standalone path bhi rakha — agar control chahiye to
switch kar sakte hain.

---

## 7. Cosine vs dot product vs euclidean

**Concept:**
- **Cosine:** sirf angle (direction) dekhta hai, magnitude ignore. Text
  embeddings ke liye standard.
- **Dot product:** angle + magnitude. Agar vectors normalized hain to
  cosine ke barabar ranking deta hai, aur compute mein sasta.
- **Euclidean (L2):** absolute distance. Normalized vectors pe ranking cosine
  jaisi hi; un-normalized pe magnitude se biased.
- Golden rule: **wahi metric use karo jis pe embedding model train hua hai.**

**Mere project mein:** cosine everywhere —
- OpenSearch index: `space_type: "cosinesimil"`, HNSW.
- Semantic cache: query embedding vs cached embeddings pe cosine, threshold 0.93.

**Kyun cosine:** OpenAI embeddings unit-length normalized aate hain, to cosine =
dot product ranking. Cosine choose kiya kyunki wo *interpretable* hai (−1 to 1) —
cache threshold 0.93 jaisa fixed cutoff tabhi meaningful hai jab score bounded ho.

**Dot product kyun nahi explicitly:** normalized vectors pe result same hai; sirf
micro-optimization milta. Euclidean ka koi faayda nahi tha yahan.

---

## 8. Hybrid search — dense + sparse combine kyun

**Concept:** Dense (embeddings) meaning samajhta hai ("leave" ≈ "time off"),
lekin exact tokens miss karta hai — IDs, naam, error codes, acronyms. Sparse
(BM25) exact keywords pakadta hai lekin synonyms nahi. Dono combine → dono ki
kamzori cover.

**Mere project mein:**
- **Live:** Bedrock managed KB **hamesha hybrid search** chalata hai internally
  (code comment mein noted — managed KB pe `overrideSearchType` hi nahi hota).
- **Standalone:** maine khud implement kiya — `retrieve_dense` (k-NN) +
  `retrieve_sparse` (BM25 `match`) → `reciprocal_rank_fusion` with **k = 60**.
  Dono queries mein tenant filter `bool.filter` mein hai.

**RRF kyun (score average kyun nahi):** BM25 score unbounded hai (0–30 kuch bhi),
cosine 0–1. Inko directly add/average karna apples-to-oranges hai. RRF sirf
*rank* use karta hai: `score = Σ 1/(k + rank)`. k = 60 original paper (Cormack
2009) ka default — rank 1 vs rank 2 ka gap ~1.6% tak dampen karta hai, to ek list
ka #1 automatically dominate nahi karta. Jo doc dono lists mein high hai, wo upar.

**Real example mere eval se:** "What is the candidate's name?" pe retrieval
**empty** aaya. Naam jaisi exact-entity query pe pure semantic search weak hota
hai — yahi case hai jahan sparse matter karta hai.

**Kya nahi kiya:** learned sparse (SPLADE) ya weighted fusion tuning — RRF bina
tuning ke robust hai, aur weights tune karne ke liye labeled data chahiye jo
abhi chhota hai.

---

## 9. Reranking kya hai aur kyun zaroori hai

**Concept:** Bi-encoder (embedding) query aur doc ko *alag-alag* encode karta hai
— fast, lekin coarse. Cross-encoder reranker query + doc ko *saath mein* padhta
hai — bahut accurate, lekin slow. Isliye: bi-encoder se broad recall (top 20–50),
cross-encoder se precise top-N.

**Mere project mein:** Cohere **`rerank-v3.5`**, **20 → 4**, phir
`MIN_RELEVANCE_SCORE = 0.2` se neeche wale drop (`chat.py`).

**Design decisions jo explain kar sakta hoon:**
- **Rerank original (rewritten) question pe hota hai, HyDE passage pe nahi.**
  Retrieval HyDE se hota hai (recall ke liye), lekin final relevance asli sawaal
  se judge hoti hai — agar HyDE passage galat direction mein gaya to reranker
  correct kar deta hai.
- **Threshold rerank ke *baad*:** KB ka score aur Cohere ka score comparable
  nahi hain, isliye cutoff sirf Cohere score pe.
- **Irrelevant chunk drop karna = security bhi:** har extra chunk prompt-injection
  surface hai.
- **Graceful degradation:** Cohere down → top_n original order mein return.
  Quality girti hai, retrieval tootta nahi.
- `rerank_with_cohere` generic hai (TypeVar) — Bedrock aur OpenSearch dono ke
  results pe kaam karta hai.

**Honest part:** 0.2 conservative chosen hai, labeled eval pe tune nahi kiya
(code comment mein bhi likha hai).

**Kya nahi kiya:** LLM-as-reranker (mehenga + slow), ya self-hosted cross-encoder
(bge-reranker) — GPU infra maintain karna padta. Cohere API = zero ops.

---

## 10. RAG evaluation — metrics kya hain

**Concept:** do hisse alag evaluate karo —
- **Retrieval:** context precision (jo laaye wo relevant tha?), context recall
  (jo chahiye tha wo laaye?), MRR / hit@k.
- **Generation:** faithfulness (answer context se supported hai ya
  hallucinated?), answer relevancy (sawaal ka jawab diya?).
- Offline (fixed test set) + online (user feedback, production signals).

**Mere project mein:**
- **Offline:** `scripts/run_eval.py` — `eval/test_qa.json` (question +
  ground truth + expected context) ko real `/chat/query` pe chalata hai, **ragas**
  se 4 metrics score karta hai (faithfulness, answer relevancy, context precision,
  context recall), result `eval/results_<timestamp>.json` mein. Admin dashboard
  pe trend + per-question table dikhta hai (`eval_service.py`).
- **Online:** 👍/👎 feedback LangSmith run pe attach hota hai; weekly
  feedback-rate trend; aur **knowledge gaps** panel — jin queries pe
  `source_count = 0` aaya (KB mein us topic pe kuch hai hi nahi). Yeh
  phrasing-independent signal hai, answer text mein "I don't know" grep karne se
  better.

**Real numbers (honestly bolo — yeh strength hai, weakness nahi):** latest run
chhota tha (3 questions, ek resume pe): faithfulness **~0.89**, lekin context
precision **~0.07**. Matlab: **generation grounded hai, bottleneck retrieval hai.**
Isse pata chala next kaam chunking/retrieval pe karna hai, prompt pe nahi. Yahi
eval ka point hai — guess nahi, measure.

**Kya nahi kiya abhi:** CI mein har PR pe eval (judge LLM calls mehenge hain,
isliye developer-triggered rakha); aur test set bada nahi hai — 50–100 questions
chahiye statistically meaningful hone ke liye.

---

## 11. Context stuffing problem

**Concept:** "Zyada context = better answer" galat hai. Bahut saare chunks
daalne se: cost + latency badhti hai, irrelevant chunks model ko distract karte
hain (answer quality *girti* hai), aur injection surface badhta hai.

**Mere project mein — har level pe cap:**
- Retrieve 20, lekin LLM ko sirf **4** (rerank).
- 4 mein se bhi score < 0.2 wale **drop**.
- Chat history: sirf last **3 turns** verbatim (`MAX_HISTORY_TURNS`), purane turns
  ek **rolling LLM summary** mein fold (`summarize_history`) — na poori history
  dump, na purana context lost.
- Answer `max_tokens = 1024`.
- Query rewrite pehle hi standalone question bana deta hai, to retrieval ke liye
  history bhejni hi nahi padti.

**Kya nahi kiya:** contextual compression (har chunk se sirf relevant sentences
nikalna LLM se) — ek aur LLM call per chunk; 4 × 400 tokens pe zaroorat nahi lagi.

---

## 12. Lost in the middle

**Concept:** (Liu et al., 2023) LLMs lambe context ke shuru aur end ki info
achhe se use karte hain, beech wali ignore kar dete hain. Fix: kam chunks, best
chunks start/end pe (reorder), ya compression.

**Mere project mein:** problem ko **design se avoid** kiya, reorder hack se nahi:
- Sirf 4 chunks, ~1,600 tokens — itne chhote context mein "middle" hai hi nahi
  practically.
- Chunks **rerank score order** mein jaate hain → sabse relevant `[1]` pe, shuru mein.
- Har chunk numbered + doc name ke saath (`[1] (doc: …)`), aur model ko inline
  cite karna padta hai — explicit numbering attention ko anchor karti hai.

**Reorder (LongContextReorder — best ko start + end pe) kyun nahi:** 4 chunks pe
koi measurable faayda nahi. Agar kabhi top-N 10–15 karna pada (lambe answers ke
liye), tab add karunga.

---

## 13. Naive vs Advanced vs Modular RAG

**Concept:**
- **Naive:** query → embed → top-k → stuff → generate.
- **Advanced:** pre-retrieval (query rewrite, HyDE, expansion) + post-retrieval
  (rerank, filter, compress) optimizations.
- **Modular:** pipeline ke components swappable modules hain, routing /
  conditional flow ho sakta hai (e.g. cache hit pe short-circuit, alag
  retrievers).

**Mere project mein — Advanced RAG, modular structure ke saath:**
- *Pre-retrieval:* query rewrite (pronoun resolution, injection-hardened prompt),
  HyDE.
- *Post-retrieval:* Cohere rerank + relevance threshold + injection scan.
- *Modular:* har stage alag service file, independently traced aur testable;
  **conditional flow** — guardrail block pe early exit, cache hit pe HyDE /
  retrieve / generate sab skip; **retriever swappable** — Bedrock KB ya
  OpenSearch hybrid, aur reranker dono pe same kaam karta hai.
- Har LLM stage ka fallback: rewrite fail → original query; HyDE fail → standalone
  query; rerank fail → original order.

**Agentic RAG kyun nahi (follow-up aa sakta hai):** query routing / multi-hop
agent har query pe kai LLM calls = latency + cost + unpredictability. Mere users
ke sawaal mostly single-hop hain ("leave policy kya hai"). Agents maine wahan use
kiye jahan zaroorat thi — admin co-pilot aur error watchdog — main query path pe
nahi.

---

## 14. HyDE — Hypothetical Document Embedding

**Concept:** user ka sawaal chhota hota hai aur "question jaisa" dikhta hai,
document "answer jaisa". Inke embeddings door ho sakte hain. HyDE: LLM se ek
*fake answer passage* likhwao, **usko** embed karke search karo — answer-to-answer
similarity zyada hoti hai.

**Mere project mein:** `query_service.py::generate_hyde_passage`
- 3–4 sentence ka passage, `temperature = 0.3` (thoda variety, zyada creative nahi).
- Passage user ko **kabhi nahi dikhta**, sirf retrieval ke liye.
- Sirf **cache miss** pe chalta hai — cache hit pe ye LLM call bachti hai.
- Fail ho to `None` → standalone query se retrieve (graceful fallback).
- Prompt injection-hardened: question ko "data" treat karta hai, instruction nahi.

**HyDE ka risk aur maine kaise handle kiya (yeh zaroor bolna):** HyDE passage
hallucinated hota hai — agar LLM galat direction mein likhe to retrieval bhi
galat jagah jaata hai. Mitigation: **rerank asli standalone question pe hota hai,
HyDE pe nahi.** HyDE recall badhata hai, reranker precision wapas laata hai.

**Tradeoff:** har cache-miss query pe ek extra LLM call (latency + cost). Mini
model use karke cost kam rakhi.

**Kya nahi kiya:** multiple HyDE passages average karna (original paper jaisa) —
N gunna cost. Aur HyDE ka impact eval se A/B measure nahi kiya abhi — next step:
same test set pe HyDE on vs off, context recall compare.

---

## 15. Parent-child chunking

**Concept:** do size ke chunks — **chhote child chunks** (e.g. 100–200 tokens)
search ke liye (precise embedding), lekin match hone pe uska **bada parent chunk**
(e.g. 1000+ tokens / poora section) LLM ko bhejo (poora context). "Small to
search, big to read."

**Mere project mein:** **abhi use nahi kiya** — honestly bolo.

**Kyun nahi (abhi):**
- Mera chunker already **section-aware** hai — 400 tokens ka chunk ek section ke
  andar rehta hai aur `section_heading` carry karta hai. Mere docs (policies,
  resumes) ke liye precision vs context ka balance ek size pe acceptable tha.
- Live path managed Bedrock KB hai — wahan custom parent lookup ke liye extra
  storage + ID mapping chahiye.
- Pehle eval se pata karna tha ki problem kahan hai — aur eval ne bataya
  bottleneck *context precision* hai, jo parent-child directly address karta hai.

**Kaise add karunga (follow-up ke liye ready rakho):** mere chunker mein
`section_heading`, `char_start`, `char_end` already stored hain — to "parent" =
same document ka same section. Chhote child chunks index karo, hit hone pe
`char_start`/`char_end` ke basis pe section ka bada span nikal ke LLM ko do.
Bedrock side pe **hierarchical chunking** strategy yahi kaam built-in karti hai,
lekin mera KB `MANAGED` type hai jismein chunking configurable nahi — uske liye
KB ko non-managed (apna vector store + chunking config) type mein recreate karna
padega. Ya phir mera standalone OpenSearch path, jahan chunking poori meri hai.
Dono case mein eval pe before/after compare.

---

## Cheat sheet — ek nazar mein

| Concept | Kya use kiya | Kya nahi kiya / kyun |
|---|---|---|
| RAG vs FT | Pure RAG | FT per-user permissions & un-share enforce nahi kar sakta |
| Chunking | Heading-aware, sentence-based (spaCy), section boundary pe break | Semantic: ingestion cost, headings already free signal |
| Chunk size | ~400 tokens, 15% overlap | Eval sweep abhi nahi kiya — harness ready hai |
| Embedding | `text-embedding-3-small` (1536) + Bedrock AWS-managed embedding | `3-large`: bottleneck retrieval precision tha, embedding nahi |
| Vector DB | Bedrock KB managed (+ OpenSearch HNSW standalone) | pgvector: Postgres stack mein nahi; Pinecone: data AWS ke bahar |
| Similarity | Cosine (`cosinesimil`, cache 0.93) | Normalized vectors pe dot = same ranking |
| Hybrid | Bedrock built-in; khud ka dense + BM25 + RRF (k=60) | Weighted fusion: labeled data chahiye |
| Rerank | Cohere v3.5, 20 → 4, min score 0.2, original query pe | Self-hosted cross-encoder: GPU ops |
| Eval | ragas 4 metrics + 👍/👎 + knowledge gaps | CI eval: judge cost; test set chhota |
| Context stuffing | 4 chunks, history 3 turns + summary, max 1024 | Contextual compression: extra LLM call |
| Lost in middle | Kam chunks, relevance order, numbered citations | Reorder: 4 chunks pe faayda nahi |
| RAG type | Advanced + modular (cache short-circuit, swappable retriever) | Agentic: latency; agents admin side pe use kiye |
| HyDE | Yes, cache miss pe, T=0.3, fallback | Multi-passage HyDE: N× cost; A/B pending |
| Parent-child | Nahi (abhi) | Section metadata already stored → next step |
