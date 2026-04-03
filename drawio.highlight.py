import html
import itertools
import re
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path


WINDOW_CODE_RE = re.compile(
    r"(CM-B\d+[A-Za-z]?|CMB\d+[A-Za-z]?|[A-Z]{1,4}\d*(?:-[A-Z0-9]+)+[A-Za-z]?)"
)
CLASS_RE = re.compile(r"^[A-Z]{2,3}$")
EXCLUDED_CLASSES = {
    "KEY", "MAP", "PIT", "EPS", "PS", "UP", "DN", "AD", "AV", "BY"
}

DEFAULT_STYLE = (
    "rounded=1;fillColor=#fff2cc;arcSize=4;absoluteArcSize=1;"
    "verticalAlign=middle;align=center;strokeColor=#d6b656;"
    "strokeWidth=1.1811;opacity=50;noLabel=1"
)


@dataclass
class TextCell:
    cell_id: str
    value: str
    x: float
    y: float
    width: float
    height: float

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def bottom(self) -> float:
        return self.y + self.height

    @property
    def center_x(self) -> float:
        return self.x + self.width / 2

    @property
    def center_y(self) -> float:
        return self.y + self.height / 2


@dataclass
class Symbol:
    code: str
    class_code: str
    key: str
    code_box: tuple[float, float, float, float]
    class_box: tuple[float, float, float, float]
    x: float
    y: float
    width: float
    height: float

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def bottom(self) -> float:
        return self.y + self.height

    @property
    def center_x(self) -> float:
        return self.x + self.width / 2

    @property
    def center_y(self) -> float:
        return self.y + self.height / 2


def normalize_text(raw: str) -> str:
    text = html.unescape(raw or "")
    text = text.replace("<br>", "").replace("<br/>", "").replace("<br />", "")
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("&nbsp;", "")
    text = re.sub(r"\s+", "", text)
    return text.strip()


def parse_text_cells(root: ET.Element, text_parent: str) -> list[TextCell]:
    cells = []
    for cell in root.iter("mxCell"):
        if cell.attrib.get("parent") != text_parent:
            continue
        value = normalize_text(cell.attrib.get("value", ""))
        if not value:
            continue
        geom = cell.find("mxGeometry")
        if geom is None:
            continue
        cells.append(
            TextCell(
                cell_id=cell.attrib.get("id", ""),
                value=value,
                x=float(geom.attrib.get("x", 0)),
                y=float(geom.attrib.get("y", 0)),
                width=float(geom.attrib.get("width", 0)),
                height=float(geom.attrib.get("height", 0)),
            )
        )
    return cells


def cluster_rows(cells: list[TextCell], tolerance: float = 2.5) -> list[list[TextCell]]:
    rows = []
    for cell in sorted(cells, key=lambda item: (item.y, item.x)):
        for row in rows:
            avg_y = sum(item.y for item in row) / len(row)
            if abs(cell.y - avg_y) <= tolerance:
                row.append(cell)
                break
        else:
            rows.append([cell])
    for row in rows:
        row.sort(key=lambda item: item.x)
    return rows


def merge_row_segments(row: list[TextCell], max_gap: float = 8.0) -> list[TextCell]:
    merged = []
    current = []
    for cell in row:
        if not current:
            current = [cell]
            continue
        gap = cell.x - current[-1].right
        if gap <= max_gap:
            current.append(cell)
            continue
        merged.append(_merge_cells(current))
        current = [cell]
    if current:
        merged.append(_merge_cells(current))
    return merged


def _merge_cells(cells: list[TextCell]) -> TextCell:
    value = "".join(cell.value for cell in cells)
    x = min(cell.x for cell in cells)
    y = min(cell.y for cell in cells)
    right = max(cell.right for cell in cells)
    bottom = max(cell.bottom for cell in cells)
    return TextCell(
        cell_id=",".join(cell.cell_id for cell in cells),
        value=value,
        x=x,
        y=y,
        width=right - x,
        height=bottom - y,
    )


def extract_prefix(code: str) -> str | None:
    if code.startswith("CM-B"):
        return "CM-B"
    if code.startswith("CMB"):
        return "CMB"
    if "-" not in code:
        return None
    return code.split("-", 1)[0]


