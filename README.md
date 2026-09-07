# Sabor da Maria

Consultora de cardápio e precificação para o delivery da Dona Maria.
O sistema transforma a planilha da despensa num cardápio de lançamento
precificado, por uma conversa stateful guiada por tools.

O agente (Hermes) pesquisa receitas reais na web e conduz a entrevista.
O servidor MCP guarda o estado, deriva requisitos da receita, cruza
ingredientes com a despensa e calcula CMV e preços com aritmética
determinística. Nenhum valor financeiro é inventado pelo modelo.

## Central design decision

The LLM is non-deterministic; the workflow is not. Hermes uses the
model only for user interaction: understanding Dona Maria, researching
recipes, and wording questions from the facts the tools return. FastMCP
and `WorkflowService` own the sequence and the results -> gated
transitions, capability elicitation, Decimal CMV/pricing, budget, and
acceptance. Tools execute that path and expose `next_actions` as staate
hints; the model does not improvise the next step or invent financial
outputs. A fluent conversation therefore does not depend on the LLM to
guide the process or produce the numbers.

I had not used Hermes before. After studying how it is extended, MCP
is how it already calls tools, and that is what this challenge asks
to customize. FastAPI, LangGraph, or a ReAct loop over REST would stil
leave the model choosing which route to hit next, or would rebuild the
same gated workflow behind HTTP while the interview still needs Hermes.
MCP keeps the conversation in the agent: each tool is a testable
transition, without an extra API call that leaves the sequence no more
deterministic. So I chose MCP as the contract between Hermes and the workflow.

## Architecture

```
Hermes Agent  (.hermes.md -> conversation runtime)
  │
  ├── Built-in web tools     research real recipes + market quotes
  │
  └── FastMCP server         10 tools, SQLite as backend
        │
        └── Workflow tools (stateful)
              initialize_workspace        load pantry from xlsx (first time)
              reset_workspace             transactional full wipe + reload
              get_workspace_status        inspect pantry, kitchen, recipes, budget
              register_recipe             persist recipe, extract requirements,
                                          match ingredients (Hermes sampling)
              record_feedback             Dona Maria's interest
              record_kitchen_capability   one equipment / skill / operational fact
              confirm_ingredient_match    resolve ambiguous pantry matches
              submit_market_quotes        plan purchases within the R$80 budget
              prepare_recipe_pricing      CMV breakdown + three price scenarios
              accept_recipe               transactional acceptance gate
```

### Responsibility boundary

| Layer | Owns | Does not own |
|-------|------|--------------|
| `.hermes.md` + Hermes | Conversation, web research, question wording, grouping of unknowns | Business state, CMV, prices, whether a transition is allowed |
| FastMCP + `WorkflowService` | SQLite state, feasibility gates, Decimal CMV/pricing/purchasing | Dialogue scripts, a single next question, recipe discovery |

Tool results return facts (`unknown_capabilities`, `ingredient_availability`,
`cmv_breakdown`, `next_actions` as state hints). Hermes decides how to speak
to Dona Maria. The UI shows tool activity separately; the agent never
narrates orchestration.

### Mandatory workflow

The conversation can adapt, but these transitions are gated:

1. Inspect or initialize the workspace.
2. Research a real recipe (web search) and present name, URL, servings, and steps.
3. Wait for interest (`register_recipe(..., interested=true)` or `record_feedback`).
4. Server extracts equipment / skills / operational needs from the sourced steps.
5. Resolve ambiguous ingredient identities (`needs_confirmation`).
6. Elicit equipment + operational conditions first; skills in a later turn.
7. Record every explicit answer with `record_kitchen_capability`.
8. Only when every capability is resolved, research quotes for missing items.
9. `submit_market_quotes` (empty list if the pantry covers everything).
10. `prepare_recipe_pricing`: present every CMV line and all three scenarios.
11. Dona Maria chooses a price label.
12. `accept_recipe` revalidates and commits stock, purchases, and budget.

`submit_market_quotes` and `prepare_recipe_pricing` refuse to run while
feedback, units, capabilities, or ambiguous matches are still open.

### `register_recipe` internals

Registration is the only tool that calls Hermes sampling, twice:

1. **Requirement extraction**: `ctx.sample` over the sourced steps. The
   result is merged with a conservative regex fallback on the same steps
   (`requirements_service.enrich_recipe_requirements`). Registration
   **fails closed** if steps, equipment, or skills still cannot be verified.
   Operational keys are always injected: `gas_or_electric`, `fridge_space`,
   `prep_time`, `counter_space`, plus `stove_burners` when the recipe
   implies a stove.
2. **Ingredient matching**: exact case-insensitive pantry hit first;
   otherwise `ctx.sample`. If sampling is unavailable, that ingredient is
   treated as `not_available` so the flow can continue as a purchase.

