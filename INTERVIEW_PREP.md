# Interview prep — enterprise-rag-bedrock

Personal notes (Hinglish): maine kya banaya, waise hi kyun banaya, konse
tradeoffs consciously liye, aur actually mein kya galat hua raaste mein.
Har section apne aap mein complete hai — agar interviewer koi ek thread
pakड़ le to bhi answer ready rahe.

---

## 30-second pitch

Ek multi-tenant RAG SaaS, AWS Bedrock pe. Har organization ek Cognito
group hai. Har document-sharing decision *vector store ki apni retrieval
query ke andar* enforce hota hai, app-layer check ke through baad mein
nahi. Do chhote LLM agents system ki apni health watch karte hain — ek
jisse admin sawaal puchh sakta hai, aur ek jo automatically trigger hota
hai jaise hi koi real query fail hoti hai.

---

## 1. Multi-tenancy: tenants ke liye Cognito groups

**Kya hai:** har org = ek Cognito group (org ka slug). Admin rights ek
*doosra*, alag group hai har org ke liye (`{org}-admins`) — koi user ya
to us companion group ka member hai ya nahi.

**`tenant_id` column + app-layer filtering se better kyun:** group
membership JWT (`cognito:groups` claim) pe free mein milti hai — koi
extra DB round-trip nahi chahiye yeh jaanne ke liye ki kaun kahan belong
karta hai ya kaun admin hai. `is_admin` claim membership check karke
derive hota hai, kahin mutable flag store nahi hota jo app ke andar
Cognito ki actual state se out-of-sync ho jaaye.

**Jo tradeoff maine accept kiya:** v1 mein ek user exactly **ek**
non-admin group tak limited hai. Mental model simple hai, but ek insaan
do orgs mein nahi ho sakta. Maine yeh edge case live hi hit kiya — ek
real test account accidentally doosre org ke group mein add ho gaya
testing ke time, aur tenant resolution ambiguous ho gaya (code
`tenant_groups[0]` pick karta hai, aur Cognito claim order guarantee
nahi karta). Yeh confirm kiya ki structural limitation hai, bug nahi —
"properly" fix karne ka matlab hoga har session mein ek explicit
"active org" selection carry karna, jo apne aap mein ek real feature
hai, patch nahi.

**Agar puchha jaaye "database row per org kyun nahi":** ek Cognito group
already durable, queryable membership state hai, built-in admin tooling
ke saath (`admin-add-user-to-group`, etc.) — isse app-owned rows mein
reinvent karna bas wahi cheez dobara banana hoga jo Cognito already free
mein deta hai, bina uske IAM-level audit trail ke.

---

## 2. Tenant isolation retrieval filter mein hai, post-fetch check mein nahi

**Kya hai:** Bedrock Knowledge Base ki `retrieve` call ek metadata
filter carry karti hai — `tenant_id` hamesha, plus (non-admins ke liye)
`uploaded_by` / `shared_with` — jo vector store khud evaluate karta hai,
app tak kuch aane se pehle hi.

**"Broad fetch karo, phir Python mein filter karo" se yeh kyun better
hai:** agar filtering retrieval ke *baad* hoti hai, to downstream kahin
bhi ek bug — ek missed check, ek copy-paste error, ek naya code path jo
filter karna bhool jaaye — koi document prompt mein leak kar sakta hai.
Agar filter retrieval call ke andar hi hai, to vector store *literally*
doosre tenant ke vectors kabhi return hi nahi karta. App mein kahin bhi
ki gayi galti ka blast radius construction se bound hai, vigilance se
nahi.

**Real challenge jo hit hua:** Bedrock KB ek empty `STRING_LIST`
metadata attribute ko outright **reject kar deta hai** — `shared_with:
[]` likhne se poori ingestion silently fail ho jaati hai, sirf us field
ki nahi, poore document ki. CloudWatch ingestion logs dekh ke pakड़ा,
jab documents mysteriously searchable nahi the. Fix: jab kuch put karne
ko na ho to key ko *poori tarah omit* kar diya — upload ke time bhi aur
un-share karte waqt bhi.

