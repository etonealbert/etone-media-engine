"""
FSM state definitions for the /review asset browser.
"""

from aiogram.fsm.state import State, StatesGroup


class ReviewStates(StatesGroup):
    """States for the asset review browser."""

    select_category = State()  # Pick which category to browse
    select_item = State()  # Pick folder (subfolder cats) or file (flat cats)
