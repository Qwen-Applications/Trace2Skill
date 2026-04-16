# Data Manipulation Patterns

## Testing Complex Transformations
- For complex transformations, consult this reference for tested implementation patterns
- Always validate transformations on sample data before full application
- Preserve original data until transformation verified

## Common Transformation Scenarios

### Text Extraction
- Use pandas str.extract() or regex for clean extraction
- Validate patterns against multiple samples
- Handle edge cases (empty strings, special characters)

### Numeric Calculations
- Verify arithmetic operations work correctly
- Handle division by zero explicitly
- Round appropriately for display

### Date Conversions
- Parse dates safely before manipulation
- Account for timezone differences
- Format output consistently

## Best Practices
- Write small test functions for each transformation
- Log intermediate results for debugging
- Keep transformations reversible where possible