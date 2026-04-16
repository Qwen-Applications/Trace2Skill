---
name: advanced-formula-patterns
description: Specialized Excel formula patterns for complex operations
---

# Advanced Formula Patterns

## Counting Unique Values Dynamically

Use the combination of `COUNTIF(range, value)=1` and `SUMPRODUCT(1/COUNTIF(range, range&""))` to count distinct items up to a current position.

### Pattern: Assign Sequential IDs to First Occurrences

When you need to assign sequential IDs to unique products appearing in order:

```python
from openpyxl import Workbook

wb = Workbook()
sheet = wb.active

# Column A: Product names (may have duplicates)
sheet['A2'] = 'Apple'
sheet['A3'] = 'Banana'
sheet['A4'] = 'Apple'  # Duplicate
sheet['A5'] = 'Cherry'
sheet['A6'] = 'Banana'  # Duplicate

# Column B: Sequential ID (1 for first occurrence, same ID for repeats)
for row in range(2, 7):
    current_cell = f'A{row}'
    end_range = f'$A$2:{current_cell}'
    
    # If this is the first time we see this product, assign new ID
    # Otherwise, look up the original ID from first occurrence
    formula = f'={end_range}&""'  # Check if cell has value
    
    # More sophisticated: COUNTIF to detect first occurrence
    sheet[f'B{row}'] = f'=IF(COUNTIF($A$2:{current_cell},{current_cell})=1, SUMPRODUCT(1/COUNTIF($A$2:{current_cell},$A$2:{current_cell}&"")), VLOOKUP({current_cell}, $A$2:B{row-1}, 2, FALSE))'

wb.save('unique_ids.xlsx')
```

### How It Works

1. **COUNTIF($A$2:A{n}, A{n})=1** - Checks if current value appears exactly once in cumulative range (first occurrence)
2. **SUMPRODUCT(1/COUNTIF($A$2:A{n}, $A$2:A{n}&""))** - Counts distinct values so far, including current
3. **VLOOKUP** - For repeats, retrieves the ID assigned at first occurrence

### When to Use This Pattern
- Creating unique identifiers for duplicate entries
- Ranking items by first appearance
- Tracking distinct counts in running totals

## Progressive Range Expansion

For formulas where the range grows with each row:

```python
for i in range(2, 100):
    current_row = f'A{i}'
    sheet[f'B{i}'] = f'=SUM($A$2:{current_row})'  # Expanding sum
    sheet[f'C{i}'] = f'=AVERAGE($A$2:{current_row})'  # Running average
```

Each row calculates over an increasingly larger range from the start point.

## Row-Level Self-Reference

When each row needs to reference its own position:

```python
for i in range(2, 50):
    cell_ref = f'D{i}'
    sheet[cell_ref] = f'={cell_ref}*1.1'  # Each row multiplies its own value
```

## Common Error Prevention

| Issue | Solution |
|-------|----------|
| Off-by-one errors | Remember Excel is 1-indexed; DataFrame row 0 = Excel row 1 |
| Invalid ranges | Always validate start < end in range definitions |
| Circular references | Ensure formula doesn't reference itself directly or indirectly |
| Empty cells | Use IF(ISBLANK(...), "", ...) to handle nulls gracefully