def estimate_code_box(merged_cell: TextCell, raw_cells: list[TextCell], code: str) -> TextCell:
    if merged_cell.value == code:
        return TextCell(
            cell_id=merged_cell.cell_id,
            value=code,
            x=merged_cell.x,
            y=merged_cell.y,
            width=merged_cell.width,
            height=merged_cell.height,
        )

    parts = []
    for raw in raw_cells:
        if abs(raw.y - merged_cell.y) > 2.5:
            continue
        if raw.x < merged_cell.x - 1 or raw.right > merged_cell.right + 1:
            continue
        if raw.value and raw.value in code:
            parts.append(raw)

    if not parts:
        return TextCell(
            cell_id=merged_cell.cell_id,
            value=code,
            x=merged_cell.x,
            y=merged_cell.y,
            width=merged_cell.width,
            height=merged_cell.height,
        )

    x = min(p.x for p in parts)
    y = min(p.y for p in parts)
    right = max(p.right for p in parts)
    bottom = max(p.bottom for p in parts)
    return TextCell(
        cell_id=",".join(p.cell_id for p in parts),
        value=code,
        x=x,
        y=y,
        width=right - x,
        height=bottom - y,
    )


def split_merged_codes(merged_cell: TextCell, raw_cells: list[TextCell]) -> list[TextCell]:
    codes = WINDOW_CODE_RE.findall(merged_cell.value)
    if not codes:
        return []
    if len(codes) == 1:
        return [estimate_code_box(merged_cell, raw_cells, codes[0])]

    row_parts = []
    for raw in raw_cells:
        if abs(raw.y - merged_cell.y) > 2.5:
            continue
        if raw.x < merged_cell.x - 1 or raw.right > merged_cell.right + 1:
            continue
        row_parts.append(raw)

    row_parts.sort(key=lambda c: c.x)
    results = []
    start_idx = 0

    for code in codes:
        matched_parts = []
        assembled = ""
        idx = start_idx

        while idx < len(row_parts):
            part = row_parts[idx]
            candidate = assembled + part.value
            if code.startswith(candidate):
                matched_parts.append(part)
                assembled = candidate
                idx += 1
                if assembled == code:
                    break
            else:
                if not matched_parts:
                    idx += 1
                    start_idx = idx
                    continue
                break

        if matched_parts and assembled == code:
            x = min(p.x for p in matched_parts)
            y = min(p.y for p in matched_parts)
            right = max(p.right for p in matched_parts)
            bottom = max(p.bottom for p in matched_parts)
            results.append(
                TextCell(
                    cell_id=",".join(p.cell_id for p in matched_parts),
                    value=code,
                    x=x,
                    y=y,
                    width=right - x,
                    height=bottom - y,
                )
            )
            start_idx = idx
        else:
            results.append(estimate_code_box(merged_cell, raw_cells, code))

    return results


def box_from_cell(cell: TextCell) -> tuple[float, float, float, float]:
    return (cell.x, cell.y, cell.right, cell.bottom)


def build_symbols(cells: list[TextCell]) -> list[Symbol]:
    merged_rows = [merge_row_segments(row) for row in cluster_rows(cells)]
    merged_cells = list(itertools.chain.from_iterable(merged_rows))

    code_candidates = []
    for cell in merged_cells:
        for code_cell in split_merged_codes(cell, cells):
            if extract_prefix(code_cell.value):
                code_candidates.append(code_cell)

    class_candidates = [
        cell for cell in cells
        if CLASS_RE.match(cell.value) and cell.value not in EXCLUDED_CLASSES
    ]

    symbols = []
    used_classes = set()

    for code_cell in sorted(code_candidates, key=lambda item: (item.y, item.x)):
        matches = []
        for class_cell in class_candidates:
            if class_cell.cell_id in used_classes:
                continue
            vertical_gap = class_cell.y - code_cell.bottom
            center_gap = abs(class_cell.center_x - code_cell.center_x)
            if -2 <= vertical_gap <= 18 and center_gap <= max(14, code_cell.width * 1.1):
                matches.append((abs(vertical_gap), center_gap, class_cell))

        if not matches:
            continue

        _, _, class_cell = min(matches, key=lambda item: (item[0], item[1]))
        used_classes.add(class_cell.cell_id)

        prefix = extract_prefix(code_cell.value)
        if not prefix:
            continue

        code_box = box_from_cell(code_cell)
        class_box = box_from_cell(class_cell)

        # 코드 + 클래스의 중심만 구하고, 박스 크기는 고정값 사용
        min_x = min(code_box[0], class_box[0])
        min_y = min(code_box[1], class_box[1])
        max_x = max(code_box[2], class_box[2])
        max_y = max(code_box[3], class_box[3])

        cx = (min_x + max_x) / 2.0
        cy = (min_y + max_y) / 2.0

        # 고정 박스 크기
        fixed_width = 23.0
        fixed_height = 23.0

        # 중심에서 왼쪽으로 1픽셀 이동
        x = cx - fixed_width / 2.0 - 1.0
        y = cy - fixed_height / 2.0
        right = x + fixed_width
        bottom = y + fixed_height

        symbols.append(
            Symbol(

                code=code_cell.value,
                class_code=class_cell.value,
                key=f"{prefix}|{class_cell.value}",
                code_box=code_box,
                class_box=class_box,
                x=x,
                y=y,
                width=right - x,
                height=bottom - y,
            )
        )

    return dedupe_symbols(symbols)


