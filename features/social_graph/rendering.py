"""PNG rendering for social graphs using Pillow only."""

from __future__ import annotations

import asyncio
from io import BytesIO
import math
import random

from PIL import Image, ImageDraw, ImageFont, ImageOps

from features.social_graph.analysis import RenderGraph, rank_central_participants


CANVAS_WIDTH = 1200
CANVAS_HEIGHT = 900
MARGIN = 100
ASYMMETRY_RATIO = 1.75

CRINGE_AVATAR_SIZE = 170
CRINGE_AVATAR_RADIUS = CRINGE_AVATAR_SIZE // 2
CRINGE_LABEL_MAX_WIDTH = 270


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


def _extract_cringe_portraits(portrait_sheet: bytes) -> list[Image.Image]:
    """Split the AI 3x2 sprite sheet into six square portrait crops."""
    sheet = Image.open(BytesIO(portrait_sheet)).convert("RGB")
    cell_w = sheet.width / 3
    cell_h = sheet.height / 2
    side = max(1, int(min(cell_w, cell_h) * 0.82))
    portraits: list[Image.Image] = []

    for row in range(2):
        for col in range(3):
            center_x = (col + 0.5) * cell_w
            center_y = (row + 0.5) * cell_h
            box = (
                round(center_x - side / 2),
                round(center_y - side / 2),
                round(center_x + side / 2),
                round(center_y + side / 2),
            )
            crop = sheet.crop(box)
            portraits.append(
                ImageOps.fit(
                    crop,
                    (CRINGE_AVATAR_SIZE, CRINGE_AVATAR_SIZE),
                    method=Image.Resampling.LANCZOS,
                )
            )

    return portraits


def _ordered_cringe_nodes(graph: RenderGraph):
    """Put the most central participant first so it always occupies the middle slot."""
    ranking = rank_central_participants(graph.edges, limit=1)
    central_id = ranking[0].user_id if ranking else graph.nodes[0].user_id
    by_id = {node.user_id: node for node in graph.nodes}
    central = by_id.get(central_id, graph.nodes[0])
    return (central,) + tuple(node for node in graph.nodes if node.user_id != central.user_id)


def _cringe_positions(graph: RenderGraph) -> tuple[dict[int, tuple[float, float]], int]:
    ordered = _ordered_cringe_nodes(graph)
    outer_count = max(0, len(ordered) - 1)
    outer_slots = {
        0: (),
        1: ((600, 155),),
        2: ((315, 260), (885, 260)),
        3: ((600, 135), (930, 625), (270, 625)),
        4: ((600, 130), (965, 420), (600, 710), (235, 420)),
        5: ((600, 125), (965, 325), (835, 700), (365, 700), (235, 325)),
    }[outer_count]

    positions = {ordered[0].user_id: (600, 440)}
    for node, slot in zip(ordered[1:], outer_slots):
        positions[node.user_id] = slot
    return positions, ordered[0].user_id


def _shorten_edge(start, end, radius: float = CRINGE_AVATAR_RADIUS + 13):
    sx, sy = start
    ex, ey = end
    dx, dy = ex - sx, ey - sy
    distance = math.hypot(dx, dy)
    if distance <= radius * 2 + 1:
        return start, end
    ux, uy = dx / distance, dy / distance
    return (
        (sx + ux * radius, sy + uy * radius),
        (ex - ux * radius, ey - uy * radius),
    )


def _crude_edge_points(start, end, seed: int):
    sx, sy = start
    ex, ey = end
    dx, dy = ex - sx, ey - sy
    distance = max(math.hypot(dx, dy), 1.0)
    px, py = -dy / distance, dx / distance
    rng = random.Random(seed)
    bend1 = rng.uniform(-24, 24)
    bend2 = rng.uniform(-24, 24)
    return [
        start,
        (sx + dx * 0.34 + px * bend1, sy + dy * 0.34 + py * bend1),
        (sx + dx * 0.68 + px * bend2, sy + dy * 0.68 + py * bend2),
        end,
    ]


def _draw_crude_arrowhead(
    draw: ImageDraw.ImageDraw,
    start,
    end,
    width: int,
    scale: float,
) -> None:
    sx, sy = start
    ex, ey = end
    dx, dy = ex - sx, ey - sy
    distance = math.hypot(dx, dy)
    if distance < 1:
        return
    ux, uy = dx / distance, dy / distance
    px, py = -uy, ux
    length = (18 + width) * scale
    half = (8 + width * 0.65) * scale
    base_x = ex - ux * length
    base_y = ey - uy * length
    draw.polygon(
        [
            (ex, ey),
            (base_x + px * half, base_y + py * half),
            (base_x - px * half, base_y - py * half),
        ],
        fill=(35, 35, 35, 245),
    )


