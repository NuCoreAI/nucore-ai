# What it takes for plugins to become full-fledged agents

**Note on this document:** this is an analysis/design-options memo, not a ready-to-execute code
plan. The question ("what does it take") is architectural, and the honest answer is a set of
distinct, separable changes with real tradeoffs -- not a single diff. Nothing below should be
built without picking a direction first.

## Context

Michel asked what it would take for NuCore's plugin mechanism -- currently minimal, stateless
"mini tools" -- to become full-fledged agents. This matters because the plugin marketplace is
NuCore's extension point for third-party capability (Camect, calendar sources, media plugins,
etc.), and today that extension point is intentionally narrow: a plugin answers exactly one
structured question per call and does no reasoning of its own. Understanding the gap to "agent"
clarifies both what's technically involved and what NEW trust/security surface would open up,
before committing to any of it.

## Where the mechanism stands today (verified against current code)

- A plugin declares tools via `get_plugin_tools` (`{"successful", "data": {"tools": [...]}}`) and
  usage prose via `get_plugin_prompt` -- both lazily fetched only once a plugin becomes relevant
  ([nucore_interface.py:580-602](../../src/nucore/nucore_interface.py#L580-L602)). Each declared tool
  name is rewritten `f"{plugin_id}_{tool_name}"` for uniqueness
  ([iox_wrapper.py:1645-1648](../../src/iox/iox_wrapper.py#L1645-L1648)).
- The host LLM (NuCore's own model, running `AgenticLoop`) picks one of those tools and calls
  `call_plugin(plugin_id, tool_name, args)`
  ([plugin_management.py:236-255](../../src/unified/handlers/plugin_management.py#L236-L255)), which
  forwards `{**args, "tool_name": tool_name}` in a single HTTP POST to
  `/api/plugin/{plugin_id}/request` via `handle_plugin_llm_result`
  ([iox_wrapper.py:1662-1688](../../src/iox/iox_wrapper.py#L1662-L1688)) and returns whatever the
  plugin reports back, verbatim.
- That's the entire contract: **one deterministic request/response per call.** The plugin never
  sees the customer's actual words, conversation history, or device state -- only the structured
  `args` the host model already extracted. It cannot ask a follow-up question, cannot invoke its
  own LLM, cannot call other tools (NuCore's or its own) mid-task, and nothing persists between
  calls beyond ordinary conversation history in `AgenticLoop`'s own `messages` list
  ([loop.py](../../src/unified/loop.py), confirmed no session/state object reaches the plugin at
  all). Structurally, `call_plugin` is indistinguishable from any other flat `TOOL_HANDLERS`
  entry -- it's a smart REST wrapper, not an agent.

An "agent" (by any standard definition -- and by what `AgenticLoop` itself already is for NuCore's
own model) means something that reasons over multiple steps, decides its own next action, and
maintains context across those steps. None of that exists on the plugin side today; all reasoning
happens once, centrally, in NuCore's own model.

## The separable changes, from least to most invasive

### 1. Let a plugin call itself "not done yet" (multi-turn within one task)
Today a plugin call is a single question-answer. The smallest step toward "agentic" is letting a
plugin's response say "I need more information" or "here's my next sub-step" instead of always
being final -- e.g. a new result shape like `{"status": "needs_input", "prompt": "...", "resume_token": "..."}`
that `call_plugin`'s handler recognizes and relays to the customer, then re-invokes the plugin
with the answer + `resume_token` on the next turn. This requires: a small protocol addition
(new response shape, `resume_token` round-trip), no change to trust/security, and no host-loop
architecture change -- `AgenticLoop` already loops turn to turn.

### 2. Give the plugin real context to reason over
Right now the plugin only gets whatever narrow `args` the *host* model already decided to extract
for one named tool -- it never sees the customer's actual request. A more agentic plugin needs
the actual natural-language ask (and possibly relevant DEVICE DATABASE context) to do its own
interpretation, not a pre-resolved parameter set. This is a real, deliberate boundary today (see
`call_plugin`'s docstring: the host does the "picking apart" so the plugin doesn't have to) and
loosening it means a third-party plugin sees more of the customer's actual data/conversation --
a privacy/security tradeoff, not just a technical one.

### 3. Move from "call one of the plugin's tools" to "delegate a task to the plugin"
The qualitative shift from tool to agent: instead of the host model picking `plugin_x_get_holidays`
and calling it with specific args, the host would call something like
`delegate_to_plugin(plugin_id, task_description)` and trust the plugin's *own* backend to figure
out which of its internal capabilities to use, in what order, possibly making several of its own
sub-decisions before returning one final answer. This is where the plugin genuinely needs its own
reasoning loop (its own LLM call(s)) -- which lives entirely in the plugin's own backend, outside
this codebase; NuCore's side of this is just: expose a coarser "delegate" tool instead of (or
alongside) the current fine-grained per-capability tools, and accept a longer-running, more
opaque call (today's 60s timeout in `handle_plugin_llm_result` would likely need to grow or become
async/pollable).

### 4. State that persists across a plugin's own calls
A real agent maintains working memory across steps. Nothing analogous exists per-plugin today.
Cheapest version: start threading a stable `session_id`/`conversation_id` through to
`handle_plugin_llm_result`'s payload, so a plugin *can* persist its own state externally keyed by
that id if it chooses to -- no new storage needed on NuCore's side, just one more field forwarded.
A heavier version would mirror `src/unified/preferences/`'s pattern (a small persisted store)
scoped per plugin instead -- only worth it if plugins can't reasonably manage their own state.

### 5. The trust/authorization question -- the real gate, not a technical detail
This is the one that actually decides how far "agent" can go, and it's a policy call, not code:
should a plugin acting as an agent ever be allowed to call NuCore's *own* core tools directly
(`send_command`, `group_scene_op`, `create_or_update_routine`, ...) as part of its own plan, or
should it only ever return data/suggestions for NuCore's top-level model to act on afterward? The
same tension already surfaced in this codebase's history for a different feature ("Should Plan
types be plugins? Decision: yes for the mechanism, no for the trust model" -- a plan type's
`handle_llm_result` needed privileged write access an arbitrary third-party plugin shouldn't get
for free). A plugin that can only converse and return data is safe to make maximally agentic
internally -- what it does inside its own backend is its own business. A plugin that can *act* on
the installation directly is a different, much bigger security surface, and probably should stay
gated behind NuCore's own model relaying/approving each concrete action, not the plugin calling it
unsupervised.

## Recommendation

Items 1, 2, and 4 are incremental, backward-compatible protocol additions to the existing
`call_plugin`/`handle_plugin_llm_result` contract -- each independently useful, each low-risk.
Item 3 (task delegation) is the actual "becomes an agent" threshold, and it's mostly *not* this
codebase's work -- it's a contract NuCore offers, that individual plugin authors then have to
build a real reasoning loop against. Item 5 isn't a build task at all; it's a decision this
conversation can't make unilaterally in code and should be resolved (at least in direction) before
any of 1-4 are implemented, since it changes what "done" looks like for all of them.

## Suggested next step

This document is the "what's involved" answer. If you want to move forward, the next concrete
decision is scope: pick one of items 1/2/4 to prototype as a real protocol change (smallest,
reversible, no policy dependency), or resolve the item-5 trust question first if you already know
you want to go all the way to task delegation. Happy to turn either into a concrete
files-to-change plan once you pick a direction.
