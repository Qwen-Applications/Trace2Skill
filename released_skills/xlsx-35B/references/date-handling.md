# Date Handling

## Date Format Patterns

### Common Formats to Detect
- MM/DD/YYYY (US standard)
- DD/MM/YYYY (European standard)
- YYYY/MM/DD (ISO standard)
- MM-DD-YYYY, DD-MM-YYYY (with separators)

## Safe Parsing Code

```python
from datetime import datetime

def detect_and_parse_date(date_str):
    formats = ['%m/%d/%Y', '%d/%m/%Y', '%Y/%m/%d']
    for fmt in formats:
        try:
            return datetime.strptime(date_str.strip(), fmt)
        except ValueError:
            continue
    raise ValueError(f"Cannot parse date: {date_str}")
```

## Validation Checklist
- [ ] Check multiple sample values before assuming format
- [ ] Look for regional indicators in filename/sheet name
- [ ] Verify no mixed formats within same column
- [ ] Handle empty/null values explicitly
- [ ] Consider timezone if relevant