import html
import re
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

# --- 설정 및 규칙 ---
CODE_RULES = {
    "PO": r"^PO\.\d+[A-Za-z]?$",
    "CM-B": r"^CM-B\d+[A-Za-z]?$",
    "CMB": r"^CMB\d+[A-Za-z]?$",
    "CM": r"^CM-?\d+[A-Za-z]?$",
    "OT": r"^OT-\d+[A-Za-z]?$",
    "OF": r"^OF\d*-\d+[A-Za-z]?$",
    "HO": r"^HO-\d+[A-Za-z]?$",
}

INLINE_CODE_RULES = {
    "AWPO": r"^AWPO\.\d+[A-Za-z]?$",
    "ACWPO": r"^ACWPO\.\d+[A-Za-z]?$",
    "FADW": r"^\d+FADW\.?\d+[A-Za-z]?$",
    "FASD": r"^\d+FASD\.?\d+[A-Za-z]?$",
    # 1F ADW.008, 1F RV.002 대응용 (normalize_text가 공백을 제거하므로 1FADW... 형태가 됨)
    "F_ADW": r"^\d+FADW\.?\d+$",
    "F_RV": r"^\d+FRV\.?\d+$",
}

RANGE_CODE_RULES = {
    "FADW_RANGE": r"^\d+FADW\.?\d+[A-Za-z]?\~\d+[A-Za-z]?$",
    "FASD_RANGE": r"^\d+FASD\.?\d+[A-Za-z]?\~\d+[A-Za-z]?$",
}

COMPILED_CODE_RULES = {p: re.compile(r) for p, r in CODE_RULES.items()}
COMPILED_INLINE_RULES = {k: re.compile(v) for k, v in INLINE_CODE_RULES.items()}
COMPILED_RANGE_RULES = {k: re.compile(v) for k, v in RANGE_CODE_RULES.items()}

# 사진 기반 유효 클래스 목록 확장
VALID_CLASS_CODES = {"ACW", "AW", "AG", "CW", "FG", "SG", "SDW", "SD"}

YELLOW_STYLE = "rounded=1;fillColor=#fff2cc;arcSize=4;absoluteArcSize=1;verticalAlign=middle;align=center;strokeColor=#d6b656;strokeWidth=1.1811;opacity=50;noLabel=1"
GREEN_STYLE = "rounded=1;fillColor=#d5e8d4;arcSize=4;absoluteArcSize=1;verticalAlign=middle;align=center;strokeColor=#82b366;strokeWidth=1.1811;opacity=50;noLabel=1"

# --- [중요] 원인 분석 결과에 따른 임계값 수정 ---
ROW_TOLERANCE = 5.0           # 행 인식 높이 (더 넉넉하게)
CODE_PART_GAP = 8.5           # 조각 간 간격 허용 (파편화 대응 강화)
MAX_CODE_PARTS = 8            # 더 많은 조각 결합 허용

FIXED_WIDTH, FIXED_HEIGHT = 24.0, 24.0
INLINE_WIDTH, INLINE_HEIGHT = 36.0, 22.0  # 초록색 박스 살짝 확대

CLASS_X_TOLERANCE = 15.0      # 상하 정렬 오차 허용 확대
CLASS_MAX_VERTICAL_GAP = 28.0 # 위아래 간격 최대 허용치 확대
CLASS_MIN_VERTICAL_GAP = -5.0 # 미세 중첩 허용

@dataclass
class TextCell:
    cell_id: str; value: str; x: float; y: float; width: float; height: float
    @property
    def right(self): return self.x + self.width
    @property
    def bottom(self): return self.y + self.height
    @property
    def center_x(self): return self.x + self.width / 2
    @property
    def center_y(self): return self.y + self.height / 2

@dataclass
class Symbol:
    code: str; class_code: str | None; key: str; kind: str
    x: float; y: float; width: float; height: float; source_cell_ids: list[str]

def normalize_text(raw: str) -> str:
    text = html.unescape(raw or "")
    text = re.sub(r"<[^>]+>", "", text).replace("&nbsp;", "")
    return re.sub(r"\s+", "", text).strip()

def parse_text_cells(root: ET.Element, text_parent: str) -> list[TextCell]:
    cells = []
    for cell in root.iter("mxCell"):
        if cell.attrib.get("parent") != text_parent: continue
        val = normalize_text(cell.attrib.get("value", ""))
        if not val: continue
        geom = cell.find("mxGeometry")
        if geom is None: continue
        cells.append(TextCell(cell.attrib.get("id", ""), val, float(geom.attrib.get("x", 0)), float(geom.attrib.get("y", 0)), float(geom.attrib.get("width", 0)), float(geom.attrib.get("height", 0))))
    return cells

def cluster_rows(cells: list[TextCell]) -> list[list[TextCell]]:
    rows = []
    for cell in sorted(cells, key=lambda c: (c.y, c.x)):
        for row in rows:
            if abs(cell.y - (sum(c.y for c in row)/len(row))) <= ROW_TOLERANCE:
                row.append(cell); break
        else: rows.append([cell])
    for r in rows: r.sort(key=lambda c: c.x)
    return rows

