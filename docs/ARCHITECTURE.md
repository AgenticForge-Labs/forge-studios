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

Provider adapters own transport URLs and provider-specific parameters.

## Boundary frames

`start_only` uses one approved static start image. `start_and_end` additionally uses
an approved destination frame. Generated endpoint inheritance is explicit rather than
inferred from shot adjacency.

Boundary prompts describe static visible compositions. Temporal action and synchronized
audio belong in the video prompt.

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

## Autonomy

Manual and automatic execution use the same primitives. Director applies explicit
permissions for generation, approval, paid video, and retries. Creative revision remains
outside Studios.

## External boundaries

World/canon reasoning belongs in Forge Worlds. Physical robot/stage execution belongs in
Forge Puppeteer. Private experimentation/optimization belongs in Forge Researcher.
