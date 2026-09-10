"""Prompt construction for the intentionally crude AI portrait sheet."""

from __future__ import annotations

from features.social_graph.analysis import RenderGraph


MAX_CRINGE_NODES = 6
MAX_CRINGE_EDGES = 7


def build_cringe_social_graph_prompt(view: RenderGraph, period_days: int) -> str:
    """Build a text-free six-portrait sprite sheet prompt.

    The actual graph structure, participant labels and centrality are rendered
    deterministically by Pillow afterwards.  The image model is deliberately
    responsible only for the ugly faces.
    """
    del view, period_days
    return """Create a single sprite sheet with EXACTLY SIX separate ugly amateur doodle portraits.

LAYOUT:
- invisible grid: 3 columns x 2 rows;
- exactly one character centered in each cell;
- head and upper torso only, with generous blank space around every character;
- all six characters must look visibly different from each other.

STYLE:
- crude old bitmap-paint-program doodles made clumsily with a mouse;
- wobbly black outlines, bad anatomy, uneven strokes, primitive flat colors;
- goofy awkward facial expressions, intentionally cheap and amateurish;
- plain white or slightly dirty off-white background;
- do NOT make it polished, cute, professional, realistic, 3D, glossy or vector-clean.

ABSOLUTELY NO TEXT OR DIAGRAM ELEMENTS:
- no letters, words, numbers, names, labels, captions, titles, logos or watermarks;
- no speech bubbles, thought bubbles, signs, cards or screens;
- no arrows, connector lines, charts, graphs, legends or symbols;
- do not connect the six portraits to each other.

This is only a source sheet of six anonymous doodle portraits. Nothing else.""".strip()