def dedupe_symbols(symbols: list[Symbol]) -> list[Symbol]:
    result = []
    for symbol in symbols:
        duplicated = False
        for existing in result:
            if (
                symbol.code == existing.code
                and symbol.class_code == existing.class_code
                and abs(symbol.center_x - existing.center_x) < 4
                and abs(symbol.center_y - existing.center_y) < 4
            ):
                duplicated = True
                break
        if not duplicated:
            result.append(symbol)
    return result


def overlaps(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> bool:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    return ax1 < bx2 and ax2 > bx1 and ay1 < by2 and ay2 > by1


def shrink_pair(a: Symbol, b: Symbol) -> tuple[Symbol, Symbol]:
    ax1, ay1, ax2, ay2 = a.x, a.y, a.right, a.bottom
    bx1, by1, bx2, by2 = b.x, b.y, b.right, b.bottom

    if not overlaps((ax1, ay1, ax2, ay2), (bx1, by1, bx2, by2)):
        return a, b

    # 가로로 더 많이 나란한 경우: 가운데에서 분할
    horizontal_relation = abs(a.center_x - b.center_x) >= abs(a.center_y - b.center_y)

    if horizontal_relation:
        split_x = (a.center_x + b.center_x) / 2.0
        if a.center_x <= b.center_x:
            ax2 = min(ax2, split_x - 1)
            bx1 = max(bx1, split_x + 1)
        else:
            bx2 = min(bx2, split_x - 1)
            ax1 = max(ax1, split_x + 1)
    else:
        split_y = (a.center_y + b.center_y) / 2.0
        if a.center_y <= b.center_y:
            ay2 = min(ay2, split_y - 1)
            by1 = max(by1, split_y + 1)
        else:
            by2 = min(by2, split_y - 1)
            ay1 = max(ay1, split_y + 1)

    # 코드/클래스 최소 외곽보다 작아지지 않게 보정
    min_ax1 = min(a.code_box[0], a.class_box[0]) - 2.0
    min_ay1 = min(a.code_box[1], a.class_box[1]) - 3.0
    min_ax2 = max(a.code_box[2], a.class_box[2]) + 2.0
    min_ay2 = max(a.code_box[3], a.class_box[3]) + 3.0

    min_bx1 = min(b.code_box[0], b.class_box[0]) - 2.0
    min_by1 = min(b.code_box[1], b.class_box[1]) - 3.0
    min_bx2 = max(b.code_box[2], b.class_box[2]) + 2.0
    min_by2 = max(b.code_box[3], b.class_box[3]) + 3.0

    ax1 = min(ax1, min_ax1) if ax1 > min_ax1 else ax1
    ay1 = min(ay1, min_ay1) if ay1 > min_ay1 else ay1
    ax2 = max(ax2, min_ax2) if ax2 < min_ax2 else ax2
    ay2 = max(ay2, min_ay2) if ay2 < min_ay2 else ay2

    bx1 = min(bx1, min_bx1) if bx1 > min_bx1 else bx1
    by1 = min(by1, min_by1) if by1 > min_by1 else by1
    bx2 = max(bx2, min_bx2) if bx2 < min_bx2 else bx2
    by2 = max(by2, min_by2) if by2 < min_by2 else by2

    a2 = Symbol(
        code=a.code,
        class_code=a.class_code,
        key=a.key,
        code_box=a.code_box,
        class_box=a.class_box,
        x=ax1,
        y=ay1,
        width=max(1.0, ax2 - ax1),
        height=max(1.0, ay2 - ay1),
    )
    b2 = Symbol(
        code=b.code,
        class_code=b.class_code,
        key=b.key,
        code_box=b.code_box,
        class_box=b.class_box,
        x=bx1,
        y=by1,
        width=max(1.0, bx2 - bx1),
        height=max(1.0, by2 - by1),
    )
    return a2, b2


def resolve_symbol_overlaps(symbols: list[Symbol]) -> list[Symbol]:
    updated = symbols[:]
    for _ in range(4):
        changed = False
        for i in range(len(updated)):
            for j in range(i + 1, len(updated)):
                a = updated[i]
                b = updated[j]
                before = (a.x, a.y, a.right, a.bottom, b.x, b.y, b.right, b.bottom)
                a2, b2 = shrink_pair(a, b)
                after = (a2.x, a2.y, a2.right, a2.bottom, b2.x, b2.y, b2.right, b2.bottom)
                if before != after:
                    updated[i] = a2
                    updated[j] = b2
                    changed = True
        if not changed:
            break
    return updated


def find_layer_id(root: ET.Element, layer_name: str) -> str:
    for cell in root.iter("mxCell"):
        if cell.attrib.get("value") == layer_name:
            return cell.attrib["id"]
    raise ValueError(f"{layer_name} not found.")


def find_existing_highlights(root: ET.Element, layer_id: str):
    highlights = []
    for obj in root.iter("object"):
        cell = obj.find("mxCell")
        geom = cell.find("mxGeometry") if cell is not None else None
        if cell is None or geom is None:
            continue
        if cell.attrib.get("parent") != layer_id:
            continue
        if "#fff2cc" not in cell.attrib.get("style", ""):
            continue
        highlights.append((obj, cell, geom))
    return highlights


def split_manual_and_auto_highlights(highlights):
    manual = []
    auto = []
    for item in highlights:
        obj, _, _ = item
        if "AutoHighlight:" in obj.attrib.get("tags", ""):
            auto.append(item)
        else:
            manual.append(item)
    return manual, auto


def boxes_almost_same(a, b, tolerance: float = 3.0) -> bool:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    return (
        abs(ax1 - bx1) <= tolerance and
        abs(ay1 - by1) <= tolerance and
        abs(ax2 - bx2) <= tolerance and
        abs(ay2 - by2) <= tolerance
    )


def make_highlight_object(layer_id: str, symbol: Symbol, style: str) -> ET.Element:
    object_id = f"auto-hl-{uuid.uuid4().hex[:10]}"
    obj = ET.Element("object", {"label": "", "tags": f"AutoHighlight:{symbol.key}", "id": object_id})
    cell = ET.SubElement(obj, "mxCell", {"style": style, "vertex": "1", "parent": layer_id})
    ET.SubElement(
        cell,
        "mxGeometry",
        {
            "x": f"{symbol.x:.3f}".rstrip("0").rstrip("."),
            "y": f"{symbol.y:.3f}".rstrip("0").rstrip("."),
            "width": f"{symbol.width:.3f}".rstrip("0").rstrip("."),
            "height": f"{symbol.height:.3f}".rstrip("0").rstrip("."),
            "as": "geometry",
        },
    )
    return obj


def apply_highlights(tree: ET.ElementTree):
    root = tree.getroot()
    text_layer_id = find_layer_id(root, "Layer_Text")
    tmpl_layer_id = find_layer_id(root, "Layer_Tmpl")

    cells = parse_text_cells(root, text_layer_id)
    symbols = build_symbols(cells)
    if not symbols:
        raise ValueError("No symbol pairs were detected.")

    highlights = find_existing_highlights(root, tmpl_layer_id)
    manual_highlights, auto_highlights = split_manual_and_auto_highlights(highlights)

    if manual_highlights:
        style = manual_highlights[0][1].attrib.get("style", DEFAULT_STYLE)
    elif auto_highlights:
        style = auto_highlights[0][1].attrib.get("style", DEFAULT_STYLE)
    else:
        style = DEFAULT_STYLE

    existing_boxes = []
    for _, _, geom in manual_highlights + auto_highlights:
        x = float(geom.attrib.get("x", 0))
        y = float(geom.attrib.get("y", 0))
        w = float(geom.attrib.get("width", 0))
        h = float(geom.attrib.get("height", 0))
        existing_boxes.append((x, y, x + w, y + h))

    inserted = 0
    root_node = next(root.iter("root"))
    groups = {}

    for symbol in symbols:
        groups.setdefault(symbol.key, symbol)
        box = (symbol.x, symbol.y, symbol.right, symbol.bottom)
        if any(boxes_almost_same(box, existing) for existing in existing_boxes):
            continue
        root_node.append(make_highlight_object(tmpl_layer_id, symbol, style))
        existing_boxes.append(box)
        inserted += 1

    return inserted, symbols, groups


def main():
    sources = [f for f in Path.cwd().glob("*.drawio") if not f.name.endswith("_auto-highlight.drawio")]
    if not sources:
        print("No source .drawio files found in the current directory.")
        return

    for source in sources:
        output = Path.cwd() / f"{source.stem}_auto-highlight{source.suffix}"
        try:
            tree = ET.parse(source)
            inserted, symbols, groups = apply_highlights(tree)
            tree.write(output, encoding="utf-8", xml_declaration=False)

            print(f"\n--- Processing: {source.name} ---")
            print(f"Detected symbols: {len(symbols)}")
            print("Detected groups:")
            for key, symbol in groups.items():
                print(f"  {key} <- {symbol.code}/{symbol.class_code}")
            print(f"Inserted highlights: {inserted}")
            print(f"Wrote: {output}")
        except Exception as e:
            print(f"\n--- Skipping {source.name} ---")
            print(f"Reason: {e}")


if __name__ == "__main__":
    main()
