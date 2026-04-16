---
title: Dynamic Sheet References Pattern
---

# Dynamic Cross-Sheet References with INDIRECT

When sheet names vary based on lookup values (e.g., student names, department codes, scenario labels), use the `INDIRECT` function to construct dynamic reference.

## Problem Scenario
You need to pull data from different worksheets where:
- Sheet names follow a pattern matching your row data
- Writing unique formulas per row would be tedious or impossible
- Example: Sheet names "Student_A", "Student_B", etc., with lookup values in column A

## Solution Pattern

### Basic Syntax
```excel
=INDIRECT(A1&"!B2")
```

This concatenates:
- `A1` = text value containing sheet name (or part of it)
- `&"!B2"` = the cell reference string
- Result = live reference to B2 on the sheet named in A1

### Complete Examples

#### Direct Sheet Name Reference
If A1 contains "Q1_Data":
```excel
=INDIRECT(A1&"!C10")  // Returns value from Q1_Data!C10
```

#### Constructed Sheet Name
If you need to build the sheet name from multiple cells:
```excel
=INDIRECT("Sheet_"&A1&"_"&B1&"!D5")  // Sheet_Sales_Q1!D5
```

#### With Named Ranges
```excel
=SUM(INDIRECT(A1&"!DataRange"))
```

## Implementation in openpyxl

```python
from openpyxl import Workbook

wb = Workbook()
sheet = wb.active

# Cell A1 contains the sheet name to reference
sheet['A1'] = 'Q1_Data'

# Cell B1 uses INDIRECT to reference C10 from that sheet
sheet['B1'] = '=INDIRECT(A1&"!C10")'

wb.save('dynamic_refs.xlsx')
```

## Important Considerations

### When to Use
- ✅ Sheet names follow predictable patterns based on row data
- ✅ You need to avoid writing 100+ nearly-identical formulas
- ✅ Referenced sheets are guaranteed to exist

### When NOT to Use
- ❌ Sheet names don't match any lookup pattern
- ❌ Referenced sheets might not exist (will return #REF!)
- ❌ Performance-critical calculations (INDIRECT is volatile)

### Error Handling
If the constructed sheet name doesn't exist, INDIRECT returns `#REF!`. Consider wrapping with IFERROR:
```excel
=IFERROR(INDIRECT(A1&"!B2"), "Sheet not found")
```

## Validation Checklist
- [ ] Verify all referenced sheet names actually exist in the workbook
- [ ] Test with sample data before applying across many rows
- [ ] Run recalc.py after creating to validate all INDIRECT formulas resolve correctly
- [ ] Check for #REF! errors in error_summary output

## Related Patterns
- See [Formula Construction Rules](../SKILL.md#formula-construction-rules) for general formula best practices
- See [Formula Verification Checklist](../SKILL.md#formula-verification-checklist) for validation steps