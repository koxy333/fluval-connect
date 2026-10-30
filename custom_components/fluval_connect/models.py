"""Pure schedule validation and device-state conversion."""
from datetime import time

CHANNELS = ("Red", "Green", "Blue", "White")

def minutes(value):
    parsed = time.fromisoformat(value)
    if parsed.second or parsed.microsecond or parsed.tzinfo:
        raise ValueError("Use a local clock time in HH:MM format")
    return parsed.hour * 60 + parsed.minute


def clock(value):
    if type(value) is int and 0 <= value < 1440:
        return f"{value // 60:02}:{value % 60:02}"
    return None


def timeline(properties):
    count = properties.get(120, 0)
    times = properties.get(121, [])
    colours = properties.get(122, b"")
    if type(count) is not int or not 0 <= count <= 12:
        return []
    return [{"time": clock(times[n]), **dict(zip((c.lower() for c in CHANNELS), colours[n*4:n*4+4]))}
            for n in range(min(count, len(times), len(colours)//4))]


def pro_properties(points):
    if not isinstance(points, list) or not 2 <= len(points) <= 12:
        raise ValueError("Provide between 2 and 12 Pro points")
    times, colours = [], []
    for point in points:
        if not isinstance(point, dict) or set(point) != {"time", "red", "green", "blue", "white"}:
            raise ValueError("Each point needs time, red, green, blue and white")
        times.append(minutes(point["time"]))
        for name in CHANNELS:
            value = point[name.lower()]
            if type(value) is not int or not 0 <= value <= 100:
                raise ValueError("Channel levels must be whole percentages from 0 to 100")
            colours.append(value)
    if any(a >= b for a, b in zip(times, times[1:])):
        raise ValueError("Pro times must be strictly increasing")
    return {120: len(points), 121: times, 122: bytes(colours)}


def auto_properties(data):
    values = {114: [minutes(data["sunrise_start"]), minutes(data["sunrise_end"])],
              115: [minutes(data["sunset_start"]), minutes(data["sunset_end"])],
              116: minutes(data["night_off"]) if data.get("night_off") is not None else 65535}
    for key, name in ((117, "day"), (118, "night")):
        levels = data[name]
        if not isinstance(levels, dict) or set(levels) != {c.lower() for c in CHANNELS}:
            raise ValueError("Day and night need red, green, blue and white percentages")
        raw = [levels[c.lower()] for c in CHANNELS]
        if any(type(v) is not int or not 0 <= v <= 100 for v in raw):
            raise ValueError("Channel levels must be whole percentages from 0 to 100")
        values[key] = bytes(raw)
    if not values[114][0] < values[114][1] <= values[115][0] < values[115][1]:
        raise ValueError("Sunrise must finish before sunset starts")
    return values

