---
name: Formula Best Practices
description: Best practices for writing and verifying Excel formulas programmatically
---

# Formula Writing & Verification Guide

## Verifying Formula Results

**Problem**: openpyxl stores formula strings but cannot evaluate them. Simply confirming "formula exists" ≠ "formula works correctly."

**Solutions**:

1. **Write calculated values directly** (when possible):
```python
# Instead of:
ws['C1'] = '=A1+B1'

# Use pre-calculated values:
ws['C1'] = ws['A1'].value + ws['B1'].value
```

2. **Read calculated values** (if workbook was saved with Excel):
```python
from openpyxl import load_workbook
wb = load_workbook('output.xlsx', data_only=True)
value = wb['Sheet1']['F4'].value
```

3. **Use pandas for numeric verification**:
```python
import pandas as pd
df = pd.read_excel('output.xlsx')
print(df.loc[row_idx, 'column_name'])
```

4. **Manual spot-check critical cells**: Verify 2-3 key output cells match expected values.

## Excel Version Compatibility

| Function | Minimum Version | Recommendation |
|----------|----------------|----------------|
| FILTER() | Excel 365/2021 | Use with fallback or note requirement |
| XLOOKUP() | Excel 365/2021 | Prefer INDEX/MATCH for broader compatibility |
| LAMBDA() | Excel 365 | Avoid unless explicitly required |
| UNIQUE() | Excel 365/2021 | Use advanced filter pattern as fallback |
| INDEX/MATCH | All versions | **Safe default choice** |
| AGGREGATE | Excel 2010+ | Good legacy-compatible alternative |

## Range Validation Checklist

Before implementing formulas:
- [ ] Read last populated row/column from actual file
- [ ] Compare against task-specified range
- [ ] Adjust formula ranges if mismatch > 2 rows
- [ ] Flag discrepancies to user if uncertainty remains

## Common Pitfalls

❌ **Don't assume task-specified ranges match actual content**
- Task says B3:D22, but data might end at B3:D15
- Check `max_row` before hardcoding ranges

❌ **Don't rely on openpyxl to verify formula correctness**
- Reading back formula cells returns text, not values
- Use external evaluation or write pre-calculated values

❌ **Don't use dynamic array functions without compatibility notes**
- FILTER/XLOOKUP will show #NAME? in Excel 2019 and earlier
- Document version requirements or provide fallbacks