def _draw_cringe_edge(draw: ImageDraw.ImageDraw, edge, positions, max_edge: float) -> None:
    start, end = _shorten_edge(positions[edge.user_a], positions[edge.user_b])
    width = max(4, min(12, round(4 + 8 * math.sqrt(edge.total_weight / max_edge))))
    seed = edge.user_a * 1_000_003 + edge.user_b * 97
    points = _crude_edge_points(start, end, seed)

    draw.line(points, fill=(255, 255, 255, 235), width=width + 5, joint="curve")
    draw.line(points, fill=(35, 35, 35, 235), width=width, joint="curve")

    high = max(edge.a_to_b, edge.b_to_a, 0.001)
    forward_ratio = edge.a_to_b / high
    backward_ratio = edge.b_to_a / high
    if forward_ratio >= 0.22:
        _draw_crude_arrowhead(draw, points[-2], points[-1], width, 0.75 + 0.3 * forward_ratio)
    if backward_ratio >= 0.22:
        _draw_crude_arrowhead(draw, points[1], points[0], width, 0.75 + 0.3 * backward_ratio)


def _draw_cringe_label(draw: ImageDraw.ImageDraw, center, label: str) -> None:
    label = " ".join(str(label or "Участник").split())
    x, y = center
    font = _load_font(26)
    for size in range(26, 11, -1):
        candidate = _load_font(size)
        bbox = draw.textbbox((0, 0), label, font=candidate)
        if bbox[2] - bbox[0] <= CRINGE_LABEL_MAX_WIDTH:
            font = candidate
            break

    bbox = draw.textbbox((0, 0), label, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    tx = x - text_w / 2
    ty = y + CRINGE_AVATAR_RADIUS + 10
    pad_x, pad_y = 8, 5
    draw.rounded_rectangle(
        (tx - pad_x, ty - pad_y, tx + text_w + pad_x, ty + text_h + pad_y),
        radius=6,
        fill=(250, 248, 241, 245),
        outline=(40, 40, 40, 235),
        width=2,
    )
    draw.text((tx, ty), label, font=font, fill=(25, 25, 25, 255))


def _draw_cringe_crown(draw: ImageDraw.ImageDraw, center) -> None:
    x, y = center
    base_y = y - CRINGE_AVATAR_RADIUS + 16
    points = [
        (x - 52, base_y),
        (x - 45, base_y - 34),
        (x - 17, base_y - 13),
        (x, base_y - 46),
        (x + 18, base_y - 13),
        (x + 46, base_y - 34),
        (x + 52, base_y),
    ]
    draw.polygon(points, fill=(248, 196, 52, 255))
    draw.line(points + [points[0]], fill=(35, 35, 35, 255), width=4, joint="curve")


def render_cringe_graph_png(graph: RenderGraph, portrait_sheet: bytes) -> bytes:
    """Compose an exact graph using anonymous AI portraits as the only generated artwork."""
    if not graph.nodes or not graph.edges:
        raise ValueError("Cannot render an empty social graph")
    if len(graph.nodes) > 6:
        raise ValueError("Cringe social graph supports at most six participants")

    portraits = _extract_cringe_portraits(portrait_sheet)
    ordered_nodes = _ordered_cringe_nodes(graph)
    positions, central_id = _cringe_positions(graph)
    image = Image.new("RGB", (CANVAS_WIDTH, CANVAS_HEIGHT), (245, 244, 239))
    draw = ImageDraw.Draw(image, "RGBA")
    max_edge = max(edge.total_weight for edge in graph.edges)

    for edge in graph.edges:
        if edge.user_a in positions and edge.user_b in positions:
            _draw_cringe_edge(draw, edge, positions, max_edge)

    mask = Image.new("L", (CRINGE_AVATAR_SIZE, CRINGE_AVATAR_SIZE), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.ellipse((2, 2, CRINGE_AVATAR_SIZE - 3, CRINGE_AVATAR_SIZE - 3), fill=255)

    for index, node in enumerate(ordered_nodes):
        x, y = positions[node.user_id]
        portrait = portraits[index]
        image.paste(
            portrait,
            (round(x - CRINGE_AVATAR_RADIUS), round(y - CRINGE_AVATAR_RADIUS)),
            mask,
        )
        draw.ellipse(
            (
                x - CRINGE_AVATAR_RADIUS,
                y - CRINGE_AVATAR_RADIUS,
                x + CRINGE_AVATAR_RADIUS,
                y + CRINGE_AVATAR_RADIUS,
            ),
            outline=(35, 35, 35, 245),
            width=4,
        )
        if node.user_id == central_id:
            _draw_cringe_crown(draw, (x, y))
        _draw_cringe_label(draw, (x, y), node.label)

    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()


async def render_graph_png_async(graph: RenderGraph) -> bytes:
    return await asyncio.to_thread(render_graph_png, graph)


async def render_cringe_graph_png_async(graph: RenderGraph, portrait_sheet: bytes) -> bytes:
    return await asyncio.to_thread(render_cringe_graph_png, graph, portrait_sheet)