**Sharing model:** per-document, admin-controlled — sharing state S3
object pe ek `.metadata.json` sidecar ke roop mein rehti hai, jo Bedrock
evaluate karne wala filter attribute ban jaata hai. Kisi document ko
un-share karna us poore tenant ke liye semantic cache invalidate kar
deta hai (§3 dekho) taaki koi stale cached answer permission change ke
baad bhi zinda na reh jaaye.

---

## 3. Semantic caching: embeddings + cosine similarity, empirically tuned

**Kya hai:** standalone (rewritten) query embed hoti hai; cache lookup
us tenant+user ke recent entries mein best cosine match dhoondta hai,
exact string match ke bajaye.

**Exact-string caching kyun nahi:** poori value *rephrased* repeats
pakड़ne mein hai — "what's the refund policy" aur "can I get a refund"
dono hit hone chahiye.

**Real tradeoff, testing se mila, assume nahi kiya:** cosine similarity
"same question, reworded" ko "different question, similar structure"
se cleanly separate **nahi** karta. `text-embedding-3-small` pe directly
measure kiya: *"vacation days?"* vs *"sick days?"* ne **0.71** score
kiya — ek genuine paraphrase pair (*"refund policy?"* vs *"what is our
refund policy?"*) ke **0.70** se bhi zyada. Matlab jo bhi threshold
loose ho real paraphrases reliably pakड़ne ke liye (~0.80–0.85), wo
*bhi* confidently ek galat question ko galat cached answer serve kar
dega. Maine **0.93** choose kiya — ek deliberately conservative
threshold, thoda kam hit-rate accept karke taaki confidently-galat
cached answer ka risk zero ho jaaye. Ek false negative sirf ek normal
pipeline run ka cost karta hai; ek false positive ek silently galat
answer hota.

**Future improvement I scoped out but haven't shipped:** upar wala
threshold problem (0.93 ka fixed cutoff) ek real limitation hai — 0.80 se
0.93 ke beech ke scores mein genuine paraphrases bhi honge jo miss ho
jaate hain, sirf isliye ki cosine similarity akele confident nahi hai.
Cosine ceiling khud badalna unsafe hoga (wo upar measure kiya hua hai),
lekin sirf us **gray zone** ke liye ek fast, structured "kya yeh dono
same question hain" decision (jaise TypeSafe AI ka **Jev** — ek "System
One Model", freeform text nahi, sirf calibrated yes/no decisions, 70-500ms
mein) add karke gray-zone paraphrases recover kiye ja sakte hain, bina
cosine ceiling touch kiye. Maine iska ek POC-level implementation try kiya
(naya `typesafe_service.py`, `cache_service.lookup` mein 3-way branch),
lekin phir revert kar diya — abhi in-repo main branch mein sirf original
single-threshold (0.93) design hai. Agar puchha jaaye "cache ko aur kaise
improve karoge", yehi accha answer hai: ek narrow, fail-safe (blank
key/error pe automatically miss) disambiguation step sirf gray-zone
scores ke liye, poore threshold ko loose kiye bina.

**Scoping:** cache key `(tenant_id, user_id, is_admin)` hai, kabhi
usi org ke users ke beech share nahi hoti — do members ko same question
pe legitimately alag, dono-hi-sahi answers mil sakte hain per-document
sharing ki wajah se, isliye ek shared cache entry ek aise document se
bana answer leak kar sakti thi jo unme se ek dekh hi nahi sakta.

**Storage:** in-memory, module-level dict — Redis nahi. v1 tradeoff
accept kiya (restart pe lost ho jaata hai, multiple backend replicas
mein span nahi karta); Redis obvious upgrade path hai agar isse ek se
zyada replica chalana pade, current scale pe zaroorat nahi.

---

## 4. HyDE kyun, rerank kyun

- **HyDE** (Hypothetical Document Embeddings): cache miss pe, pehle ek
  *hypothetical answer* generate karo, phir raw question ke bajaye
  *usko* embed karke retrieve karo. Ek plausible answer passage embedding
  space mein real source document ke zyada paas baithta hai chhote se
  question se — better recall milta hai.
- **Rerank (Cohere), retrieval ke baad:** vector store se broad fetch
  (top 20) karo, phir generation model tak pahunchne se pehle ek
  reranker se precisely top 4 tak narrow karo. Vector store se hi
  directly top-4 pe precise hone ko kehne se yeh cheaper aur zyada
  accurate hai.

