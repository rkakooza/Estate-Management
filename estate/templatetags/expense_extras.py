import re
from django import template

register = template.Library()

@register.filter
def clean_salary_label(description: str) -> str:
    """
    Removes internal employee markers like [Emp #5]
    from salary descriptions for UI display.
    """
    if not description:
        return description
    return re.sub(r"\s*\[Emp\s+#\d+\]", "", description)