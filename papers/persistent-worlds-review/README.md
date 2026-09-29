# Resolving Persistent Worlds review

This folder contains an arXiv-ready LaTeX review/perspective manuscript:

**Resolving Persistent Worlds Through Agentic Story and Video Creation: A Review of Narrative Agents, Cinematic Systems, 3D Simulation, and Generative World Models**

## Files

- `main.tex` — manuscript source.
- `references.bib` — verified bibliography from primary arXiv, ACL, and AAAI records.
- `arxiv.sty` — George Kour's arXiv-style preprint template, used under its MIT license.
- `ARXIV_STYLE_LICENSE.txt` — upstream template license.

The arXiv style is a community preprint template, not an official arXiv submission requirement.

## Build

From this directory:

```bash
latexmk -pdf main.tex
```

or with a conventional BibTeX sequence:

```bash
pdflatex main.tex
bibtex main
pdflatex main.tex
pdflatex main.tex
```

For Overleaf, import/sync the folder and set `main.tex` as the main document.

## Scope

The manuscript is a narrative systems review, current through September 2026. It connects four literatures:

1. agentic story generation, role-playing agents, and fictional worldbuilding;
2. agentic filmmaking, multi-shot continuity, and automated video review/editing;
3. LLM-controlled 3D creation and physics-grounded simulation; and
4. interactive video/world models and generative game engines.

Its synthesis is that these strands are converging on persistent generative worlds with explicit state, action, observation, and reconciliation boundaries.