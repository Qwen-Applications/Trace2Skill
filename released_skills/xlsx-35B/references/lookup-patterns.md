# Lookup Patterns

## Same-Row vs Cross-Row Lookups

### Same-Row Pattern
- Search parameters and target data exist in the same row
- Simple direct comparison sufficient
- Example: Find price in same row as product ID

### Cross-Row Pattern
- Search parameters exist outside target row
- Must iterate through entire dataset to find matches
- Critical: DO NOT assume `row[i]` contains both search params AND target value
- When parameters include None values but expected results exist elsewhere, suspect cross-row pattern

## Efficient Iteration Strategies

```python
# Cross-row lookup example
for row in ws.iter_rows(min_row=2, values_only=True):
    if row[0] == search_param:  # Search column
        found_value = row[target_col_index]
        break
```

## Common Mistakes
- Assuming data alignment without verification
- Stopping at first row without checking entire dataset
- Not handling cases where no match exists