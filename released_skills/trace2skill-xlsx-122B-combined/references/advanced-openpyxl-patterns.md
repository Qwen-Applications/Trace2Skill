---
name: advanced-openpyxl-patterns
description: Advanced openpyxl patterns for complex spreadsheet operations
---

# Advanced openpyxl Patterns

Use these patterns for complex spreadsheet manipulation tasks.

## Pattern 1: Non-Contiguous Column Access

When working with scattered columns (e.g., F, L, P), use `cell(row, column)` with explicit column numbers instead of dataframe column names:

```python
from openpyxl import load_workbook

wb = load_workbook('file.xlsx')
ws = wb.active

# Direct access to non-contiguous columns
for row in range(2, ws.max_row + 1):
    col_f = ws.cell(row=row, column=6).value   # Column F
    col_l = ws.cell(row=row, column=12).value  # Column L
    col_p = ws.cell(row=row, column=16).value  # Column P
    
    # Process all three columns simultaneously
    if col_f == 'Yes' or col_l == 'NA':
        print(f"Row {row}: F={col_f}, L={col_l}")
```

**Why**: Preserves original formatting and enables precise condition checking across scattered columns without intermediate mappings.

## Pattern 2: Backward Search for Nearest Prior Value

When finding "the most recent X before Y", iterate backwards from the trigger row:

```python
# Find first row with trigger condition
first_trigger_row = None
for row in range(2, ws.max_row + 1):
    if ws.cell(row=row, column=6).value == 'Yes':
        first_trigger_row = row
        break

# Search backwards for nearest prior value
nearest_value_row = None
if first_trigger_row:
    for row in range(first_trigger_row - 1, 1, -1):
        if ws.cell(row=row, column=6).value == 100:
            nearest_value_row = row
            break
```

**Why**: Guarantees finding the temporally closest match rather than any earlier match.

## Pattern 3: Cross-Sheet Reference Matching

When inserting data based on matches across sheets:

```python
wb = load_workbook('file.xlsx')
ws_a = wb['SheetA']
ws_b = wb['SheetB']

# Step 1: Read lookup value from source
lookup_value = ws_a.cell(row=1, column=6).value  # F1

# Step 2: Scan target column for matching rows
for target_row in range(2, ws_b.max_row + 1):
    target_value = ws_b.cell(row=target_row, column=3).value  # Column C
    
    if target_value == lookup_value:
        # Step 3: Write result to same row in target column
        ws_b.cell(row=target_row, column=6).value = 'Match Found'
```

**Why**: Two-step process (read source → find match in target → write) ensures accurate data consolidation across sheets.

## Checklist for Complex Operations

- [ ] Use `cell(row, column)` for non-contiguous columns
- [ ] Iterate backwards when temporal proximity matters
- [ ] Read source value before scanning target column
- [ ] Verify column indices (Excel is 1-indexed)
- [ ] Test on small dataset before applying broadly