Re-registering the same recipe id keeps previous feedback and match
decisions for unchanged ingredient names, but clears plan / CMV / pricing
and `accepted`.

## State management

All business state lives in SQLite (`.runtime/sabor-da-maria.sqlite3` by
default, or `SABOR_DB_PATH`). State survives MCP and Hermes restarts.

| Table | Role |
|-------|------|
| `workspace` | Budget (R$ 80.00) + serialized pantry |
| `kitchen_capabilities` | Tri-state equipment / skill / operational + optional detail |
| `recipes` | Sourced recipe JSON, interest, plan, CMV, pricing, acceptance |
| `ingredient_matches` | `matched` / `needs_confirmation` / `not_available` |
| `market_quotes` | Sourced complementary-purchase quotes |
| `inventory_reservations` | Pantry quantity reserved on accept |
| `committed_purchases` | Cash outlay + leftover package quantity |

Effective stock for later recipes = pantry quantity − reservations + leftover
purchased packages that share a pantry ingredient name.

## Key design decisions

| Decision | Rationale |
|----------|-----------|
| SQLite over in-memory | State survives restarts; transactions commit stock and budget together |
| Deterministic services | CMV, pricing, and purchasing use `Decimal`, never LLM arithmetic |
| Hermes sampling, no extra LLM SDK | The MCP server asks its caller for two semantic decisions (so we can use the LLMs to understand ingredients semantically): recipe requirements and ingredient identity |
| Fail-closed requirement extraction | Sampling is merged with a step-based fallback; missing equipment or skills block registration |
| Fail-open ingredient matching | A sampling outage marks the item missing so complementary purchase can still be planned |
| Tri-state capabilities | `True` / `False` / `None`; the agent only asks about unknowns |
| Capability normalization | Portuguese aliases (`forno` → `oven`) resolve to canonical `Equipment` / operational keys |
| Structured workflow guidance | Tools expose facts and grouped unknowns; Hermes owns wording |
| Two-stage elicitation | Equipment + operational first, skills in a separate turn; server `next_actions` enforce the split |
| Mid-flow gates | Quotes and pricing require interest, compatible units, and resolved feasibility |
| Per-ingredient CMV breakdown | `PricingResult` includes `quantity_used × unit_cost = line_total` for every component |
| Verifiable price explanation | Tool returns the 10% fee, net-revenue, no-loss, and profit formulas with the actual numbers |
| Structured availability | `RecipeRegistration` returns required / available / missing quantity per ingredient |
| Cash outlay vs allocated cost | Full package price spends the R$ 80 budget; only the consumed quantity enters CMV |
| Leftover packages | Unused complementary quantity is added to effective stock for later recipes; CMV still uses the source's own unit cost (pantry history vs quoted purchase), not a blended average |
| Operational checklist | Every recipe must resolve energy, fridge, time, and counter space |
| Idempotent re-register | Re-registering preserves feedback and prior matches, invalidates plan / CMV / pricing |
| Double-accept guard | `accept_recipe` rejects an already-accepted recipe |
| Portuguese price aliases | `entrada` / `equilibrado` / `maior margem` also accept phrases such as `mais barato` or `no meio` |

## Acceptance gates

No recipe can be accepted until:

1. Every required equipment, skill, and operational fact is answered (`None` is not enough).
2. No capability is known-unavailable.
3. Every `needs_confirmation` match is confirmed or rejected.
4. CMV and the three price scenarios have been calculated (`prepare_recipe_pricing`).
5. Complementary purchases fit the remaining R$ 80 budget (checked when quotes are planned).
6. The selected price label exists on the stored pricing result.
7. The recipe has not already been accepted.

Acceptance is transactional: pantry reservations, committed purchases, and
budget are written atomically.

Price scenarios are deterministic markups over per-serving CMV, after the
10% platform fee (`P` such that Dona Maria receives `0,90 · P`):

| Label | Profit over CMV |
|-------|-----------------|
| `entrada` | 20% |
| `equilibrado` | 40% |
| `maior margem` | 60% |

Minimum no-loss price is `CMV / 0,90` (rounded up). Profit is `0,90 · P − CMV`.


## Highlights

- Implemented a Demo UI (`web/`) so the interview can be exercised as a product, not
  only in the CLI, a base to push UX further.
- Implemented a deterministic unit tests around workflow gates, CMV, pricing, and MCP
  tools.

## Next steps I would implement

- Eval on LLM outputs (deep-evaluation or LLM-as-judge) for high-level
  conversational quality and observability.
- An LLM-as-judge loop-back before `register_recipe`, to check flow
  consistency and what was extracted from Dona Maria, if necessary loop back to hermes again.
