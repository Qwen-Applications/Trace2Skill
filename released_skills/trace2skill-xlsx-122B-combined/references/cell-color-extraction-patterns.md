---
name: cell-color-extraction-patterns
description: Patterns for extracting and filtering cells by fill color in Excel files
---

# Cell Color Extraction Patterns

Use these patterns when filtering or extracting cells based on background fill color.

## RGB Format Variations

When accessing fill colors via `cell.fill.start_color.rgb`, openpyxl returns values in different formats depending on file origin:

| File Origin | Format Example | Description |
|-------------|----------------|-------------|
| Excel-native | `"FF00FFFF00"` | Full ARGB (alpha + RGB) |
| Some tools | `"00FFFF00"` | Shortened hex (no alpha) |
| Others | `"FFFF00"` | Even shorter variant |

### Pattern Matching Code

```python
from openpyxl import load_workbook

def get_fill_rgb(cell):
    """Extract RGB value handling multiple formats."""
    rgb = cell.fill.start_color.rgb
    if rgb is None:
        return None
    # Remove 'FF' prefix if present (alpha channel)
    if rgb.startswith('FF'):
        rgb = rgb[2:]
    return rgb

def is_yellow_fill(cell):
    """Check if cell has yellow background (#FFFF00)."""
    rgb = get_fill_rgb(cell)
    if rgb is None:
        return False
    # Match both full and short forms
    return rgb == 'FFFF00' or rgb.endswith('FFFF00')
```

## Blank Filtering During Extraction

When collecting cells conditionally (e.g., by color), always filter out null/empty values:

```python
yellow_values = []
for row in sheet.iter_rows(min_row=1, max_col=5):
    for cell in row:
        if is_yellow_fill(cell):
            value = cell.value
            # Explicitly exclude blanks
            if value is not None and value != '' and str(value).strip() != '':
                yellow_values.append(value)
```

**Why**: Without explicit blank filtering, None/empty cells occupy positions in result arrays, creating gaps in transposed data.

## Complete Example: Extract Yellow Cells by Row

```python
from openpyxl import load_workbook

def extract_by_fill_color(filepath, target_rgb='FFFF00'):
    """Extract all non-blank cells with specified fill color."""
    wb = load_workbook(filepath)
    results = {}
    
    for sheet_name in wb.sheetnames:
        sheet = wb[sheet_name]
        extracted = []
        
        for row in sheet.iter_rows():
            row_values = []
            for cell in row:
                rgb = cell.fill.start_color.rgb
                if rgb:
                    # Normalize RGB format
                    test_rgb = rgb[2:] if rgb.startswith('FF') else rgb
                    if test_rgb == target_rgb:
                        val = cell.value
                        if val is not None and val != '' and str(val).strip() != '':
                            row_values.append(val)
            if row_values:
                extracted.append(row_values)
        
        if extracted:
            results[sheet_name] = extracted
    
    return results
```

## Using openpyxl Instead of VBA

For cross-platform compatibility (Linux/Mac), prefer openpyxl over VBA macros:

| Task | openpyxl Approach | Why Prefer |
|------|-------------------|------------|
| Read fill color | `cell.fill.start_color.rgb` | No macro security blocks |
| Apply pattern fill | `PatternFill(start_color='FFFF00', end_color='FFFF00', fill_type='solid')` | Works on all platforms |
| Set borders | `Border(side=Side(style='thin'), ...)` | Cross-platform compatible |
| Modify formulas | `cell.value = '=SUM(A1:A10)'` | No VBA required |

VBA macros require Windows with Excel installed and trigger macro security warnings. openpyxl handles all styling natively.

## Validation Checklist

- [ ] Handle both `"FF..."` and shortened RGB formats
- [ ] Filter `None`, empty strings, and whitespace-only values
- [ ] Test with files from different sources (Excel, Google Sheets, other tools)
- [ ] Use openpyxl for cross-platform compatibility
- [ ] Verify extracted values match expected count before writing output