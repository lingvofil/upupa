"""Pillow renderer for the World of Upupa diplomacy map.

States are placed with a small deterministic force-directed layout: allies
attract strongly, enemies weakly, everybody repels everybody.  Alliances
therefore cluster instead of all lines crossing the centre of a circle.  A
state's flag (or emblem) is drawn inside its node; bloc members get a coloured
ring.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from io import BytesIO
import math

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps

from features.world.authority import authority_from_counts
from features.world.ledger import WorldRelation
from features.world.models import WorldState


CANVAS_WIDTH = 1400
CANVAS_HEIGHT = 1000
MARGIN_X = 150
MARGIN_TOP = 140
MARGIN_BOTTOM = 170
_BLOC_COLORS = (
    (66, 133, 244),
    (171, 71, 188),
    (0, 150, 136),
    (255, 112, 67),
    (121, 85, 72),
    (233, 30, 99),
)


def _load_font(size: int, *, bold: bool = False):
    candidates = (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf",
    )
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _force_layout(
    ids: list[int],
    relations: tuple[WorldRelation, ...],
    *,
    iterations: int = 300,
) -> dict[int, tuple[float, float]]:
    """Deterministic Fruchterman–Reingold layout in the unit square."""
    count = len(ids)
    if count == 0:
        return {}
    if count == 1:
        return {ids[0]: (0.5, 0.5)}
    index = {world_id: position for position, world_id in enumerate(ids)}
    angles = np.linspace(-math.pi / 2, 3 * math.pi / 2, count, endpoint=False)
    pos = np.column_stack((0.5 + 0.4 * np.cos(angles), 0.5 + 0.4 * np.sin(angles)))
    weights = np.zeros((count, count))
    for relation in relations:
        if relation.state_a not in index or relation.state_b not in index:
            continue
        weight = 1.0 if relation.relation == "allied" else 0.25
        a, b = index[relation.state_a], index[relation.state_b]
        weights[a, b] = weights[b, a] = weight
    k = math.sqrt(1.0 / count)
    temperature = 0.12
    for _ in range(iterations):
        delta = pos[:, None, :] - pos[None, :, :]
        distance = np.linalg.norm(delta, axis=2)
        np.fill_diagonal(distance, 1.0)
        distance = np.maximum(distance, 0.01)
        repulsive = (k * k / distance**2)[:, :, None] * delta
        attractive = (weights * distance / k)[:, :, None] * delta
        displacement = (repulsive - attractive).sum(axis=1)
        # gentle pull to the centre keeps disconnected states on the canvas
        displacement += (0.5 - pos) * 0.15
        length = np.maximum(np.linalg.norm(displacement, axis=1), 1e-9)
        pos += displacement / length[:, None] * np.minimum(length, temperature)[:, None]
        temperature = max(0.002, temperature * 0.985)
    low = pos.min(axis=0)
    span = pos.max(axis=0) - low
    normalized = (pos - low) / np.maximum(span, 1e-6)
    for axis in range(2):
        if span[axis] < 1e-3:
            normalized[:, axis] = 0.5
    return {world_id: (float(normalized[index[world_id], 0]), float(normalized[index[world_id], 1])) for world_id in ids}


def _separate(points: np.ndarray, *, min_distance: float, bounds: tuple[float, float, float, float]) -> np.ndarray:
    """Push nodes apart until no two are closer than ``min_distance`` (in pixels)."""
    left, top, right, bottom = bounds
    points = points.copy()
    for _ in range(400):
        moved = False
        for i in range(len(points)):
            for j in range(i + 1, len(points)):
                delta = points[j] - points[i]
                distance = float(np.hypot(*delta))
                if distance >= min_distance:
                    continue
                if distance < 1e-6:
                    delta = np.array([math.cos(i + j), math.sin(i + j)])
                    distance = 1.0
                push = (min_distance - distance) / 2 * delta / distance
                points[i] -= push
                points[j] += push
                moved = True
        points[:, 0] = np.clip(points[:, 0], left, right)
        points[:, 1] = np.clip(points[:, 1], top, bottom)
        if not moved:
            break
    return points


def _positions(states: tuple[WorldState, ...], relations: tuple[WorldRelation, ...] = ()) -> dict[int, tuple[float, float]]:
    ids = sorted(state.world_id for state in states)
    if len(ids) == 1:
        return {ids[0]: (CANVAS_WIDTH / 2, CANVAS_HEIGHT / 2)}
    unit = _force_layout(ids, relations)
    width = CANVAS_WIDTH - 2 * MARGIN_X
    height = CANVAS_HEIGHT - MARGIN_TOP - MARGIN_BOTTOM
    points = np.array([[MARGIN_X + unit[i][0] * width, MARGIN_TOP + unit[i][1] * height] for i in ids])
    # Smaller worlds get roomier spacing; big ones fall back to what fits.
    min_distance = max(140.0, min(260.0, math.sqrt(width * height / len(ids)) * 0.75))
    points = _separate(
        points,
        min_distance=min_distance,
        bounds=(MARGIN_X, MARGIN_TOP, CANVAS_WIDTH - MARGIN_X, CANVAS_HEIGHT - MARGIN_BOTTOM),
    )
    return {world_id: (float(points[n, 0]), float(points[n, 1])) for n, world_id in enumerate(ids)}


def _authority(states: tuple[WorldState, ...], relations: tuple[WorldRelation, ...]) -> dict[int, int]:
    allies: dict[int, int] = defaultdict(int)
    wars: dict[int, int] = defaultdict(int)
    for relation in relations:
        target = allies if relation.relation == "allied" else wars
        target[relation.state_a] += 1
        target[relation.state_b] += 1
    return {
        state.world_id: authority_from_counts(allies[state.world_id], wars[state.world_id])
        for state in states
    }


def _short_title(title: str, limit: int = 24) -> str:
    compact = " ".join(title.split())
    return compact if len(compact) <= limit else compact[: limit - 1].rstrip() + "…"


def _circle_image(raw: bytes, diameter: int) -> Image.Image | None:
    try:
        source = Image.open(BytesIO(raw)).convert("RGB")
    except Exception:
        return None
    fitted = ImageOps.fit(source, (diameter, diameter), method=Image.LANCZOS)
    mask = Image.new("L", (diameter, diameter), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, diameter - 1, diameter - 1), fill=255)
    fitted.putalpha(mask)
    return fitted


def render_world_map_png(
    states: tuple[WorldState, ...],
    relations: tuple[WorldRelation, ...],
    *,
    authority: dict[int, int] | None = None,
    flags: dict[int, bytes] | None = None,
    blocs: dict[int, int] | None = None,
) -> bytes:
    """Render the map; ``authority`` totals, ``flags`` images and ``blocs`` (state → bloc id) are optional."""
    if not states:
        raise ValueError("Cannot render world map without active states")

    image = Image.new("RGB", (CANVAS_WIDTH, CANVAS_HEIGHT), "white")
    draw = ImageDraw.Draw(image, "RGBA")
    title_font = _load_font(42, bold=True)
    node_font = _load_font(22, bold=True)
    small_font = _load_font(18)
    alliance_font = _load_font(16)

    title = "МИР УПУПЫ — дипломатическая карта"
    bbox = draw.textbbox((0, 0), title, font=title_font)
    draw.text(((CANVAS_WIDTH - (bbox[2] - bbox[0])) / 2, 30), title, font=title_font, fill=(25, 25, 30, 255))

    positions = _positions(states, relations)
    scores = authority if authority is not None else _authority(states, relations)
    bloc_colors: dict[int, tuple[int, int, int]] = {}
    for bloc_id in sorted(set((blocs or {}).values())):
        bloc_colors[bloc_id] = _BLOC_COLORS[len(bloc_colors) % len(_BLOC_COLORS)]

    for relation in relations:
        if relation.state_a not in positions or relation.state_b not in positions:
            continue
        start = positions[relation.state_a]
        end = positions[relation.state_b]
        if relation.relation == "allied":
            color = (53, 145, 86, 210)
            width = 8
        else:
            color = (190, 55, 55, 225)
            width = 10
        draw.line([start, end], fill=color, width=width)

        if relation.relation == "allied" and relation.alliance_name:
            label = f"«{_short_title(relation.alliance_name, 28)}»"
            mx = (start[0] + end[0]) / 2
            my = (start[1] + end[1]) / 2
            lb = draw.textbbox((0, 0), label, font=alliance_font)
            tw = lb[2] - lb[0]
            th = lb[3] - lb[1]
            draw.rounded_rectangle(
                (mx - tw / 2 - 7, my - th / 2 - 5, mx + tw / 2 + 7, my + th / 2 + 5),
                radius=6,
                fill=(255, 255, 255, 235),
                outline=(53, 145, 86, 190),
                width=2,
            )
            draw.text((mx - tw / 2, my - th / 2), label, font=alliance_font, fill=(30, 90, 50, 255))

    for state in states:
        x, y = positions[state.world_id]
        score = int(scores.get(state.world_id, 50))
        # Authority is unbounded, but node geometry is intentionally bounded so
        # a diplomatic superpower does not eat the whole map.
        radius = 42 + min(score, 180) * 0.11
        bloc_id = (blocs or {}).get(state.world_id)
        if bloc_id is not None:
            ring = bloc_colors[bloc_id]
            draw.ellipse(
                (x - radius - 9, y - radius - 9, x + radius + 9, y + radius + 9),
                outline=(*ring, 255),
                width=6,
            )
        flag = _circle_image(flags[state.world_id], int(radius * 2)) if flags and state.world_id in flags else None
        if flag is not None:
            image.paste(flag, (int(x - radius), int(y - radius)), flag)
            draw.ellipse(
                (x - radius, y - radius, x + radius, y + radius),
                outline=(55, 55, 65, 255),
                width=3,
            )
            badge = f"№{state.world_id} · {score}"
            bb = draw.textbbox((0, 0), badge, font=small_font)
            bw = bb[2] - bb[0]
            bh = bb[3] - bb[1]
            by = y + radius - bh / 2
            draw.rounded_rectangle(
                (x - bw / 2 - 8, by - 4, x + bw / 2 + 8, by + bh + 6),
                radius=8,
                fill=(238, 184, 77, 245),
                outline=(55, 55, 65, 255),
                width=2,
            )
            draw.text((x - bw / 2, by), badge, font=small_font, fill=(30, 30, 35, 255))
            label_top = by + bh + 14
        else:
            draw.ellipse(
                (x - radius, y - radius, x + radius, y + radius),
                fill=(238, 184, 77, 250),
                outline=(55, 55, 65, 255),
                width=3,
            )
            number = f"№{state.world_id}"
            nb = draw.textbbox((0, 0), number, font=node_font)
            draw.text((x - (nb[2] - nb[0]) / 2, y - 24), number, font=node_font, fill=(30, 30, 35, 255))
            score_text = f"{score}"
            sb = draw.textbbox((0, 0), score_text, font=small_font)
            draw.text((x - (sb[2] - sb[0]) / 2, y + 8), score_text, font=small_font, fill=(50, 50, 55, 255))
            label_top = y + radius + 10

        label = _short_title(state.title)
        lb = draw.textbbox((0, 0), label, font=small_font)
        tw = lb[2] - lb[0]
        th = lb[3] - lb[1]
        draw.rounded_rectangle(
            (x - tw / 2 - 7, label_top - 4, x + tw / 2 + 7, label_top + th + 4),
            radius=6,
            fill=(255, 255, 255, 235),
        )
        draw.text((x - tw / 2, label_top), label, font=small_font, fill=(25, 25, 30, 255))

    legend_y = CANVAS_HEIGHT - 60
    draw.line([(70, legend_y), (130, legend_y)], fill=(53, 145, 86, 230), width=8)
    draw.text((145, legend_y - 12), "союз", font=small_font, fill=(30, 30, 35, 255))
    draw.line([(260, legend_y), (320, legend_y)], fill=(190, 55, 55, 235), width=10)
    draw.text((335, legend_y - 12), "война", font=small_font, fill=(30, 30, 35, 255))
    legend_x = 460
    if bloc_colors:
        draw.ellipse((legend_x, legend_y - 14, legend_x + 28, legend_y + 14), outline=(*_BLOC_COLORS[0], 255), width=5)
        draw.text((legend_x + 40, legend_y - 12), "кольцо — блок", font=small_font, fill=(30, 30, 35, 255))
        legend_x += 230
    draw.text((legend_x, legend_y - 12), "число = международный авторитет", font=small_font, fill=(70, 70, 75, 255))

    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()


async def render_world_map_png_async(
    states: tuple[WorldState, ...],
    relations: tuple[WorldRelation, ...],
    **kwargs,
) -> bytes:
    return await asyncio.to_thread(render_world_map_png, states, relations, **kwargs)
