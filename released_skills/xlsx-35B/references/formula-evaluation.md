# Formula Evaluation

## VLOOKUP with Wildcard Patterns

### Pattern Recognition
```
VLOOKUP(D & "*", range, col, 0)
```
- Finds first match where lookup value CONTAINS the search string
- Case-insensitive substring match
- Returns first occurrence, not all matches

## Computed Values vs Stored Formulas

### When to Compute in Python
- Tasks asking for "computed results" or "final values"
- Extracted/cleaned text data
- Aggregated statistics (sum, average, count)

### When to Store Formulas
- Tasks saying "build a formula" or "create calculation"
- Dynamic recalculation requirements
- User-editable cells where users expect Excel to calculate

## Implementation Examples

### Create Formula Cell
```python
cell.value = '=SUM(A2:A10)'
```

### Create Computed Value
```python
cell.value = sum(range_data)  # Direct numeric result
```

## Common Mistakes
- Copying formula text verbatim without evaluating
- Assuming formulas are already calculated when loaded
- Mixing formula and value types in same column