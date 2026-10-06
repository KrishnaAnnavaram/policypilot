# ASD-STE100 Simplified Technical English: the standard for PolicyPilot documents

Use these rules for the `README.md` of PolicyPilot and for this file. Section 3 gives the
**project vocabulary**: the technical names and the technical verbs of PolicyPilot. Each term has one meaning.

## 1. Rules for text

### Words

1. Use one word for one meaning, and one meaning for one word. Do not use synonyms for variety.
2. Use a word only as one part of speech. For example, "test" is a noun or a verb, "check" is a verb.
3. Do not use phrasal verbs (`set up`, `carry out`, `find out`, `pick up`, `look up`, `come up with`).
   Use one verb: "prepare", "do", "find", "get", "make".
4. Do not use an "-ing" form as a noun or an adjective (`the running job`, `after indexing`).
   Exception: a technical name, a file name, a command or a status value.
5. Do not use contractions (`don't`, `it's`, `can't`). Do not use slang or idioms
   (`out of the box`, `under the hood`, `at a glance`, `gotcha`, `bells and whistles`).
6. Do not use `and/or`. Write "A, B or both".
7. Do not use `should`, `could`, `would` or `may` for instructions. Use "must" for a rule, the
   imperative for a step and "can" for a possibility.
8. Keep the articles "a", "an" and "the" in sentences.
9. Do not make a noun cluster of more than three words. A technical name is one word.

### Sentences

1. A procedural sentence (an instruction) has a maximum of **20 words**.
2. A descriptive sentence has a maximum of **25 words**.
3. Write one instruction in one sentence.
4. Use the imperative for an instruction: "Run the tests." Not `The tests are to be run.`
5. Use the active voice. Use the passive voice only when the agent of the action is not important.
6. Use only the simple present, the simple past and the simple future.
7. Put a condition before the instruction: "If the index is old, build it again."
8. Do not use semicolons in sentences. Write two sentences.

### Paragraphs, notes and warnings

1. A paragraph has one topic and a maximum of **6 sentences**. Start with the topic sentence.
2. A warning or a caution starts with a clear command. Then it gives the reason.
3. A note gives information. It does not give an instruction.
4. Use a vertical list for a sequence or a set of conditions. Each item of a numbered procedure is one step.

### Tables, headings and diagrams

1. A table cell can be a short phrase. If a cell has a sentence, the sentence obeys the rules.
2. A heading is a noun phrase ("The cost model") or an imperative ("Run the demo").
   Do not start a heading with an "-ing" form.
3. A diagram label is a short phrase. Use the same terms as the text.

### What STE does not change

Code, commands, file names, paths, field names, environment variables, status values, enum values,
product names and URLs stay exactly as they are. They are technical names. Put them in backticks.

## 2. General words to replace

| Do not use | Use |
|---|---|
| utilize, leverage | use |
| in order to | to |
| set up | prepare, install, configure |
| carry out, perform | do |
| make sure, ensure | make sure (allowed), or "check that" |
| a lot of, lots of | many, much |
| e.g., i.e. | for example, that is |
| should (instruction) | must (rule) / imperative (step) |
| might, may (possibility) | can |
| very, really, just, simply, easily | (delete) |
| seamless, robust, powerful, blazing | (delete or give a measured fact) |

## 3. Project vocabulary

### 3.1 Technical names (nouns)

