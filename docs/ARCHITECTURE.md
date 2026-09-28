# Forge Studios architecture

Forge Studios is the public deterministic execution side of AgenticForge.

## Contract boundary

```text
Forge Worlds or hand author
        ↓
   EpisodePackage
        ↓
      Director
        ↓
      Animator
        ↓
     Filmmaker
        ↓
  final production
```

There is one current unversioned EpisodePackage:

```text
EpisodePackage
├── beats[]
├── shots[]
└── assets[]
```

The package has no active `scenes[]` hierarchy. Ordered `shots[]` is the narrative
and production sequence. Old scene-based or numbered package contracts are not
execution inputs.

## Director

Director derives execution work from the package. It does not author story. Work plans,
storyboards, timelines, and dependency views are projections and cannot become competing
sources of truth.

## Animator

Animator compiles authored shot intent into provider requests. A shot supplies:

- site/beat identity;
- static boundary prompt(s);
- chronological video prompt;
- approved reference IDs;
- per-reference usage semantics;
- visual constraints;
- frame policy and media lifecycle fields.

Provider adapters own provider-specific parameters that encode deterministic production
intent. AgenticForge AI Runtime owns provider credentials and authenticated network
transport. For fal.ai, Studios compiles the exact request, supplies explicit local assets,
and Runtime performs upload/provider execution without changing prompt semantics.

## Prompt execution

Forge Worlds (or another package author) supplies complete provider-facing shot prompts
inside the EpisodePackage. Forge Studios does not perform semantic prompt compilation.

Studios' job is execution:

```text
package prompt + ordered asset IDs + provider options
        ↓
transport adaptation only
        ↓
provider API
```

Transport adaptation includes resolving local assets to provider-accessible inputs,
binding approved start/end boundary media, mapping duration/model/options, and recording
provenance. It does not include adding camera prose, site geometry, visual constraints,
reference-role prose, audio policy, or fallback creative instructions.

A missing prompt is a package error and is returned upstream rather than repaired here.


## Boundary frames

`start_only` means the shot has no authored outgoing destination frame.
`start_and_end` adds an authored destination frame. Incoming start ownership is a
separate axis: `inherits_start_from_shot_id` binds the immediate predecessor's exact
approved end frame as this shot's start. This allows a shot to inherit a precise start
while remaining `start_only` at its own end.

The predecessor of an inherited start must author an end frame. Inheritance is explicit
rather than inferred from adjacency. Boundary prompts describe static visible
compositions; temporal action and synchronized audio belong in the video prompt.

Forge Worlds may also attach controlled `cinematic_choices` selections to shots. Those
are semantic provenance/inspection data. Studios executes the already-compiled concrete
boundary and prompt contract rather than interpreting the vocabulary itself.

## Reference roles

Reference images are evidence with explicit roles, not generic inspiration. A primary
site view grounds composition; identity references ground character design; supporting
site views may provide distant/background evidence. Studios must preserve the
`reference_uses` supplied upstream.

## Review and provenance

Each generation attempt records provider/model/options, prompts, ordered inputs, outputs,
errors, latency/cost when available, and review decisions. Candidate history is retained.

## Filmmaker

Filmmaker selects approved media in package order and creates deterministic edit/timeline
projections. It does not introduce a second editorial truth.

Episode finishing is two-stage:

```text
approved shot clips
  -> deterministic picture lock (+ optional show-owned end card)
  -> provider-generated music stem
  -> deterministic Studios audio mix
  -> final master
```

The show repository owns reusable finishing identity such as logo asset IDs and the
constant score prompt. Studios owns execution: resolve those stable assets, render the
end card, submit the complete picture lock to the configured music provider, preserve the
returned stem/provenance, and mix it beneath the generated clip audio. The default
configured Sonilo route uses the show's constant style prompt; video-only and custom
prompt reruns use the same scoring primitive.

Ordinary shot boundaries remain hard cuts by default. End-card fades are deterministic
finishing behavior, not a reason to add semantic editing decisions inside Studios.

## Autonomy

Manual and automatic execution use the same primitives. Director applies explicit
permissions for generation, approval, paid video, and retries. Creative revision remains
outside Studios.

## Shared evidence and research lineage

The complete local Studios JSONL telemetry remains the authoritative execution log.
When configured, Studios mirrors each event into Platform's append-oriented event stream.
Platform stores and links the data but does not reinterpret production semantics.

Repeated prompt strings remain present in the local event and are stored once centrally
as content-addressed evidence. Central events reference that evidence by ID/hash instead
of duplicating prompt bodies. Stable production, episode, shot, attempt, asset, and render
identity remains directly queryable.

AI Runtime receives the same production lineage for fal image/video and finishing calls,
so its exact provider request/response and execution usage/cost can be joined to Studios
attempt/review/render outcomes.

Forge Researcher consumes these authoritative producer records and external outcome
observations to build derived research tables, experiments, datasets, and models. Those
derivatives do not replace Studios production history.

## Shared provider execution

```text
EpisodePackage / finishing profile
        ↓
Forge Studios deterministic provider request
        ↓
AgenticForge AI Runtime
        ↓
       fal.ai
```

Animator image/video generation and Filmmaker Sonilo scoring use the same Runtime media
boundary. Studios retains provider-specific request assembly, media interpretation,
downloads, review, telemetry, and final deterministic assembly. Runtime centralizes
credentials and execution only.

## External boundaries

World/canon reasoning belongs in Forge Worlds. Physical robot/stage execution belongs in
Forge Puppeteer. Private experimentation/optimization belongs in Forge Researcher.
