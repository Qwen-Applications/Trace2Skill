---
description: Safe coding patterns for openpyxl operations
---

# OpenPyXL Safe Patterns

Read this when working with Excel features that may or may not exist (comments, merged cells, formulas, etc.)

## Checking for Comments Safely

```python
# ✅ Correct way to check for comments
if cell.comment is not None:
    # process comment
    pass

# ❌ Never do this - raises AttributeError
if 'comment' in cell.__dict__:
    pass
```

## Handling Optional Output Files

```python
import os
from openpyxl import Workbook, load_workbook

# ✅ Check before loading
output_path = 'output.xlsx'
if os.path.exists(output_path):
    wb = load_workbook(output_path)
else:
    wb = Workbook()
```

## Working with Sheet Names

```python
# ✅ Check sheet exists before accessing
if 'Output' in wb.sheetnames:
    ws = wb['Output']
else:
    ws = wb.active

# ❌ Don't assume sheet name matches expected
ws = wb['expected_name']  # may raise KeyError
```

## Safe Cell Access

### DO: Iterate Through Rows
```python
for row in ws.iter_rows(min_row=6, max_row=11):
    for cell in row:
        print(f"{cell.column_letter}{cell.row}: {cell.value}")
```

### DON'T: Use Non-Existent Methods
```python
# WRONG — ws.range() does not exist in openpyxl
values = ws.range("M6:S11")  # AttributeError!

# CORRECT — use iter_rows or cell()
values = [[cell.value for cell in row] for row in ws.iter_rows(min_row=6, max_row=11)]
```

## Handling Merged Cells

OpenPyXL represents merged regions as special `MergedCell` objects that lack standard attributes.

### Pattern: Check Before Access
```python
import openpyxl.cell

for row in ws.iter_rows():
    for cell in row:
        if isinstance(cell, openpyxl.cell.MergedCell):
            continue  # Skip merged cells or handle specially
```

## Exploration Checklist

Before writing transformation logic:

1. Print sheet names: `print(ws.sheetnames)`
2. Scan first few rows of target area to confirm column positions
3. Check adjacent columns for shifted data or merged headers
4. Verify non-empty values match expected brand/data names
5. Log actual column letters found vs. task description assumptions

## Error Prevention Checklist

Before running your script:
- [ ] Verify input file exists
- [ ] Check output file creation strategy (new vs modify existing)
- [ ] Use public API only (no `__dict__`, `__private__` access)
- [ ] Handle missing sheets gracefully
- [ ] Test with sample data if possible