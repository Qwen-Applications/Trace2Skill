# Formula Patterns

## Reference Structure Guide

### Absolute vs Relative References
- `$B12` - Column locked, row relative (copy down preserves row)
- `B$12` - Row locked, column relative (copy across preserves column)
- `$B$12` - Fully locked reference (copies stay exactly the same)
- `B12` - Both relative (adjusts when copied)

### INDEX/MATCH Pattern
```
=INDEX(target_column, MATCH(lookup_value, lookup_column, 0))
```
- More flexible than VLOOKUP
- Works left-to-right and right-to-left
- Easier to maintain when columns shift

## Pre-Save Checklist
- [ ] Verify all formulas use correct cell references
- [ ] Check for circular dependencies
- [ ] Confirm formula evaluates to expected result type
- [ ] Test with sample data before full run

## Common Mistakes
- Writing formula string without `=` prefix
- Using hardcoded values instead of cell references
- Not accounting for header rows in range calculations