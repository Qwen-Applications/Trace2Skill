---
name: data-expansion-patterns
description: Patterns for expanding rows with optional dimensions
---

# Data Expansion with Optional Dimensions

When expanding source rows into multiple output rows based on ID columns, handle missing dimensions gracefully.

## Problem
Some ID columns may be empty/null, causing naive cartesian expansion to return zero results.

## Solution Pattern

```python
def expand_row_with_optional_dims(row, specialties=None, contexts=None):
    """
    Expand a row considering optional dimension columns.
    Handles cases where specialties or contexts may be missing.
    """
    # Check which dimensions actually exist for this row
    has_specialties = specialties is not None and len(specialties) > 0
    has_contexts = contexts is not None and len(contexts) > 0
    
    # Generate appropriate output based on available dimensions
    if has_specialties and has_contexts:
        # Full cartesian product
        for spec in specialties:
            for ctx in contexts:
                yield {...row, 'specialtyId': spec, 'contextId': ctx}
    elif has_specialties:
        # Single dimension: specialties only
        for spec in specialties:
            yield {...row, 'specialtyId': spec}
    elif has_contexts:
        # Single dimension: contexts only
        for ctx in contexts:
            yield {...row, 'contextId': ctx}
    else:
        # No dimensions: keep original row
        yield row
```

## Key Principles

1. **Validate each dimension independently** - Don't assume all IDs must exist
2. **Generate partial outputs** - Valid data with fewer dimensions is better than no output
3. **Test edge cases** - Verify behavior when 0, 1, or N dimensions exist

## Common Pitfalls to Avoid

- ❌ Requiring all ID columns to be non-empty before generating output
- ❌ Returning empty list when some dimensions are missing
- ❌ Using strict AND logic instead of OR logic for dimension existence

## Success Memory
*This pattern has been validated in practice: when expand_row() returned empty due to missing specialtyId, checking if either specialties or contexts existed and creating appropriate rows (single-dimensional or full cartesian) produced correct outputs.*