def get_merged_candidates(row: list[TextCell], rules: dict):
    candidates = []
    idx = 0
    while idx < len(row):
        best = None
        for span in range(1, min(MAX_CODE_PARTS, len(row) - idx) + 1):
            parts = row[idx : idx + span]
            gap_ok = True
            for i in range(len(parts)-1):
                if parts[i+1].x - parts[i].right > CODE_PART_GAP: gap_ok = False; break
            if not gap_ok: continue

            combined = "".join(p.value for p in parts)
            prefix = next((k for k, v in rules.items() if v.fullmatch(combined)), None)
            if prefix: best = (parts, combined, prefix)
        
        if best:
            candidates.append(best)
            idx += len(best[0])
        else: idx += 1
    return candidates

def build_symbols(cells: list[TextCell]) -> list[Symbol]:
    all_rows = cluster_rows(cells)
    green_symbols = []
    green_cell_ids = set()
    
    for row in all_rows:
        res = get_merged_candidates(row, {**COMPILED_INLINE_RULES, **COMPILED_RANGE_RULES})
        for parts, val, pref in res:
            kind = "range" if "~" in val else "inline"
            min_x, max_r = min(p.x for p in parts), max(p.right for p in parts)
            cy = sum(p.center_y for p in parts)/len(parts)
            w = max(44.0, min(65.0, (max_r - min_x) + 10.0)) if kind == "range" else INLINE_WIDTH
            green_symbols.append(Symbol(val, None, pref, kind, (min_x+max_r)/2 - w/2, cy - INLINE_HEIGHT/2, w, INLINE_HEIGHT, [p.cell_id for p in parts]))
            for p in parts: green_cell_ids.add(p.cell_id)

    yellow_symbols = []
    remaining_cells = [c for c in cells if c.cell_id not in green_cell_ids]
    rem_rows = cluster_rows(remaining_cells)
    
    code_cands = []
    for row in rem_rows:
        res = get_merged_candidates(row, COMPILED_CODE_RULES)
        for parts, val, pref in res:
            min_x, min_y, max_r, max_b = min(p.x for p in parts), min(p.y for p in parts), max(p.right for p in parts), max(p.bottom for p in parts)
            code_cands.append((TextCell(",".join(p.cell_id for p in parts), val, min_x, min_y, max_r-min_x, max_b-min_y), pref))

    used_class_ids = set()
    class_cells = [c for c in remaining_cells if c.value in VALID_CLASS_CODES]

    for code, pref in code_cands:
        matches = []
        for cl in class_cells:
            if cl.cell_id in used_class_ids: continue
            v_gap = cl.y - code.bottom
            c_gap = abs(cl.center_x - code.center_x)
            if (CLASS_MIN_VERTICAL_GAP <= v_gap <= CLASS_MAX_VERTICAL_GAP) and (c_gap <= CLASS_X_TOLERANCE):
                matches.append(cl)
        
        if matches:
            target = min(matches, key=lambda m: abs(m.center_x - code.center_x))
            used_class_ids.add(target.cell_id)
            yellow_symbols.append(Symbol(code.value, target.value, f"{pref}|{target.value}", "stacked", (code.center_x + target.center_x)/2 - FIXED_WIDTH/2, (code.y + target.bottom)/2 - FIXED_HEIGHT/2, FIXED_WIDTH, FIXED_HEIGHT, code.cell_id.split(",") + [target.cell_id]))

    return green_symbols + yellow_symbols

def apply_highlights(tree: ET.ElementTree):
    root = tree.getroot()
    text_layer_id = find_layer_id(root, "Layer_Text")
    tmpl_layer_id = find_layer_id(root, "Layer_Tmpl")
    cells = parse_text_cells(root, text_layer_id)
    
    root_node = next(root.iter("root"))
    for obj in [o for o in root.iter("object") if "AutoHighlight:" in o.attrib.get("tags", "")]:
        try: root_node.remove(obj)
        except: pass

    all_symbols = build_symbols(cells)
    for sym in all_symbols:
        root_node.append(make_highlight_object(tmpl_layer_id, sym))
    return len(all_symbols)

def find_layer_id(root, name):
    for c in root.iter("mxCell"):
        if c.attrib.get("value") == name: return c.attrib["id"]
    raise ValueError(f"{name} not found")

def make_highlight_object(layer_id, sym):
    obj = ET.Element("object", {"label": "", "tags": f"AutoHighlight:{sym.key}", "id": f"auto-hl-{uuid.uuid4().hex[:10]}"})
    style = GREEN_STYLE if sym.kind != 'stacked' else YELLOW_STYLE
    cell = ET.SubElement(obj, "mxCell", {"style": style, "vertex": "1", "parent": layer_id})
    ET.SubElement(cell, "mxGeometry", {"x": f"{sym.x:.2f}", "y": f"{sym.y:.2f}", "width": f"{sym.width:.2f}", "height": f"{sym.height:.2f}", "as": "geometry"})
    return obj

def main():
    for source in [f for f in Path.cwd().glob("*.drawio") if not f.name.endswith("_auto-highlight.drawio")]:
        try:
            tree = ET.parse(source)
            count = apply_highlights(tree)
            tree.write(Path.cwd() / f"{source.stem}_auto-highlight{source.suffix}", encoding="utf-8", xml_declaration=False)
            print(f"Processed {source.name}: {count} highlights added.")
        except Exception as e: print(f"Error {source.name}: {e}")

if __name__ == "__main__":
    main()