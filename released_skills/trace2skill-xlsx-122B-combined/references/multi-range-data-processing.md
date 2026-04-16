---
name: multi-range-data-processing
description: Patterns for processing spreadsheets with multiple independent data blocks
---

# Multi-Range Data Processing Patterns

Use these patterns when working with spreadsheets containing multiple independent data tables separated by blank rows.

## When to Use This Reference
Read this when working with spreadsheets that contain:
- Multiple distinct data tables separated by blank rows
- Independent data blocks that need separate processing
- Mixed alphanumeric data requiring sorting

## Pattern 1: Identifying Range Boundaries

When data is split into multiple sections with blank rows between them:

```python
import pandas as pd
from openpyxl import load_workbook

wb = load_workbook('data.xlsx')
sheet = wb.active

# Detect range boundaries by checking if columns A or B contain data
ranges = []
current_start = None

for row_idx in range(1, sheet.max_row + 1):
    col_a_val = sheet.cell(row=row_idx, column=1).value
    col_b_val = sheet.cell(row=row_idx, column=2).value
    
    # Start new range if both A and B are empty (blank separator)
    if col_a_val is None and col_b_val is None:
        if current_start is not None:
            ranges.append((current_start, row_idx - 1))
            current_start = None
    elif current_start is None:
        current_start = row_idx

# Don't forget the last range
if current_start is not None:
    ranges.append((current_start, sheet.max_row))
```

**Key Principle**: Only process cells where key columns contain data; leave blank separator rows untouched.

## Pattern 2: Preserving Separator Rows

Maintain blank separator rows unchanged when transforming data:

```python
# Process each range independently, leaving blank rows untouched
for start_row, end_row in ranges:
    # Extract data within this range only
    data_rows = []
    for row in range(start_row, end_row + 1):
        row_data = [sheet.cell(row=r, column=c).value for c in range(1, num_cols + 1)]
        data_rows.append(row_data)
    
    # Transform/sort data_rows
    sorted_data = transform(data_rows)
    
    # Write back starting at original position
    for i, row_data in enumerate(sorted_data):
        for j, val in enumerate(row_data):
            sheet.cell(row=start_row + i, column=j + 1).value = val

# Blank separator rows remain unchanged automatically
wb.save('output.xlsx')
```

## Pattern 3: Header Anchoring During Sort

Always keep header rows fixed at the top of each range:

```python
def sort_range_preserving_header(df, range_start, range_end, sort_column):
    """
    Sort a data range while keeping its header row fixed at the top.
    """
    # Extract header (first row of range)
    header_row = df.iloc[range_start:range_start+1]
    
    # Extract data rows (everything after header in range)
    data_rows = df.iloc[range_start+1:range_end+1].copy()
    
    # Sort only data rows
    sorted_data = data_rows.sort_values(by=sort_column)
    
    # Reconstruct range with header + sorted data
    result = pd.concat([header_row, sorted_data], ignore_index=True)
    
    return result
```

```python
for start_row, end_row in ranges:
    # Extract header row separately
    header_row = [sheet.cell(row=start_row, column=c).value for c in range(1, num_cols + 1)]
    
    # Extract data rows (excluding header)
    data_rows = []
    for row in range(start_row + 1, end_row + 1):
        row_data = [sheet.cell(row=r, column=c).value for c in range(1, num_cols + 1)]
        data_rows.append(row_data)
    
    # Sort only data rows, not the header
    sorted_data = sorted(data_rows, key=lambda x: str(x[0]))  # Example: sort by first column
    
    # Reconstruct: header first, then sorted data
    all_rows = [header_row] + sorted_data
    
    # Write back
    for i, row_data in enumerate(all_rows):
        for j, val in enumerate(row_data):
            sheet.cell(row=start_row + i, column=j + 1).value = val
```

**Why This Works**: Prevents headers from being mixed into sorted data, maintaining proper column labeling.

## Pattern 4: Alphanumeric Sorting Strategy

For columns containing mixed alphanumeric values (e.g., "A 37", "Z 12", "D 6"):

### ✅ CORRECT - String-Based Lexicographic Sorting
```python
# Standard string comparison works correctly for A-Z prefixes
sorted_data = sorted(data_rows, key=lambda x: str(x[0]))
# Result: "A 55" < "C 14" < "D 6" < "Z 12"
```

### ❌ WRONG - Numeric Extraction (unless explicitly required)
```python
# Avoid parsing embedded numbers unless specifically requested
# This adds unnecessary complexity and can fail on edge cases
sorted_data = sorted(data_rows, key=lambda x: int(re.search(r'\d+', str(x[0])).group()))
```

**Key Points**:
- Use `str(val)` in sort keys for lexicographic ordering
- Prefix letters will sort alphabetically (A before C before D)
- Only use numeric extraction when the requirement explicitly calls for it
- Test with sample data to verify expected sort order

## Verification Checklist

After processing multi-range data:

- [ ] Blank separator rows remain unchanged
- [ ] Each range retains its original header at the top
- [ ] Headers were not mixed into sorted data
- [ ] Sorting order matches expected output (verify first few entries)
- [ ] No data rows were lost or duplicated

## Common Pitfalls to Avoid

| Pitfall | Consequence | Solution |
|---------|-------------|----------|
| Treating entire sheet as one table | Headers get sorted with data | Identify and process each range independently |
| Including blank rows in sort operations | Empty rows appear in wrong positions | Exclude separator rows from sort ranges |
| Parsing embedded numbers unnecessarily | Complexity increases, edge cases multiply | Use string comparison for simple alphabetical sorts |
| Not verifying range boundaries | Data from adjacent ranges mixes | Test boundary detection on sample data first |