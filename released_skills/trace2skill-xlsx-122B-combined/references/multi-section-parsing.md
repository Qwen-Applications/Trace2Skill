---
name: multi-section-parsing
description: Handling spreadsheets with multiple data sections separated by marker rows
---

# Multi-Section Spreadsheet Parsing

## Problem
Some Excel sheets contain multiple distinct data sections separated by label/marker rows (e.g., "DATA", "OPERATION", "SUMMARY"). Section lengths vary dynamically.

## Solution Pattern

### Step 1: Identify Section Markers
Scan column values to find marker text, not row numbers:

```python
import pandas as pd

df = pd.read_excel('file.xlsx')

# Find rows containing marker values in specific column
marker_col = 2  # Column C (0-indexed)
markers = df[marker_col].astype(str).isin(['DATA', 'OPERATION'])
marker_indices = df[markers].index.tolist()
```

### Step 2: Extract Each Section
Use marker indices to define section boundaries:

```python
sections = {}
for i, start_idx in enumerate(marker_indices):
    end_idx = marker_indices[i + 1] if i + 1 < len(marker_indices) else len(df)
    section_name = df.iloc[start_idx][marker_col]
    # Data rows typically follow marker row
    sections[section_name] = df.iloc[start_idx + 1:end_idx]
```

### Step 3: Validate Section Content
Check for expected data patterns within each section:

```python
for name, sec_df in sections.items():
    # Verify numeric values exist where expected
    assert sec_df['S.N'].apply(lambda x: isinstance(x, (int, float))).all()
```

## Common Pitfalls

| Mistake | Fix |
|---------|-----|
| Assuming fixed row numbers | Always scan for markers first |
| Including marker row in data | Start section data after marker row |
| Missing final section boundary | Handle last section until end of sheet |
| Case sensitivity issues | Use `.str.upper()` before comparison |

## Example: Variable-Length Ranges Sheet

```python
# Original approach (fragile)
data_start_row = 5  # Breaks when ranges are added

# Robust approach
marker_rows = df[df[2].isin(['DATA', 'OPERATION'])].index
for i, start in enumerate(marker_rows):
    end = marker_rows[i+1] if i+1 < len(marker_rows) else len(df)
    section_data = df.iloc[start+1:end]
    # Process section_data dynamically
```

This handles new ranges being added without breaking.