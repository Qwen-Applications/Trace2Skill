# Valid Command Patterns for JSON Actions

## ✅ Working Patterns

### Simple One-Liner (Preferred)
```bash
python3 -c "from openpyxl import load_workbook; wb = load_workbook('data.xlsx'); print(wb.sheetnames)"
```

### With Single Quotes Inside (Use Double Outer Quotes)
```bash
python3 -c "wb['Sheet1']['A1'].value = 'Hello World'"
```

### Write Script to Temp File First (For Complex Logic)
```bash
echo 'from openpyxl import load_workbook' > /tmp/s.py && python3 /tmp/s.py
```

## ❌ Broken Patterns (Will Fail JSON Parsing)

### Heredoc Syntax (NEVER USE)
```bash
python3 << 'EOF'
import openpyxl
# This fails in JSON Actions
EOF
```

### Multi-Line Commands
```bash
python3 -c "
from openpyxl import load_workbook
wb = load_workbook('data.xlsx')
"  # Newline breaks JSON string
```

### Unescaped Nested Quotes
```bash
python3 -c "print('It\'s working')"  # Confusing quote nesting
```

## Debugging Checklist

1. Can you run this as a single line without newlines?
2. Are all quotes properly balanced?
3. Is there only one level of quote nesting?
4. If it fails 3 times, change the approach entirely