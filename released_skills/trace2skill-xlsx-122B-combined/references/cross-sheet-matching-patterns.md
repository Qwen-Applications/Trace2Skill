---
name: cross-sheet-matching-patterns
description: Patterns for matching and joining data across Excel sheets
---

# Cross-Sheet Data Matching Patterns

## When Single Keys Are Ambiguous

Use composite keys when identifiers repeat across different contexts (dates, categories, etc.).

### Example: Race Results Matching
```
Bad: Match only on Race# → May match wrong day's races
Good: Match on (Meet, Date, Race#, Category) → Unique combination
```

**Implementation:**
```python
# Create composite key from multiple columns
df['match_key'] = df['Meet'].astype(str) + '|' + \
                  df['Date'].astype(str) + '|' + \
                  df['Race#'].astype(str)

# Merge on composite key
result = sheet1.merge(sheet2, on='match_key', how='left')
```

## Pre-Build Lookup Indexes for Efficiency

When looking up values repeatedly, build an inverted index once rather than searching per row.

### Pattern: Position Mapping
```
Bad: For each Sheet1 row, search Sheet2's J-M columns (O(n*m))
Good: Build positions_dict once, then O(1) lookups
```

**Implementation:**
```python
# Build inverted index once
positions_dict = {}
for pos, tab_num in enumerate(range(start_col, end_col+1), start=1):
    if sheet.cell(row=r, column=tab_num).value:
        positions_dict[sheet.cell(row=r, column=tab_num).value] = pos

# Fast lookups
rank = positions_dict.get(tab_number, '')  # Empty string if not found
```

## Accept Empty Results Appropriately

Do not fill cells with placeholder values when source data has no match.

### Guidelines
- Leave cells blank when data legitimately doesn't exist
- Do not infer or estimate values for unmatched rows
- Document why certain cells remain empty if this might confuse users

### Example: Top-N Results
```
Scenario: Sheet2 has top 4 finishers in columns J-M
Result: Horses outside top 4 correctly have empty Rank field
Avoid: Filling with 'N/A' or default values that imply false data
```

## Summary Checklist

- [ ] Check if single identifier has duplicates before matching
- [ ] Build composite keys when necessary for uniqueness
- [ ] Pre-build lookup dictionaries for repeated access patterns
- [ ] Leave cells empty for legitimate non-matches
- [ ] Verify matched data makes logical sense (e.g., correct dates aligned)
