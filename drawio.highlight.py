import html
import re
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path


WINDOW_CODE_FULL_RE = re.compile(
    r"^(CM-B\d+[A-Za-z]?|CMB\d+[A-Za-z]?|[A-Z]{1,4}\d*(?:[-.][A-Z0-9]+)+[A-Za-z]?)$"
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

ROW_TOLERANCE = 2.5
CODE_PART_GAP = 4.5
MAX_CODE_PARTS = 4
FIXED_WIDTH = 24.0
FIXED_HEIGHT = 24.0


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


def cluster_rows(cells: list[TextCell], tolerance: float = ROW_TOLERANCE) -> list[list[TextCell]]:
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


def merge_cells(cells: list[TextCell], value: str | None = None) -> TextCell:
    x = min(cell.x for cell in cells)
    y = min(cell.y for cell in cells)
    right = max(cell.right for cell in cells)
    bottom = max(cell.bottom for cell in cells)
    return TextCell(
        cell_id=",".join(cell.cell_id for cell in cells),
        value=value if value is not None else "".join(cell.value for cell in cells),
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
    if "-" in code:
        return code.split("-", 1)[0]
    if "." in code:
        return code.split(".", 1)[0]
    return None


def are_close_enough(parts: list[TextCell], max_gap: float = CODE_PART_GAP) -> bool:
    if len(parts) <= 1:
        return True
    for i in range(len(parts) - 1):
        gap = parts[i + 1].x - parts[i].right
        if gap > max_gap:
            return False
    return True


def build_code_candidates_from_raw_rows(cells: list[TextCell]) -> list[TextCell]:
    code_candidates = []
    rows = cluster_rows(cells)

    for row in rows:
        idx = 0
        while idx < len(row):
            best_parts = None
            best_code = None

            for span in range(1, min(MAX_CODE_PARTS, len(row) - idx) + 1):
                parts = row[idx: idx + span]
                if not are_close_enough(parts):
                    break

                combined = "".join(part.value for part in parts)
                if WINDOW_CODE_FULL_RE.fullmatch(combined):
                    best_parts = parts
                    best_code = combined

            if best_parts is not None:
                code_candidates.append(merge_cells(best_parts, best_code))
                idx += len(best_parts)
            else:
                idx += 1

    return code_candidates


def box_from_cell(cell: TextCell) -> tuple[float, float, float, float]:
    return (cell.x, cell.y, cell.right, cell.bottom)


def build_symbols(cells: list[TextCell]) -> list[Symbol]:
    code_candidates = [
        cell for cell in build_code_candidates_from_raw_rows(cells)
        if extract_prefix(cell.value)
    ]

    class_candidates = [
        cell for cell in cells
        if CLASS_RE.fullmatch(cell.value) and cell.value not in EXCLUDED_CLASSES
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

            if -4 <= vertical_gap <= 25 and center_gap <= max(20, code_cell.width * 1.5):
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

        min_y = min(code_box[1], class_box[1])
        max_y = max(code_box[3], class_box[3])

        cx = class_cell.center_x
        cy = (min_y + max_y) / 2.0

        x = cx - FIXED_WIDTH / 2.0
        y = cy - FIXED_HEIGHT / 2.0
        right = x + FIXED_WIDTH
        bottom = y + FIXED_HEIGHT

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
        if "#fff2cc" in cell.attrib.get("style", ""):
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
        abs(ax1 - bx1) <= tolerance
        and abs(ay1 - by1) <= tolerance
        and abs(ax2 - bx2) <= tolerance
        and abs(ay2 - by2) <= tolerance
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

    root_node = next(root.iter("root"))
    for obj, _, _ in auto_highlights:
        try:
            root_node.remove(obj)
        except ValueError:
            pass

    if manual_highlights:
        style = manual_highlights[0][1].attrib.get("style", DEFAULT_STYLE)
    elif auto_highlights:
        style = auto_highlights[0][1].attrib.get("style", DEFAULT_STYLE)
    else:
        style = DEFAULT_STYLE

    existing_boxes = []
    for _, _, geom in manual_highlights:
        x = float(geom.attrib.get("x", 0))
        y = float(geom.attrib.get("y", 0))
        w = float(geom.attrib.get("width", 0))
        h = float(geom.attrib.get("height", 0))
        existing_boxes.append((x, y, x + w, y + h))

    inserted = 0
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
