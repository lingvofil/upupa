"""Replay the 60 Hz Sasulka controls on the server before awarding points.

Keep the numeric rules in sync with assets/sasulka_app/drop-engine.js.
"""

from math import cos, floor, hypot, pi, sin


STEP = 1 / 60
MAX_TICKS = 4500
ROAD = 448
# In selector order, which also determines the initial state of the seeded generator.
SPEEDS = {"record": 21, "bell": 34, "branches": 33, "bridge": 37, "asphalt": 28,
          "lantern": 34, "arch": 33, "alley": 37, "mouth": 35}
LEVEL_IDS = tuple(SPEEDS)
# Gates, gap, horizontal shift, target radius, wind multiplier, hold strength, points.
DIFFICULTIES = {
    "easy": (2, 82, 69, 16, .84, 2.16, 30),
    "normal": (3, 61, 88, 11, 1, 2.04, 70),
    "hard": (4, 43, 82, 8, 1.1, 2.48, 120),
}
GUSTS = ((7.5, 2.1, .84), (14.6, 2.55, .97), (22, 2.35, .9), (28, 1.85, .75))


def _round(value):
    return floor(value + .5)


def _clamp(value, low, high):
    return min(high, max(low, value))


def _course(level, difficulty, seed):
    if level == "record":
        return 200, 82, 1, 1, 0, (), None

    gate_count, gap, offset, radius, wind, strength, _ = DIFFICULTIES[difficulty]
    random_state = seed + LEVEL_IDS.index(level) * 8461

    def random():
        nonlocal random_state
        random_state = (1664525 * random_state + 1013904223) & 0xffffffff
        return random_state / 4294967296

    target_x = 308 if level in {"bridge", "alley"} else _round(166 + random() * 103)
    target_y = ROAD if level == "asphalt" else 423
    center = target_x
    direction = -1 if center > 208 else 1
    gates = []
    for index in reversed(range(gate_count)):
        shift = offset + _round(random() * 16)
        if not 54 <= center + direction * shift <= 324:
            direction *= -1
        center += direction * shift
        direction *= -1
        y = 164 + _round(index * (target_y - 234) / (gate_count - 1))
        gates.append((center - gap / 2, center + gap / 2, y))
    return (_clamp(center + direction * 73, 70, 305), 85, strength, wind,
            random() * 5 if seed else 0, gates, (target_x, target_y, radius))


def _wind(time):
    phase = time % 32
    force = .12 + .04 * sin(time * 1.9)
    for peak, radius, strength in GUSTS:
        distance = abs(phase - peak)
        if distance < radius:
            force += strength * (.5 + .5 * cos(distance * pi / radius))
    return _clamp(force, 0, 1.1)


def replay(level, difficulty, seed, trace):
    """Return the verified length in centimetres, or None for an invalid run."""
    if not isinstance(level, str) or level not in SPEEDS:
        return None
    if not isinstance(difficulty, str) or difficulty not in DIFFICULTIES:
        return None
    if type(seed) is not int or not 0 <= seed <= 999999999 or not isinstance(trace, list):
        return None
    if level == "record" and (difficulty != "normal" or seed != 0):
        return None
    if not 1 <= len(trace) <= MAX_TICKS:
        return None

    ticks = 0
    for command in trace:
        if not isinstance(command, list) or len(command) != 3 or any(type(item) is not int for item in command):
            return None
        pour, direction, count = command
        if pour not in (0, 1) or not -100 <= direction <= 100 or count < 1:
            return None
        ticks += count
        if ticks > MAX_TICKS:
            return None
    if not trace[0][0]:
        return None

    root_x, root_y, strength, wind, wind_offset, gates, target = _course(level, difficulty, seed)
    x, y, length, cracks, elapsed = root_x, root_y + 14, 14, 0, 0
    speed = SPEEDS[level] * STEP
    remaining = ticks
    for pour, direction, count in trace:
        steer = 0 if level == "record" else direction / 100
        for _ in range(count):
            remaining -= 1
            elapsed += STEP
            force = _wind(elapsed + wind_offset) * wind
            if pour and y < ROAD:
                new_x = _clamp(x + speed * steer * 1.18, 14, 386)
                new_y = min(ROAD, y + speed * (1 - .31 * abs(steer)))
                length += hypot(new_x - x, new_y - y)
                x, y = new_x, new_y

            load = (length * (.132 + force * .225) + abs(x - root_x) * .08) / strength
            cracks = max(0, cracks + STEP * ((load - 79) * .32 if load >= 79 else -.17))
            if load >= 113 or cracks >= 16:
                return None
            if any(hypot(max(0, x - left), y - height) <= 9 or
                   hypot(max(0, right - x), y - height) <= 9
                   for left, right, height in gates):
                return None

            if target:
                tx, ty, radius = target
                if level == "asphalt":
                    if y == ROAD:
                        return _round(length * .6) if abs(x - tx) <= radius and not remaining else None
                elif hypot(x - tx, y - ty) <= radius:
                    return _round(length * .6) if not remaining else None
                elif y > ty + radius:
                    return None
            if elapsed >= MAX_TICKS * STEP:
                return None

    if level == "record" and length >= 30:
        return _round(length * .6)
    return None
