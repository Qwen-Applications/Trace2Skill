---
name: formula-patterns
description: Common Excel formula patterns and best practices
---

# Formula Patterns Reference

## Case-Insensitive Pattern Matching

Use SEARCH with ISNUMBER for case-insensitive substring matching:

```excel
=ISNUMBER(SEARCH("text", A1))
```

Returns TRUE if "text" appears in A1, regardless of case.

## OR Logic with SUMIFS

When matching against multiple possible values in one criteria field, sum separate SUMIFS calls instead of using array formulas.

### Pattern
```
=IF(A1<>"",SUMIFS(sum_range,criteria_range,A1),0)+IF(A2<>"",SUMIFS(sum_range,criteria_range,A2),0)+...
```

### Example: Multiple Region Selection
If regions can be selected in C11, C12, or C13:
```excel
=IF(C11<>"",SUMIFS(Revenue,Region,C11),0)+IF(C12<>"",SUMIFS(Revenue,Region,C12),0)+IF(C13<>"",SUMIFS(Revenue,Region,C13),0)
```

**Why this works:**
- Each SUMIFS handles one region value
- IF guards prevent errors from blank selection slots
- Avoids double-counting when fewer than max selections are made
- Simpler and more maintainable than array formulas

## Cross-Sheet References

Format: `SheetName!CellReference` or `'Sheet Name'!CellReference` (with quotes if sheet name has spaces)

Example: `=SUM(Sheet1!A1:A10)`

## Common Error Prevention

| Error | Cause | Prevention |
|-------|-------|------------|
| #REF! | Deleted/moved cells | Verify references after inserting/deleting rows |
| #DIV/0! | Zero denominator | Wrap with IFERROR or check denominator first |
| #VALUE! | Wrong data type | Ensure ranges contain numbers, not text |
| #NAME? | Invalid function name | Check spelling of Excel functions |

## Formula Construction Checklist

1. Verify source data exists in referenced cells
2. Confirm column/row indices match actual spreadsheet layout
3. Test with sample data before applying broadly
4. Run recalc.py after creating/modifying formulas
5. Check error_summary output for any issues