---

## 5. Do agents, jaan-boojhkar alag trigger models

Same low-level building blocks (retry helper, notification email, audit
log) — lekin jaan-boojhkar opposite cost/safety posture ke saath deploy
kiye.

| | Admin co-pilot | Error watchdog |
|---|---|---|
| Trigger | On-demand — admin sawaal puchhta hai | Event-driven — real query fail hote hi turant fire hota hai |
| Reasoning | Full tool-calling loop (5 read tools, 2 action tools), 5 iterations pe capped | Deterministic retry loop (koi LLM per attempt nahi); ek hi LLM call agar saare retries fail ho jaayein |
| Kiski tarah act karta hai | Admin ki tarah | **Original failing user ki hi permissions** — kabhi elevated nahi |
| Cost shape | Admin kitni baar puchhta hai usse bound | Kitne *real* errors actually hue usse bound, koi polling interval nahi |

**Ek hi unified agent kyun nahi:** watchdog unsupervised hai — koi bhi
uski har invocation ko approve nahi karta jaise admin ek co-pilot
question approve karta hai — isliye isme *structural*, sirf logical
nahi, guarantees chahiye runaway behavior ke against:

- **No recursion by construction.** Watchdog bare pipeline function ko
  directly call karta hai, kabhi us endpoint ke through nahi jo isse
  trigger karta hai. Ek retry ki apni failure ka koi code path watchdog
  tak wapas nahi jaata.
- **Hard retry cap (3).** Koi soft guideline nahi — ek `while` loop
  bound hai.
- **Per-tenant cooldown (15 min).** Taaki ek real outage (jaise KB down
  ho) har us user ke liye duplicate investigation spawn na kare jo us
  waqt query kar raha ho — sirf pehla wala ek investigation start karta
  hai; baaki skipped log hote hain, silently drop nahi hote.
- **Retry failing user ki hi identity use karta hai, admin ki nahi.**
  Elevated access se retry karna sirf isliye "succeed" ho sakta tha
  kyunki ab wo aise documents padh sakta jo original user kabhi padh hi
  nahi sakta tha — ek galat, misleading fix.
- **`notify_admin` ka recipient server-side hardcoded hai**, kabhi
  LLM-suppliable parameter nahi — ek test se verify kiya jo deliberately
  tool-call arguments ke through ek alag recipient smuggle karne ki
  koshish karta hai aur confirm karta hai ki wo ignore ho jaata hai.
  Model decide karta hai *whether* aur *kya* bolna hai, kabhi *kisko*
  batana hai nahi.

**Agar puchha jaaye "agent cost spiral kaise rokte ho":** yehi poora
answer hai upar — loop ko structurally bound karo, real events pe
trigger karo timers pe nahi, aur expensive path (ek LLM call) ko
*exception* path banao, default path nahi.

---

## 6. Observability, doosra metrics system banaye bina

Har pipeline run already ek LangSmith trace hai (`@traceable`), runtime
pe `tenant_id`, `user_id`, `cache_hit`, `source_count` se tagged. Admin
dashboard ke cost/latency/error-rate/cache-hit numbers seedhe us trace
metadata se compute hote hain — koi alag metrics store nahi jo reality
ke saath sync rakhna pade.

**Real constraint jisne shape kiya:** LangSmith ka `list_runs` `limit`
ko directly API page size ki tarah forward karta hai (server-capped 100
pe) — `limit=5000` pass karne se paginate nahi hota, bas 500 error aata
hai. `itertools.islice` se iterator ke upar fix kiya. Aur ek baat: filter
DSL sirf **ek** `metadata_key`/`metadata_value` pair accept karta hai per
query, isliye per-user breakdowns tenant ke runs ek baar pull karke aur
client-side group karke compute kiye jaate hain, har user ke liye alag
query karne ke bajaye.

Admin CRUD actions (suspend, promote, invite, document upload/delete) ko
ek *alag*, simple append-only DynamoDB audit log milta hai — yeh plain
routes pe hote hain jahan koi LLM trace hi nahi hota jispe metadata tag
kiya ja sake.

