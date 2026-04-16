---
title: Spreadsheet Formula Construction
description: Comprehensive formula scope matching and validation
---
# Formula Construction Guide

Read this when writing formulas to cells or creating calculated columns.

## Critical Rules

### Match Formula Scope to Analysis
**DO NOT** write a formula that covers fewer dimensions than your analysis requires.

If your code searches rows 2 through N to find matches, your formula must also span those rows:

| ❌ Wrong | ✅ Right |
|----------|----------|
| `=SUMPRODUCT(..., O2:AZ2)` (single row) | `=SUMPRODUCT(..., O$2:AZ$100)` (multiple rows) |
| Searches 5 rows, formula checks 1 row | Formula range = analysis range |

**Checklist before writing formula:**
1. Did my analysis scan multiple rows? → Formula needs row range
2. Did my analysis scan multiple columns? → Formula needs column range
3. Are both dimensions reflected in every range reference?

### Avoid Volatile Functions
**DO NOT** use these functions inside SUMPRODUCT, array formulas, or large calculations unless absolutely necessary:

| Volatile Function | Problem | Alternative |
|-------------------|---------|-------------|
| `OFFSET()` | Recalculates on any change; inconsistent in arrays | `INDEX()` |
| `INDIRECT()` | Recalculates on any change | Direct references or `INDEX()` |
| `NOW()`, `TODAY()` | Recalculate every time file opens | Static values or separate timestamp cell |

**Example - Replace OFFSET with INDEX:**
```python
# ❌ Volatile (problematic)
=N(OFFSET(O2:AZ2,0,1))

# ✅ Stable (preferred)
=INDEX(O2:AZ2, ROW(), COLUMN()+1)
```

### Validate Column References Before Writing
When building formulas programmatically:

1. **Test conversion logic separately** - Verify `col_to_letter()` produces correct output for edge cases
2. **Print complete formula string** before assignment to catch truncation or interpolation errors
3. **Verify start/end columns** match your intended range

## Recommended Patterns

### Multi-row SUMPRODUCT Search
```python
# If searching part numbers across rows 2-100:
start_row, end_row = 2, 100
start_col, end_col = 15, 52  # O to AZ
formula = f"=SUMPRODUCT((MOD(COLUMN({chr(15)+str(start_row)}:{chr(52)+str(end_row)}),2)=1)*({chr(15)+str(start_row)}:{chr(52)+str(end_row)}={cell_ref}),N(INDEX(...)))"
```

## Quick Reference
- ✅ Use `INDEX()` for offset-like behavior
- ✅ Validate formula strings before file write
- ✅ Ensure formula spans same rows/columns as analysis
- ❌ Don't use `OFFSET()` in array formulas
- ❌ Don't skip testing column conversion logic