| Term | Meaning | Do not use |
|---|---|---|
| **agent** | A component that answers one route: SQL agent, aggregation agent, planner or RAG agent | bot, worker, handler |
| **aggregation agent** | The agent for the `nosql` route (`MongoAgent`) | Mongo agent, NoSQL agent, document agent |
| **allow-list** | A fixed list of permitted tables, columns, functions, stages, operators or fields | whitelist, safe list |
| **answer** | The text that the service returns for a question | reply, output text |
| **answerer** | The component that makes an answer from result rows (`Answerer`) | summarizer, composer |
| **attempt** | One LLM call, with the extraction, validation and run of its query | try, round |
| **back end** | A data system that PolicyPilot reads: SQLite, PostgreSQL, MongoDB or the in-memory document store | database layer, storage, backend |
| **canonical data model** | The typed description of the tables and the document fields in `schema.py` | schema (alone), data contract |
| **chunk** | A piece of a document in the hybrid index | passage, snippet, segment, fragment |
| **citation** | A reference `[n]` in an answer to the chunk with number `n` | reference, footnote, quote |
| **component** | One part of PolicyPilot with one purpose | module (except for a Python file), piece, part |
| **document view** | The nested form of one vehicle with its claim data (`to_document()`) | Mongo view, JSON view |
| **document store** | The component that runs a pipeline (`InMemoryDocStore`, `MongoDocStore`) | doc store, collection adapter |
| **embedder** | The component that changes text into a vector (`hashing` or `sentence-transformers`) | encoder, vectorizer |
| **embedding** | The vector that the embedder makes for a text | vector representation, encoding |
| **embedding model** | The `sentence-transformers` model that `EMBEDDING_MODEL` names | encoder model |
| **evaluation harness** | The code in `src/policypilot/evaluation/` that scores the service on the gold set | benchmark, test bench, evaluator |
| **executor** | The component that runs a SQL query read-only (`SQLiteExecutor`, `PostgresExecutor`) | runner, driver, SQL client |
| **front end** | The CLI, the HTTP API or the chat UI | interface, client, frontend |
| **gold query** | A correct SQL query or pipeline for a gold question | reference query, expected query |
| **gold set** | The list of evaluation questions in `gold_questions.json` | test set, benchmark set |
| **ground truth** | The result of a gold query on the data under test | expected answer, label |
| **hybrid index** | The search index that fuses BM25 and embedding scores (`HybridIndex`) | vector store, search engine, knowledge base |
| **input check** | The component that normalizes and limits the question (`clean_question()`). A technical name: the verb is "check" | sanitizer, input filter |
| **keyword router** | The rule-based router (`KeywordRouter`) | rule router, fallback router |
| **LLM** | The language model client: an OpenAI-compatible API or the offline LLM | model (alone), AI, bot |
| **LLM text** | The raw text that the LLM returns, before extraction | reply, completion, output |
| **offline LLM** | `OfflineLLM`, the rule-based stand-in for an LLM | mock LLM, fake model, dummy |
| **pipeline** | A MongoDB aggregation pipeline: a JSON array of stages | aggregation query, Mongo query |
| **planner** | The agent for the `both` route (`Planner`) | orchestrator, coordinator, joiner |
| **query** | A SQL query or a pipeline that the LLM generates | command, request (for a query) |
| **random seed** | The number that makes the synthetic data the same each time (`--seed`, default `7`) | seed (alone), RNG state |
| **question** | The plain-language text that a user sends | query (for user text), prompt, input |
| **response** | The JSON object that `POST /ask` or `policypilot ask --json` returns | payload, reply |
| **result** | The rows that a query returns | records, data |
| **retry** | A repeat of a failed LLM call or of a failed attempt | second try, redo |
| **route** | One of `sql`, `nosql`, `both` or `pdf` | path, channel, intent |
| **router** | The component that selects the route (`LLMRouter` with the keyword router) | classifier, dispatcher |
| **seed step** | The component that creates and fills the databases (`policypilot seed`, `src/policypilot/data/`) | loader, import job, ETL |
| **session** | One conversation with a random 32-character session ID | chat, thread, user context |
| **setting** | One configuration value that an environment variable gives | option, parameter, flag (for configuration) |
| **SQL agent** | The agent for the `sql` route (`SQLAgent`) | text-to-SQL bot |
| **statement** | One SQL command in a query text | SQL instruction |
| **upload index** | A hybrid index for the PDF files of one browser session | user index, private store |
| **query safety model** | The set of rules in code that limit what a query can read and how long it runs | security layer, guard rails |
| **validator** | The code that checks a query against the allow-lists (`sql_guard.py`, `mongo_guard.py`) | guard, checker, sanitizer, filter |

### 3.2 Technical verbs

| Verb | Meaning |
|---|---|
| **cite** | Refer to a chunk by its number `[n]` in an answer |
| **condense** | Change a follow-up question into a question that has its full meaning without the history |
| **generate** | Make text with the LLM: a route decision, a query, a plan or an answer |
| **reject** | Stop a query or an input and give an error message |
| **retrieve** | Find the top chunks for a question in the hybrid index |
| **run** | Start a command, a test, a query on a back end or the evaluation |
| **seed** | Create the databases and fill them from one dataset (`policypilot seed`) |
| **validate** | Check a query against the allow-lists with a validator |
