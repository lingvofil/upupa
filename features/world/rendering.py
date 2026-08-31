"""Pillow renderer for the World of Upupa diplomacy map."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from io import BytesIO
import math

from PIL import Image, ImageDraw, ImageFont

from features.world.ledger import WorldRelation
from features.world.models import WorldState


CANVAS_WIDTH = 1400
CANVAS_HEIGHT = 1000
CENTER = (CANVAS_WIDTH / 2, CANVAS_HEIGHT / 2 + 25)
ORBIT_X = 500
ORBIT_Y = 330


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


def _positions(states: tuple[WorldState, ...]) -> dict[int, tuple[float, float]]:
    ordered = sorted(states, key=lambda state: state.world_id)
    if not ordered:
        return {}
    if len(ordered) == 1:
        return {ordered[0].world_id: CENTER}

    positions: dict[int, tuple[float, float]] = {}
    for index, state in enumerate(ordered):
        angle = -math.pi / 2 + 2 * math.pi * index / len(ordered)
        positions[state.world_id] = (
            CENTER[0] + ORBIT_X * math.cos(angle),
            CENTER[1] + ORBIT_Y * math.sin(angle),
        )
    return positions


def _authority(states: tuple[WorldState, ...], relations: tuple[WorldRelation, ...]) -> dict[int, int]:
    allies: dict[int, int] = defaultdict(int)
    wars: dict[int, int] = defaultdict(int)
    for relation in relations:
        target = allies if relation.relation == "allied" else wars
        target[relation.state_a] += 1
        target[relation.state_b] += 1
    return {
        state.world_id: max(0, min(100, 50 + 8 * allies[state.world_id] - 5 * wars[state.world_id]))
        for state in states
    }


def _short_title(title: str, limit: int = 24) -> str:
    compact = " ".join(title.split())
    return compact if len(compact) <= limit else compact[: limit - 1].rstrip() + "…"


def render_world_map_png(
    states: tuple[WorldState, ...],
    relations: tuple[WorldRelation, ...],
) -> bytes:
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

    positions = _positions(states)
    authority = _authority(states, relations)

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
        score = authority[state.world_id]
        radius = 42 + score * 0.16
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

        label = _short_title(state.title)
        lb = draw.textbbox((0, 0), label, font=small_font)
        tw = lb[2] - lb[0]
        th = lb[3] - lb[1]
        ty = y + radius + 10
        draw.rounded_rectangle(
            (x - tw / 2 - 7, ty - 4, x + tw / 2 + 7, ty + th + 4),
            radius=6,
            fill=(255, 255, 255, 235),
        )
        draw.text((x - tw / 2, ty), label, font=small_font, fill=(25, 25, 30, 255))

    legend_y = CANVAS_HEIGHT - 70
    draw.line([(70, legend_y), (130, legend_y)], fill=(53, 145, 86, 230), width=8)
    draw.text((145, legend_y - 12), "союз", font=small_font, fill=(30, 30, 35, 255))
    draw.line([(300, legend_y), (360, legend_y)], fill=(190, 55, 55, 235), width=10)
    draw.text((375, legend_y - 12), "война", font=small_font, fill=(30, 30, 35, 255))
    draw.text((560, legend_y - 12), "число внутри государства = международный авторитет", font=small_font, fill=(70, 70, 75, 255))

    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()


async def render_world_map_png_async(
    states: tuple[WorldState, ...],
    relations: tuple[WorldRelation, ...],
) -> bytes:
    return await asyncio.to_thread(render_world_map_png, states, relations)
