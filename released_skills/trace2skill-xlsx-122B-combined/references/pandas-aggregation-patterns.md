---
purpose: "Guidance for pandas data aggregation tasks requiring correct row ordering and proper handling of zeros vs nulls"
---

# Pandas Aggregation Patterns

## Preserving Insertion Order in GroupBy Operations

Pandas `groupby()` sorts groups alphabetically by default, which often produces incorrect output order for spreadsheet tasks.

### ❌ WRONG - Alphabetical Sorting (Default Behavior)
```python
# This sorts TY/OR groups alphabetically (APPLE before BANANA)
df.groupby(['TY', 'OR']).agg({'SALE': 'sum'})
```

### ✅ CORRECT - Preserve First-Appearance Order
```python
# Method 1: Use observed=True with sort=False
df.groupby(['TY', 'OR'], sort=False).agg({'SALE': 'sum'})

# Method 2: Track insertion order explicitly
seen_keys = []
for idx, row in df.iterrows():
    key = (row['TY'], row['OR'])
    if key not in seen_keys:
        seen_keys.append(key)
# Process in seen_keys order
```

### When to Apply
- Always verify expected output order before implementing groupby
- Trace through source sheets sequentially to identify intended order pattern
- If gold output shows rows ordered by first encounter, use `sort=False`

## Distinguish Zeros from Missing Values

When applying formatting rules like "show hyphen for empty cells", distinguish between:

| Value Type | Source | Should Show |
|------------|--------|-------------|
| NaN/Null | Truly missing data | Hyphen "-" |
| Zero (0) | Valid aggregation result | Numeric 0 |

### ❌ WRONG - Replace All Zeros
```python
# Incorrect: Converts legitimate zero sums to hyphens
df = df.fillna('-')  # This also converts 0 to '-'
```

### ✅ CORRECT - Only Replace Nulls
```python
# Correct: Only replace actual NaN values
df = df.where(pd.notna(df), '-')  # Keeps zeros intact

# Or explicitly:
df['SALE'] = df['SALE'].apply(lambda x: '-' if pd.isna(x) else x)
```

### Validation Checklist
- [ ] Check if zero appears in expected output for same (key) combinations
- [ ] Verify zeros come from multiple non-zero inputs that cancel out
- [ ] Confirm hyphens only appear where source had no contributing data

## Row Ordering Verification

Before finalizing output, validate row sequence matches requirements:

```python
# Compare actual vs expected order
def verify_row_order(actual_df, expected_df):
    actual_keys = list(zip(actual_df['TY'], actual_df['OR']))
    expected_keys = list(zip(expected_df['TY'], expected_df['OR']))
    return actual_keys == expected_keys

# If mismatch, trace source sequence
print("First appearances in source:")
for sheet_name in ['STA', 'RPA', 'SR', 'SS']:
    sheet_data = pd.read_excel('source.xlsx', sheet_name=sheet_name)
    print(f"{sheet_name}: {list(sheet_data[['TY', 'OR']].drop_duplicates().values)}")
```

## Common Aggregation Template

```python
import pandas as pd
import numpy as np

# Read all relevant sheets
sheets = {}
for name in ['STA', 'RPA', 'SR', 'SS']:
    sheets[name] = pd.read_excel('source.xlsx', sheet_name=name)

# Concatenate maintaining sheet order
df = pd.concat(sheets.values(), ignore_index=True)

# Aggregate preserving insertion order
group_cols = ['TY', 'OR']
result = df.groupby(group_cols, sort=False).agg({
    'SALE': 'sum',
    'RET': 'sum'
}).reset_index()

# Only convert NaN to hyphen, keep zeros
for col in ['SALE', 'RET']:
    result[col] = result[col].where(pd.notna(result[col]), '-')

# Verify ordering before saving
print("Row order check passed:", verify_row_order(result, expected_df))
```