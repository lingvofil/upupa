"""PNG rendering for a compact social graph using Pillow only."""

from __future__ import annotations

import asyncio
from io import BytesIO
import math
import random

from PIL import Image, ImageDraw, ImageFont

from features.social_graph.analysis import RenderGraph


CANVAS_WIDTH = 1200
CANVAS_HEIGHT = 900
MARGIN = 100
ASYMMETRY_RATIO = 1.75


def _load_font(size: int):
    for candidate in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "DejaVuSans.ttf",
    ):
        try:
            return ImageFont.truetype(candidate, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _force_layout(graph: RenderGraph) -> dict[int, tuple[float, float]]:
    ids = [node.user_id for node in graph.nodes]
    n = len(ids)
    if n == 0:
        return {}
    if n == 1:
        return {ids[0]: (CANVAS_WIDTH / 2, CANVAS_HEIGHT / 2)}

    rng = random.Random(42)
    positions = {
        user_id: (
            MARGIN + rng.random() * (CANVAS_WIDTH - 2 * MARGIN),
            MARGIN + rng.random() * (CANVAS_HEIGHT - 2 * MARGIN),
        )
        for user_id in ids
    }
    area = (CANVAS_WIDTH - 2 * MARGIN) * (CANVAS_HEIGHT - 2 * MARGIN)
    k = math.sqrt(area / n)
    edge_weights = {(edge.user_a, edge.user_b): edge.total_weight for edge in graph.edges}
    max_weight = max(edge_weights.values(), default=1.0)

    temperature = min(CANVAS_WIDTH, CANVAS_HEIGHT) / 8
    for _iteration in range(80):
        displacement = {user_id: [0.0, 0.0] for user_id in ids}

        for index, v in enumerate(ids):
            x_v, y_v = positions[v]
            for u in ids[index + 1 :]:
                x_u, y_u = positions[u]
                dx = x_v - x_u
                dy = y_v - y_u
                distance = max(math.hypot(dx, dy), 0.01)
                force = (k * k) / distance
                fx = dx / distance * force
                fy = dy / distance * force
                displacement[v][0] += fx
                displacement[v][1] += fy
                displacement[u][0] -= fx
                displacement[u][1] -= fy

        for edge in graph.edges:
            v, u = edge.user_a, edge.user_b
            x_v, y_v = positions[v]
            x_u, y_u = positions[u]
            dx = x_v - x_u
            dy = y_v - y_u
            distance = max(math.hypot(dx, dy), 0.01)
            normalized = 0.7 + 1.3 * (edge.total_weight / max_weight)
            force = (distance * distance / k) * normalized
            fx = dx / distance * force
            fy = dy / distance * force
            displacement[v][0] -= fx
            displacement[v][1] -= fy
            displacement[u][0] += fx
            displacement[u][1] += fy

        for user_id in ids:
            dx, dy = displacement[user_id]
            magnitude = max(math.hypot(dx, dy), 0.01)
            x, y = positions[user_id]
            step = min(magnitude, temperature)
            x += dx / magnitude * step
            y += dy / magnitude * step
            positions[user_id] = (
                min(CANVAS_WIDTH - MARGIN, max(MARGIN, x)),
                min(CANVAS_HEIGHT - MARGIN, max(MARGIN, y)),
            )
        temperature *= 0.94

    return positions


def _draw_arrowhead(draw: ImageDraw.ImageDraw, start, end, width: int) -> None:
    sx, sy = start
    ex, ey = end
    dx, dy = ex - sx, ey - sy
    distance = math.hypot(dx, dy)
    if distance < 1:
        return
    ux, uy = dx / distance, dy / distance
    px, py = -uy, ux
    tip_x = ex - ux * 25
    tip_y = ey - uy * 25
    length = 12 + width
    half = 5 + width / 2
    base_x = tip_x - ux * length
    base_y = tip_y - uy * length
    points = [
        (tip_x, tip_y),
        (base_x + px * half, base_y + py * half),
        (base_x - px * half, base_y - py * half),
    ]
    draw.polygon(points, fill=(95, 100, 110, 220))


def render_graph_png(graph: RenderGraph) -> bytes:
    if not graph.nodes or not graph.edges:
        raise ValueError("Cannot render an empty social graph")

    image = Image.new("RGB", (CANVAS_WIDTH, CANVAS_HEIGHT), "white")
    draw = ImageDraw.Draw(image, "RGBA")
    positions = _force_layout(graph)
    max_edge = max(edge.total_weight for edge in graph.edges)
    max_node = max(node.strength for node in graph.nodes)

    for edge in graph.edges:
        start = positions[edge.user_a]
        end = positions[edge.user_b]
        width = max(2, min(12, round(2 + 10 * math.sqrt(edge.total_weight / max_edge))))
        draw.line([start, end], fill=(120, 125, 135, 150), width=width)

        forward = edge.a_to_b
        backward = edge.b_to_a
        if forward > 0 and forward >= ASYMMETRY_RATIO * max(backward, 0.001):
            _draw_arrowhead(draw, start, end, width)
        elif backward > 0 and backward >= ASYMMETRY_RATIO * max(forward, 0.001):
            _draw_arrowhead(draw, end, start, width)

    node_font = _load_font(18)
    for node in graph.nodes:
        x, y = positions[node.user_id]
        radius = 20 + 15 * math.sqrt(node.strength / max_node)
        draw.ellipse(
            (x - radius, y - radius, x + radius, y + radius),
            fill=(244, 177, 74, 245),
            outline=(70, 70, 75, 255),
            width=2,
        )
        label = node.label if len(node.label) <= 24 else node.label[:22] + "…"
        bbox = draw.textbbox((0, 0), label, font=node_font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
        tx = x - text_w / 2
        ty = y + radius + 7
        pad = 4
        draw.rounded_rectangle(
            (tx - pad, ty - pad, tx + text_w + pad, ty + text_h + pad),
            radius=5,
            fill=(255, 255, 255, 225),
        )
        draw.text((tx, ty), label, font=node_font, fill=(30, 30, 35, 255))

    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()


async def render_graph_png_async(graph: RenderGraph) -> bytes:
    return await asyncio.to_thread(render_graph_png, graph)
