from django import template
from decimal import Decimal, InvalidOperation

register = template.Library()

@register.filter
def ugx(value):
    """
    Format a number as UGX with commas.
    Example: 300000 → UGX 300,000
    """
    try:
        amount = Decimal(str(value))
        if amount == amount.to_integral_value():
            return f"UGX {amount:,.0f}"
        return f"UGX {amount:,.2f}"
    except (InvalidOperation, TypeError, ValueError):
        return value
