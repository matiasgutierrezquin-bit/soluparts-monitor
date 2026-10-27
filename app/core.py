import datetime as dt

UTC = dt.timezone.utc

def iso(value):
    return value.astimezone(UTC).isoformat()

def parse(value):
    result = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('La fecha requiere zona horaria')
    return result.astimezone(UTC)
