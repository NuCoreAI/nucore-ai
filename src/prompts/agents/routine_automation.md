<<nucore_common_rules>>

────────────────────────────────
# COMPARISON OPERATOR TOKENS 
- Used for evaluating Change of State Subexpressions (COS)  
- Is exactly one of `>`, `>=`, `<`, `<=`, `==`, `!=`  
Example
- {"comp":">"}
- {"comp":"<="}
- {"comp":"=="}

────────────────────────────────
# EQUALITY OPERATOR TOKENS
- Used for evaluating Change of Control Subexpressions (COC)
- Is exactly one of:  `is` and `is not`
Example:
- {"eq":"is"}
- {"eq":"isnot"}

────────────────────────────────
# LOGICAL OPERATOR TOKENS 
- Used for boolean logic and grouping
- Is exactly one of: `and`, `or`, `(`, `)`
Example: 
- {"logic":"and"}
- {"logic":"or"}
Valid grouping tokens (use exactly as shown):
- {"logic":"("}
- {"logic":")"}


