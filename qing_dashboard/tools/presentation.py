"""Read-only presentation for native spreadsheet dashboards."""
import bisect
import json
import re
from copy import deepcopy


def _content(cell):
    return cell.get('content', '') if isinstance(cell, dict) else cell


def style_dashboard(snapshot):
    """Adapt the displayed sheet, without modifying data sources or formulas."""
    if any(snapshot.get('settings', {}).get(key) == 1 for key in ('qingDashboardPresentation', 'bxrDashboardPresentation')):
        return snapshot
    result = deepcopy(snapshot)
    result.setdefault('settings', {})['qingDashboardPresentation'] = 1
    if not result.get('sheets'):
        return result
    sheet = result['sheets'][0]
    styles = result.setdefault('styles', {})
    next_id = max((int(key) for key in styles), default=0) + 1
    replacements = {}
    for key, original in list(styles.items()):
        style = dict(original)
        large = original.get('fontSize', 10) >= 14
        style.update(fontSize=16 if large else max(10, original.get('fontSize', 10)),
                     textColor='#153f46' if large else '#334155')
        if original.get('textColor', '').lower() in ('#01666b', '#01636b', '#741b47'):
            style['textColor'] = '#153f46' if large else '#0f766e'
        if original.get('fillColor'):
            style['fillColor'] = '#f7fafc'
        elif large:
            style['fillColor'] = '#f4f7fa'
        new_id = next_id + len(replacements)
        replacements[str(key)] = new_id
        styles[str(new_id)] = style
    for cell in sheet.get('cells', {}).values():
        if isinstance(cell, dict) and 'style' in cell:
            cell['style'] = replacements.get(str(cell['style']), cell['style'])
    for zone, style in list(sheet.get('styles', {}).items()):
        sheet['styles'][zone] = replacements.get(str(style), style)
    rows = sheet.setdefault('rows', {})
    count = sheet.get('rowNumber', 0)
    if count <= 0 or sheet.get('colNumber', 0) <= 0:
        return snapshot
    old_heights = [rows.get(str(i), {}).get('size', 23) for i in range(count)]
    new_heights = list(old_heights)
    figures = sheet.get('figures', [])
    original_widths = [sheet.get('cols', {}).get(str(i), {}).get('size', 96) for i in range(sheet['colNumber'])]
    anchored = set()
    for figure in figures:
        if 'x' not in figure:
            anchored.add(figure['id'])
            figure['x'] = sum(original_widths[:figure.get('col', 0)]) + figure.get('offset', {}).get('x', 0)
            figure['y'] = sum(old_heights[:figure.get('row', 0)]) + figure.get('offset', {}).get('y', 0)
    cards = [f for f in figures if f.get('data', {}).get('type') == 'scorecard']
    one_card_row = bool(cards) and max(f['y'] for f in cards) - min(f['y'] for f in cards) < 15
    if one_card_row and max(f['y'] for f in cards) < 30:
        for i in range(min(6, count)):
            new_heights[i] = max(new_heights[i], 28)
    # Enlarge populated table rows while keeping chart-only areas compact.
    table_rows = set()
    for address, cell in sheet.get('cells', {}).items():
        value = _content(cell)
        if isinstance(value, str) and re.search(r'(?:ODOO\.LIST|PIVOT\.(?:VALUE|HEADER)|ODOO\.PIVOT)', value):
            match = re.fullmatch(r'[A-Z]+(\d+)', address)
            if match:
                index = int(match[1]) - 1
                if index < count:
                    table_rows.add(index)
                    new_heights[index] = max(new_heights[index], 28)
    for i, height in enumerate(new_heights):
        rows.setdefault(str(i), {})['size'] = height
    old_edges = [0]
    new_edges = [0]
    for old, new in zip(old_heights, new_heights):
        old_edges.append(old_edges[-1] + old)
        new_edges.append(new_edges[-1] + new)

    def map_y(y):
        index = min(count - 1, max(0, bisect.bisect_right(old_edges, y) - 1))
        return new_edges[index] + (y - old_edges[index]) * new_heights[index] / old_heights[index]

    cols = sheet.setdefault('cols', {})
    widths = [cols.get(str(i), {}).get('size', 96) for i in range(sheet['colNumber'])]
    used_columns = [re.match(r'([A-Z]+)', address)[1] for address in sheet.get('cells', {}) if re.fullmatch(r'[A-Z]+[0-9]+', address)]
    used_count = 0
    for name in used_columns:
        index = 0
        for char in name:
            index = index * 26 + ord(char) - 64
        used_count = max(used_count, index)
    total_width = max(sum(widths[:used_count]), max((f['x'] + f['width'] for f in figures), default=0))
    ratio = min(1, 1000 / max(total_width, 1))
    for i, width in enumerate(widths):
        cols.setdefault(str(i), {})['size'] = round(width * ratio)
    for figure in figures:
        old_y, old_height = figure['y'], figure['height']
        figure.update(x=round(figure['x'] * ratio), width=round(figure['width'] * ratio),
                      y=round(map_y(old_y)), height=round(map_y(old_y + old_height) - map_y(old_y)))
        data = figure.get('data', {})
        data['background'] = '#ffffff'
        if data.get('type') == 'scorecard':
            data.update(baselineColorUp='#0f766e', baselineColorDown='#b45309')
    # Align KPI rows to the same four-column rhythm as the approved Purchase UI.
    groups = {}
    for card in cards:
        groups.setdefault(round(card['y'] / 10) * 10, []).append(card)
    for group in groups.values():
        if len(group) <= 4:
            width = min(238, (1000 - (len(group) - 1) * 16) / len(group))
            for index, card in enumerate(sorted(group, key=lambda f: f['x'])):
                card.update(x=round(index * (width + 16)), width=round(width))
                if one_card_row:
                    card.update(y=16, height=128)
    # Complete each table row's surface, including cells with no result.
    header_rows = {row - 1 for row in table_rows if row > 0 and row - 1 not in table_rows}
    style_cache = {}
    modern_styles = sheet.setdefault('styles', {}) if 'styles' in sheet else None
    range_styles = list((modern_styles or {}).items())

    def column_name(index):
        name = ''
        while index >= 0:
            name = chr(65 + index % 26) + name
            index = index // 26 - 1
        return name

    def coordinate(address):
        match = re.fullmatch(r'([A-Z]+)(\d+)', address)
        if not match:
            return -1, -1
        column = 0
        for char in match[1]:
            column = column * 26 + ord(char) - 64
        return column - 1, int(match[2]) - 1

    for row in sorted(table_rows | header_rows):
        for column in range(sheet['colNumber']):
            if widths[column] <= 50:
                continue
            address = f'{column_name(column)}{row + 1}'
            cell = sheet['cells'].get(address, '' if modern_styles is not None else {})
            style_id = cell.get('style') if isinstance(cell, dict) else None
            for zone, candidate in range_styles:
                parts = zone.split(':')
                left, top = coordinate(parts[0])
                right, bottom = coordinate(parts[-1])
                if left <= column <= right and top <= row <= bottom:
                    style_id = candidate
            style = dict(styles.get(str(style_id), {}))
            style['fillColor'] = '#e8f1f2' if row in header_rows else ('#ffffff' if row % 2 else '#f7fafc')
            if row in header_rows:
                style.update(bold=True, textColor='#526572')
            key = json.dumps(style, sort_keys=True)
            if key not in style_cache:
                style_cache[key] = max((int(k) for k in styles), default=0) + 1
                styles[str(style_cache[key])] = style
            if modern_styles is not None:
                modern_styles[address] = style_cache[key]
            else:
                cell = dict(cell) if isinstance(cell, dict) else {'content': cell}
                cell['style'] = style_cache[key]
                sheet['cells'][address] = cell
    for border in result.get('borders', {}).values():
        for edge in border.values():
            if isinstance(edge, list) and len(edge) == 2:
                edge[1] = '#d9e5eb'
    # Scorecards inherit font color from their referenced KPI cells.
    # Update display styles only; calculation content and formats stay intact.
    for card in cards:
        reference = card.get('data', {}).get('keyValue', '')
        match = re.fullmatch(r"=?'?([^'!]+)'?!\$?([A-Z]+)\$?(\d+)", reference) if isinstance(reference, str) else None
        if not match:
            continue
        target = next((item for item in result['sheets'] if item['name'] == match[1]), None)
        if not target:
            continue
        address = match[2] + match[3]
        cell = target.get('cells', {}).get(address)
        if cell is None:
            continue
        column, row = coordinate(address)
        style_id = cell.get('style') if isinstance(cell, dict) else None
        for zone, candidate in target.get('styles', {}).items():
            parts = zone.split(':')
            left, top = coordinate(parts[0])
            right, bottom = coordinate(parts[-1])
            if left <= column <= right and top <= row <= bottom:
                style_id = candidate
        style = dict(styles.get(str(style_id), {}), textColor='#153f46')
        key = str(max((int(k) for k in styles), default=0) + 1)
        styles[key] = style
        if 'styles' in target:
            target['styles'][address] = int(key)
        elif isinstance(cell, dict):
            cell['style'] = int(key)
    for figure in figures:
        if figure['id'] in anchored:
            x, y = figure.pop('x'), figure.pop('y')
            edges = [0]
            for i in range(sheet['colNumber']):
                edges.append(edges[-1] + cols[str(i)]['size'])
            column = max(0, min(sheet['colNumber'] - 1, bisect.bisect_right(edges, x) - 1))
            row = max(0, min(count - 1, bisect.bisect_right(new_edges, y) - 1))
            figure.update(col=column, row=row, offset={'x': x - edges[column], 'y': y - new_edges[row]})
    sheet['backgroundColor'] = '#f4f7fa'
    return result
