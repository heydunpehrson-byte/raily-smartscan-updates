"""Canonical filing paths shared by processing, review and previews."""
import re
from pathlib import Path


def filing_components(railroad=None, location=None):
    values = []
    for value, legacy, fallback in ((railroad, 'unknown railroad', 'Unassigned Railroad'),
                                    (location, 'unknown location', 'General')):
        value = (value or '').strip()
        if not value or value.casefold() == legacy:
            value = fallback
        if (value in {'.', '..'} or re.search(r'[<>:"/\\\\|?*\x00-\x1f]', value)
                or value.endswith('.') or value.split('.')[0].upper() in
                {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}):
            raise ValueError('Unsafe filing path component')
        values.append(value)
    return tuple(values)


def resolve_destination(brain_root, railroad=None, location=None):
    base = (Path(brain_root) / 'Documents' / 'Railroads').resolve()
    destination = base.joinpath(*filing_components(railroad, location)).resolve()
    if not destination.is_relative_to(base):
        raise ValueError('Unsafe filing destination')
    return destination