---

## 7. Streaming, aur uske saath aane wala guardrail tradeoff

Answers SSE ke through token-by-token stream hote hain perceived latency
ke liye. Real cost: **output** guardrail check jo pehle poore generated
answer pe chalta tha, wo streaming path ke liye drop karna pada — jab
tak complete text check karne ke liye exist karta hai, tab tak tokens
user tak pahunch chuke hote hain, isliye post-hoc block ya to kuch nahi
karega ya actively misleading hoga (user already answer dekh chuka hai
uske baad block karna). **Input** guardrail ab bhi generation shuru hone
se pehle chalta hai. Yeh ek known, accepted v1 tradeoff hai, oversight
nahi.

---

## 8. Real bugs jo mile, aur kaise mile

- **Unauthenticated endpoint:** `POST /documents/{id}/ingest` pe koi
  auth dependency thi hi nahi — ek deliberate, systematic security pass
  mein mila (accident se nahi), `require_admin` + ek tenant-ownership
  check se fix kiya. "Security bugs kaise dhoondte ho" ka accha answer:
  har route ki auth dependency explicitly review karo, consistency
  assume mat karo.
- **Ek poora directory silently git se untracked tha:** `.gitignore` ka
  bare `lib/` pattern (leftover Python boilerplate) `frontend/app/lib/`
  ko bhi match kar raha tha — usme ki har file first commit se hi
  un-versioned thi. Yeh notice karke mila ki `git status` un files ke
  changes kabhi nahi dikha raha tha jinhe maine *edit kiya hi tha*.
  Overly-broad pattern hataya aur files re-add ki. "Koi subtle bug bataao"
  ka accha answer: fix ek line ka tha; dhoondhne ke liye ek *absence*
  notice karna padा, koi error message nahi.
- **Invite gap:** kisi org mein join karne ke liye sirf uska slug
  jaanna/guess karna kaafi tha — iski jagah HMAC-signed, expiring invite
  tokens se close kiya.

---

## 9. Cost/latency levers jo actually apply kiye

- Right-sized model tier (`gpt-5.4-mini`, flagship model nahi) — pipeline
  ki har LLM call aur dono agents ke liye.
- Generation pe `max_tokens` cap (1024) — ek cited RAG answer ke paas
  itna lamba chalne ki koi legitimate wajah nahi hoti.
- Broad retrieve, narrow rerank (20 → 4) — vector store se hi directly
  precise hone ko kehne ke bajaye.
- `@lru_cache`'d client factories — har request pe naya client banane ke
  bajaye connection pooling.
- Semantic cache — near-repeat question pe poora pipeline skip karne ke
  liye.
- Background tasks — jo bhi user ka apna response block nahi karta uske
  liye (watchdog investigation streaming response poori tarah close hone
  ke *baad* chalti hai).

---

## 10. Guardrails, PII redaction, prompt-injection detection — layered, ek hi gate nahi

Teen alag, deliberately narrow safety mechanisms, har ek alag failure
mode solve karta hai:

- **Input guardrail** (Bedrock Guardrail, optional — blank env var isse
  no-op bana deta hai): generation shuru hone se *pehle* chalta hai, user
  ke raw question pe. Teeno mein se yehi ek hai jo actually request block
  kar sakta hai.
- **PII/secrets redaction** (`pii_service.py`): jaan-boojhkar regex-only
  hai — API keys (`AKIA...`, `sk-...`, `ghp_...`), PEM private-key
  blocks, `label: value` credential pairs, SSNs, credit-card numbers
  match karta hai. **Deliberately NER-based nahi** (koi Presidio-style
  PERSON/LOCATION detection nahi): "who is the project lead" jaise RAG
  answer mein ek naam hi to *sahi* answer hai — ek NER redactor sahi
  answer delete kar deta. Regex sirf un categories ko target karta hai
  jo *kabhi* legitimate answer ka part nahi hoti, isliye yeh bina kisi
  tuning ke unconditionally chal sakta hai, bina kisi sahi cheez ko
  redact karne ke risk ke.