- Docker, so deploy and run are standardized.


## Setup

Requirements: Python 3.11+, `uv`, [Hermes Agent](https://github.com/nousresearch/hermes-agent).

```bash
# Install dependencies
uv sync --dev

# Run tests (190 deterministic tests)
uv run pytest

# Register MCP server with Hermes
hermes mcp add sabor_da_maria \
  --command uv \
  --args --directory "$(pwd)" run python -m src.mcp.server

hermes config set mcp_servers.sabor_da_maria.protocol legacy
hermes config set mcp_servers.sabor_da_maria.sampling.max_tool_rounds 0
hermes config set mcp_servers.sabor_da_maria.tools.resources false
hermes config set mcp_servers.sabor_da_maria.tools.prompts false

# Verify
hermes mcp test sabor_da_maria
```

Sampling `max_tool_rounds 0` is required: `register_recipe` calls
`ctx.sample` for requirement extraction and ingredient matching. Nested
tool use during sampling would recurse.

## Running the UI or Server

```bash
# Browser demo: chat + tool steps + live menu table
./scripts/demo-ui.sh
```

Opens `http://127.0.0.1:8765`. Each run restats the Hermes gateway
(and the `sabor_da_maria` MCP) so tool schema changes load, then serves
the UI from `web/` via `scripts/demo_ui.py`. The page talks to the Hermes
API server (default `http://127.0.0.1:8642`) through a local proxy, so
the API key never reaches the browser.

The header shows remaining complementary-purchase budget. The side table
lists accepted dishes (CMV, sale price, profit, scenario). Use
**Resetar demo** to wipe workspace state, start a new Hermes session, and
send `scripts/demo-prompt.txt`.

The gateway needs `API_SERVER_ENABLED=true` and `API_SERVER_KEY` in
`~/.hermes/.env` or `hermes config`.

```bash
# CLI demo: wipe SQLite, reload the pantry, start Hermes with the first turn
./scripts/demo.sh
```

Close any existing Hermes CLI session before running `demo.sh`. It deletes
`.runtime/`, reloads `data/despensa_dona_maria.xlsx`, and opens

```text
hermes chat --in <repo> --query-file scripts/demo-prompt.txt -t web,sabor_da_maria
```

The `-t` flag limits the agent to web research plus this project's MCP.

```bash
# Start the agent without resetting (keeps previous workspace)
hermes chat --in . -t web,sabor_da_maria
```

### Demo prompt (Portuguese)

`scripts/demo-prompt.txt` is only Dona Maria's first spoken turn. Workflow
order (short web search, present then wait, register only after she says yes,
feasibility before CMV/price) lives in `.hermes.md`. The pantry is already
loaded by `demo.sh` and **Resetar demo**.

```
Olá! Sou a Dona Maria e quero montar meu cardápio de delivery do zero.
```

### Inspect or reset state

```bash
# Reset and reload pantry without starting Hermes
uv run python scripts/reset_workspace.py
```

## Development

```bash
# Run all tests
uv run pytest

# Run with verbose output
uv run pytest -v

# Inspect the MCP server
uv run fastmcp inspect src/mcp/server.py:mcp
```

## Project structure

```
.hermes.md                 Agent protocol (Portuguese UX, gates, elicitation)
src/
├── domain/                Pydantic models (recipe, pantry, costing, pricing, workflow)
├── infrastructure/        Excel loader (abas Despensa + Precos) + SQLite
├── mcp/                   FastMCP server + tool definitions
│   ├── runtime.py         SQLite + WorkflowService singleton
│   ├── server.py          registers the 10 stateful tools
│   ├── ingredient_matching/  Semantic matching + sampling prompts
│   ├── requirements/      Hermes requirement extraction + fail-closed merge
│   └── workflow/          Stateful workflow tools
├── services/              Business logic
│   ├── costing_service.py
│   ├── feasibility_service.py
│   ├── pantry_service.py
│   ├── pricing_service.py
│   ├── purchasing_service.py
│   ├── requirements_service.py   step-based fallback + operational minimums
│   └── workflow_service.py       orchestration + gates
└── utils/                 Unit conversion, Equipment enum, capability aliases
web/                       HTML / CSS / JS demo UI (chat + live menu)
scripts/
├── demo-ui.sh             Restart Hermes gateway + serve the UI
├── demo_ui.py             Local proxy (workspace JSON + Hermes SSE)
├── demo.sh                Reset SQLite and open Hermes CLI
├── demo-prompt.txt        First user turn
└── reset_workspace.py     Wipe .runtime and reload the spreadsheet
tests/                     190 deterministic tests
data/despensa_dona_maria.xlsx
```
