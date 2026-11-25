"""Human labels for presentation; stored identifiers and units stay unchanged."""
import re


def goal_labels(goals):
    return {goal['id']: '目标'+str(index) for index, goal in enumerate(goals, 1)}


def goal_name(goal):
    return goal['objective'] if goal.get('kind')=='qualitative' else goal['quantity']


def display_unit(value):
    text=str(value)
    if '$' in text or '\\' in text:return text
    text=re.sub(r'(?<![A-Za-z])degC(?![A-Za-z])','°C',text,flags=re.I)
    text=re.sub(r'(?<![A-Za-z])ohm(?![A-Za-z])','Ω',text,flags=re.I)
    superscript=str.maketrans('0123456789-+','⁰¹²³⁴⁵⁶⁷⁸⁹⁻⁺')
    text=re.sub(r'(?:\*\*|\^)\s*\(?([+-]?\d+)\)?',lambda m:m.group(1).translate(superscript),text)
    return re.sub(r'(?<!\*)\*(?!\*)','·',text)