- **Prompt-injection detection** (`injection_service.py`): retrieved
  chunks ka heuristic regex scan ("ignore previous instructions", "you
  are now admin", "reveal your system prompt", ek fake `system:` turn).
  **Flagged chunk phir bhi LLM ko bhej diya jaata hai** — yeh
  detection/observability hai, filter nahi. Do wajahein: (1) real defense
  already structural hai — har chunk `<context>` tags mein wrap hota hai
  ek explicit "yeh untrusted data hai, instructions nahi" framing ke
  saath, isliye model ko waise hi bataya gaya hai ki embedded commands
  follow na kare; (2) koi document jo *legitimately* prompt injection ke
  baare mein discuss karta ho (ek security wiki page, ek training doc),
  filter se uska content silently mutilate ho jaata, jabki ek genuinely
  malicious payload ko in patterns se match karne ki zaroorat hi nahi
  hoti. Iske badle jo milta hai: LangSmith trace/logs pe ek signal —
  "is answer ka context suspicious lag raha tha" — ek insaan review kare
  iske liye, same review-not-auto-fix posture jo admin dashboard ke
  zero-citation "knowledge gaps" panel ki hai.

**Agar puchha jaaye "match hote hi block kyun nahi karte":** block
karna ek false sense of safety ke badle real false positives deta hai,
bina kisi actual security gain ke ek motivated attacker ke against.
Structural containment (prompt framing) hi real control hai; regex scan
ek insaan ke liye cheap tripwire hai, last line of defense nahi.

---

## 11. RAG evaluation — is project mein maine kya implement kiya

### Interview mein bolne layak answer

"Maine RAG quality ko sirf answer dekh kar judge nahi kiya. Maine ek
developer-triggered offline evaluation harness banaya: fixed JSON test set ke
har question ko running `/chat/query` endpoint par bhejta hoon, answer aur
retrieved source excerpts collect karta hoon, phir Ragas se answer aur
retrieval-context quality separately score karta hoon. Results per-question
aur overall averages ke saath timestamped JSON mein save hote hain. Admin
dashboard un saved runs ko history/trend aur latest per-question breakdown ke
roop mein dikhata hai. Yeh offline regression/quality signal hai — production
traffic ka live score nahi."

### Test set aur execution flow

- Test set `backend/eval/test_qa.json` mein hai. Is waqt ismein Nimbus
  Robotics policy documents par based **5 simple factual questions** hain;
  har case mein `question`, `ground_truth_answer`, aur `expected_context`
  diya hai. Yeh useful starting point hai, lekin abhi broad representative
  benchmark nahi: multi-hop, ambiguous, unanswerable, conflicting/outdated,
  permission-sensitive aur prompt-injection categories systematically cover
  nahi ki gayi hain.
- `backend/scripts/run_eval.py` running API ko call karke har question ka
  answer aur returned `sources[].excerpt` collect karta hai. Test tenant
  `eval-tenant` ke documents pehle ingest hone chahiye.
- Script Ragas ke judge LLM/embeddings ke saath scores nikal kar
  `backend/eval/results_<timestamp>.json` mein per-question output likhti hai.
  Evaluation manual/developer-triggered hai; dashboard evaluation start nahi
  karta. Isse page load par slow aur paid judge-LLM calls nahi chalti.
- `backend/app/services/eval_service.py` saved JSON runs ko summarize karta
  hai; admin dashboard aggregate run history aur latest question-level scores
  dikhata hai. Eval history shared hai, kisi ek tenant ke production answers
  ka data nahi.

### Metrics: concept aur project mein actual implementation

| Metric | Kya batata hai | Is project mein |
|---|---|---|
| **Faithfulness** | Answer retrieved context se supported hai ya nahi | Implemented; generation grounding signal |
| **Answer relevancy** | Answer user ke question ko address karta hai ya nahi | Implemented; answer relevance signal |
| **Context precision** | Retrieved context mein useful/relevant evidence kitni hai | Implemented; retrieval noise ka signal |
| **Context recall** | Ground-truth answer ke liye zaroori evidence retrieve hua ya nahi | Implemented; retrieval coverage ka signal |
| **Recall@k, Precision@k, MRR, NDCG** | Ranked top-k retrieval ki hit-rate/ranking quality | In exact IR metrics ko abhi implement nahi kiya; context precision/recall ko inka synonym nahi kehna |

`expected_context` test file mein reference ke roop mein rakha gaya hai, lekin
current runner Ragas ko `question`, generated `answer`, retrieved `contexts`
aur `ground_truth_answer` deta hai — woh `expected_context` field ko scoring
input ke roop mein pass nahi karta. Isliye is implementation ko
reference-context matching ya manually labeled evidence-recall benchmark
kehkar overstate nahi karna chahiye.

### Result ko kaise interpret karunga

Repo mein maujood `results_20260827T132236Z.json` ek **purana, 3-question
candidate-resume run** hai; woh aaj ke 5-question Nimbus policy test set ka
result nahi hai. Us artifact ke averages lagbhag faithfulness **0.89**, answer
relevancy **0.58**, context precision **0.07**, aur context recall **0.33**
hain. Is ek chhote, alag test run se general quality claim nahi karunga. Us
run mein precision/recall kam hone ka signal tha ki retrieval/context selection
investigate karna chahiye; yeh apne aap mein statistically reliable conclusion
nahi hai.

### Limitations aur next step

"Abhi benchmark chhota hai aur mostly simple factual questions cover karta
hai. Main ise real anonymized user queries aur deliberately designed edge
cases se expand karunga; answerability aur expected evidence labels add
karunga; phir retrieval ranking ke liye Recall@k/MRR/NDCG jaise metrics bhi
measure karunga. CI mein har PR par Ragas chalana abhi nahi kiya, kyunki judge
calls slow/costly hain. Pehle test set aur baseline ko reliable banana
priority hogi."

### Online feedback se farq

👍/👎 feedback LangSmith run par `user_score` attach karta hai, aur knowledge
gaps panel zero-source queries dikhata hai. Yeh production feedback/monitoring
signals hain — fixed offline benchmark ya Ragas metrics ka replacement nahi.
Feedback authorization server-side par run ke `tenant_id` se verify hoti hai;
client-supplied run ID ko ownership proof nahi maana jaata.

---

## 12. Real production incident: independent frontend/backend deploys

Frontend (Vercel) aur backend (Railway) independently deploy hote hain —
koi shared release train nahi. Isse ek live window bana jahan frontend
ek field padh raha tha jo backend ne abhi ship hi nahi ki thi, aur yeh
actually do baar hua, same dashboard pe:

1. **Pehli baar:** `/admin/eval` frontend pe ship ho gaya us backend
   endpoint se pehle jo production mein exist hi nahi karta tha.
   Dashboard har panel ki fetch ke liye `Promise.all` use kar raha tha —
   ek endpoint ke 404 karne se poora `Promise.all` reject ho gaya aur
   **har doosra panel bhi blank ho gaya jo already successfully load ho
   chuka tha**. Fix: `Promise.all` → `Promise.allSettled`, taaki har
   panel doosron se independently populate ho (ya apna khud ka error
   dikhaaye).
2. **Doosri baar, same root cause:** `p99_latency_s` /
   `latency_histogram` frontend pe ship ho gaye us backend redeploy se
   pehle jo actually inhe produce karta hai. `formatSeconds(undefined)`
   throw ho gaya, aur kuch bhi catch na karne ki wajah se **poora admin
   app white-screen ho gaya**, sirf ek panel nahi. Teen-layer fix:
   - `formatCost`/`formatSeconds`/`formatScore` ab non-numeric input pe
     `"—"` render karte hain, throw nahi karte — backend se aane wala
     har field ab frontend mein *optional* maana jaata hai jab tak
     proven na ho.
   - Latency-histogram section ab ek explanatory empty state pe fallback
     karta hai, kisi possibly-missing array pe `.map` call karne ke
     bajaye.
   - Admin route pe ek Next.js route-level `error.tsx` — matlab is page
     pe koi *aur* render crash bhi ab poora app blank karne ke bajaye ek
     retry screen tak degrade hota hai — yeh ek last line of defense
     hai, asli fix nahi.

**"Production incident batao" / "independently-versioned services kaise
handle karte ho" ka accha answer:** yeh assume mat karo ki API ke dono
side saath deploy honge sirf isliye ki wo ek hi team ka code hai — jo
bhi field frontend backend se padhta hai use possibly-absent maano, ek
*poore page* ke bajaye ek *panel* fail hone do (`all` ki jagah
`allSettled`), aur jo kuch anticipate nahi kiya usके liye ek route-level
error boundary backstop ki tarah rakho.

---

## Likely follow-up questions

**"Tumhe kaise pata ki Tenant A kabhi Tenant B ka data nahi dekh
sakta?"**
Kyunki filter vector store khud retrieval query ke part ki tarah
evaluate karta hai — koi code path hi nahi hai jahan retrieval pehle ho
aur *phir* filter ho. Main per-document sharing test ki taraf bhi point
karunga: ek failed query ka retry hamesha *original* user ki apni
`tenant_id`/`is_admin` se replay hota hai, kabhi elevated identity se
nahi, specifically taaki koi "successful" auto-retry permission
elevation ki wajah se false positive na ho.

**"Separate databases per tenant kyun nahi?"**
Sabse strong isolation guarantee dega, but ek aise SaaS ke liye real
infra cost pe jismein unknown number of small orgs ho sakte hain —
connection/schema management per tenant operationally utna scale nahi
karta jitna ek shared store with a hard query-time filter karta hai.
KB-level filter is shape ke product ke liye sahi tradeoff tha.

**"Agar dobara banao to kya alag karoge?"**
Din 1 se hi ek user ko ek se zyada org mein belong karne dunga — single-
group constraint tab tak theek tha jab tak nahi tha, aur "main abhi
kaunse org ki tarah act kar raha hun" ko har session ke through baad
mein retrofit karna ek bada change hai us se jo shuru se design karna
hota.

**"Yeh multiple backend replicas tak kaise scale karega?"**
Semantic cache aur watchdog ka cooldown state dono in-memory,
single-process hain — honest answer yeh hai ki correctness ke liye
replicas ke across inhe Redis (ya similar) pe move karna padega. Yeh
code mein ek known, explicitly documented tradeoff hai, production mein
hard way se discover karne wali cheez nahi.

**"Ek query end to end walk-through karo."**
Guardrail check → standalone question mein rewrite → embed + semantic
cache lookup → (miss pe) HyDE passage → tenant-filtered retrieval from
Bedrock KB → Cohere rerank top 20 se 4 tak → streamed, cited generation
→ cache write-back → har step ek hi LangSmith trace pe tagged.

---

## Quick recall — sabse mushkil parts + maine kya kiya

Interview mein bolne se pehle apne liye ek-line recall. Har point:
*problem kya thi* → *solution kya liya, aur kyun*.

1. **Cross-tenant data leak ka risk** — sabse bada dar tha ki koi bug se
   Tenant A ka document Tenant B ko dikh jaaye. **Solution:** filtering
   app code mein *baad* mein nahi ki — Bedrock KB ke retrieval query ke
   *andar* hi metadata filter (`tenant_id` + `shared_with`) laga diya,
   taaki vector store khud dusre tenant ka vector kabhi return hi na
   kare. Ek downstream bug se bhi leak nahi ho sakta (§2 dekho).
2. **Bedrock KB silently reject kar raha tha empty `shared_with: []`** —
   pura document un-searchable ho gaya tha, koi clear error nahi mila,
   CloudWatch ingestion logs dekh ke pakड़ा. **Solution:** jab share
   karne ke liye koi user nahi hai, key ko `[]` likhne ke bajaye poori
   tarah *omit* kar diya, upload aur un-share dono jagah.
3. **Semantic cache ka threshold tune karna** — "vacation days?" aur
   "sick days?" jaise **different** questions cosine similarity mein
   genuine paraphrase se bhi zyada score kar rahe the (0.71 vs 0.70).
   Isliye loose threshold (~0.80-0.85) galat-cached-answer serve kar
   deta. **Solution:** deliberately conservative **0.93** rakha —
   hit-rate kam ho jaayegi but confidently-wrong cached answer ka risk
   zero. Assumption nahi, actual testing se decide kiya. **Future idea
   (not shipped):** 0.93 ke neeche ek 0.80–0.93 "gray zone" ke liye ek
   fast structured yes/no disambiguation model (jaise TypeSafe AI ka
   Jev) se ambiguous cases recover kiye ja sakte hain, cosine ceiling
   ko loose kiye bina — maine iska POC try kiya tha, phir revert kar
   diya (§3 ka "Future improvement" part dekho).
4. **Agent (watchdog) cost/safety spiral ka risk** — ek unsupervised
   agent jo har real error pe khud trigger hota hai, uske runaway hone
   ka structural risk. **Solution:** hard retry cap (3), per-tenant
   15-min cooldown, no recursion (bare pipeline function directly call
   hota hai, endpoint ke through nahi), aur retry hamesha **original
   failing user ki hi identity** se hota hai — admin ki elevated access
   se nahi, taaki "fix" fake positive na ho.
5. **`notify_admin` tool ka recipient LLM se manipulate ho sakta tha** —
   test likha jo deliberately galat recipient smuggle karne ki koshish
   karta hai tool-call arguments mein. **Solution:** recipient
   hardcoded server-side rakha, LLM sirf decide karta hai *whether* aur
   *kya* bolna hai, kabhi *kisko* batana hai nahi.
6. **Unauthenticated endpoint mil gaya** — `POST /documents/{id}/ingest`
   pe koi auth dependency hi nahi thi. Ek systematic security review
   pass mein pakड़ा, accident se nahi. **Solution:** `require_admin` +
   tenant-ownership check add kiya. Har route ka auth dependency
   explicitly review karna chahiye, consistency assume nahi karni.
7. **Ek pura directory silently git se untracked tha** — `.gitignore`
   ka bare `lib/` pattern `frontend/app/lib/` ko bhi match kar raha
   tha, first commit se hi. Koi error nahi tha — sirf `git status` mein
   wo changes dikh hi nahi rahe the jo maine kiye the. **Solution:**
   overly-broad pattern hataya, files re-add ki. Subtle bug ka matlab
   yeh — absence notice karna padta hai, error message nahi milta.
8. **Streaming vs output guardrail** — token-by-token streaming se
   perceived latency improve hui, but output guardrail full generated
   answer pe hi kaam karta hai — stream ho jaane ke baad block karna
   useless/misleading hota (user already answer dekh chuka). **Solution:**
   output guardrail streaming path pe drop kiya, sirf input guardrail
   generation shuru hone se pehle chalta hai — ek conscious, documented
   tradeoff, oversight nahi.
9. **Independent frontend/backend deploys se production crash (do baar
   hua)** — Vercel (frontend) aur Railway (backend) alag-alag deploy
   hote hain. Frontend naya field (`p99_latency_s`) padhne laga backend
   redeploy se pehle hi → `formatSeconds(undefined)` crash → **poora
   admin dashboard white-screen**. Isse pehle bhi `/admin/eval` shipping
   ke time same wajah se `Promise.all` ek panel fail hone pe *saare*
   panels blank kar deta tha. **Solution:** teen layers — (1) format
   helpers ab non-numeric input pe `"—"` render karte hain, crash nahi;
   (2) `Promise.all` → `Promise.allSettled` taaki ek panel fail ho to
   baaki independently load ho; (3) route-level `error.tsx` error
   boundary as last line of defense. Lesson: backend se aane wala har
   field frontend mein *optional* maano jab tak proven na ho.
10. **Prompt injection aur PII ke liye kya kiya** — retrieved chunks
    mein injection heuristically *detect* kiya (block nahi, kyunki real
    defense to `<context>` tags ki structural framing hai, aur filter
    false-positive de sakta — jo document legitimately injection
    discuss karta ho uska content delete ho jaata). PII/secrets ke liye
    NER-based redaction use nahi kiya (wo galti se sahi answer bhi
    redact kar deta, jaise koi naam) — sirf narrow regex patterns (API
    keys, SSN, credit card) jo kabhi bhi legitimate answer ka part
    nahi hote.
11. **Single non-admin group per user ki limitation** — ek real test
    account accidentally do orgs ke group mein add ho gaya, tenant
    resolution ambiguous ho gaya (Cognito claim order guarantee nahi
    karta). Yeh maine confirm kiya ki structural limitation hai, bug
    nahi — proper fix ke liye har session mein "active org" select
    karna padega, jo v1 scope se bahar ek real feature hai.
