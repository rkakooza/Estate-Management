from django import template

register = template.Library()

@register.filter
def ugx(value):
    """
    Format a number as UGX with commas.
    Example: 300000 → UGX 300,000
    """
    try:
        value = float(value)
        return f"UGX {value:,.0f}"
    except:
        return value